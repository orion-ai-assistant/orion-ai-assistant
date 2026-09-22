import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from orion.api.services import job_service
from orion.contracts.http import JobCreateRequest
from orion.contracts.settings import RuntimeSettings
from orion.kernel.chat_titles import initial_chat_title
from orion.kernel import router_models
from orion.worker.services import chat_titles, router


class TitleTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for module, name, result in [(chat_titles, "get_chat_db", {"name": "hello"})]:
            patcher = patch.object(module, name, AsyncMock(return_value=result))
            patcher.start()
            self.addCleanup(patcher.stop)


class TitleTests(TitleTestBase):
    def test_initial_title(self):
        self.assertEqual(initial_chat_title("  bir iki\n üç dört beş altı yedi"), "bir iki üç dört beş altı…")
        self.assertEqual(initial_chat_title(""), "Yeni sohbet")
        self.assertLessEqual(len(initial_chat_title("a" * 100)), 61)

    async def test_create_persists_same_title_before_queue(self):
        redis = AsyncMock()
        pipe = Mock()
        pipe.execute = AsyncMock()
        redis.pipeline = Mock(return_value=pipe)
        redis.exists.return_value = False
        request = JobCreateRequest(user_id="u", input={"text": "Bugün hava nasıl"})
        with patch.object(job_service, "get_runtime_settings", AsyncMock(return_value=RuntimeSettings())), patch.object(job_service, "upsert_chat", AsyncMock()) as persist:
            await job_service.create_job(redis, request)
        self.assertEqual(persist.call_args.kwargs["title"], "Bugün hava nasıl")
        metadata = [c.kwargs["mapping"] for c in pipe.hset.call_args_list if "name" in c.kwargs.get("mapping", {})]
        self.assertEqual(metadata[0]["name"], "Bugün hava nasıl")
        pipe.execute.assert_awaited_once()

    async def test_ai_disabled_failure_and_manual_rename(self):
        context = SimpleNamespace(request=SimpleNamespace(chat_id=None), prompt="hello", chat_id="c", channel="u", turn_id="t", redis=AsyncMock())
        context.redis.hget.return_value = "t"
        with patch.object(chat_titles, "llama_chat", AsyncMock(return_value="A title")) as model, patch.object(chat_titles, "replace_initial_chat_title", AsyncMock(return_value=False)) as persist:
            await chat_titles.generate_chat_title(context, RuntimeSettings())
            model.assert_not_awaited()
            await chat_titles.generate_chat_title(context, RuntimeSettings(ai_chat_titles_enabled=True))
            persist.assert_awaited_once_with("c", "hello", "A title")
            context.redis.delete.assert_not_awaited()
            model.side_effect = TimeoutError()
            with self.assertLogs(level="ERROR"):
                await chat_titles.generate_chat_title(context, RuntimeSettings(ai_chat_titles_enabled=True))
            self.assertEqual(persist.await_count, 1)

    async def test_ai_success_invalidates_cache_and_notifies(self):
        context = SimpleNamespace(request=SimpleNamespace(chat_id=None), prompt="hello", chat_id="c", channel="u", turn_id="t", redis=AsyncMock())
        context.redis.hget.return_value = "t"
        with patch.object(chat_titles, "llama_chat", AsyncMock(return_value="A title")), patch.object(chat_titles, "replace_initial_chat_title", AsyncMock(return_value=True)):
            await chat_titles.generate_chat_title(context, RuntimeSettings(ai_chat_titles_enabled=True))
        context.redis.delete.assert_awaited_once()
        context.redis.publish.assert_awaited_once()


class TitleRoutingTests(TitleTestBase):
    async def test_title_request_routes_selected_provider_and_publishes(self):
        for model, provider in [("local-chat", "local"), ("gemini-test", "gemini"), ("gpt-test", "openai")]:
            with self.subTest(model=model):
                response = AsyncMock()
                response.raise_for_status = Mock()
                response.json.return_value = {"choices": [{"message": {"content": "A title"}}]}
                response.__aenter__.return_value = response
                session = Mock()
                session.post.return_value = response
                context = SimpleNamespace(request=SimpleNamespace(chat_id=None), prompt="hello", chat_id="c", channel="u", turn_id="t", redis=AsyncMock())
                context.redis.hget.return_value = "t"
                settings = RuntimeSettings(ai_chat_titles_enabled=True, router_model_group=model)
                with patch.object(router, "get_session", AsyncMock(return_value=session)), patch.object(
                    router, "_router_urls", return_value=["http://test/chat"]
                ), patch.object(
                    router, "get_model_provider", AsyncMock(return_value=provider)
                ), patch.object(chat_titles, "replace_initial_chat_title", AsyncMock(return_value=True)):
                    await chat_titles.generate_chat_title(context, settings)
                request = session.post.call_args.kwargs
                self.assertEqual(request["headers"]["x-orion-provider"], provider)
                self.assertEqual(request["json"]["model"], model)
                self.assertFalse(request["json"]["stream"])
                context.redis.publish.assert_awaited_once()


class UpdatedTitleTests(TitleTestBase):
    def test_generic_title_falls_back_to_question(self):
        self.assertEqual(chat_titles.clean_title("Sohbet Başlığı", "En sevdiğin renk ne"), "En sevdiğin renk ne")

    async def test_followup_uses_previous_title_and_separate_model(self):
        context = SimpleNamespace(request=SimpleNamespace(chat_id="c"), prompt="Yeni soru", chat_id="c", channel="u", turn_id="t", redis=AsyncMock())
        context.redis.hget.return_value = "t"
        with patch.object(chat_titles, "llama_chat", AsyncMock(return_value="Yeni sorunun konusu")) as model, patch.object(chat_titles, "replace_initial_chat_title", AsyncMock(return_value=True)) as persist:
            await chat_titles.generate_chat_title(context, RuntimeSettings(ai_chat_titles_enabled=True, chat_title_model="local-chat", router_model_group="gemini-test"))
        messages, settings = model.call_args.args
        self.assertEqual(settings.router_model_group, "local-chat")
        self.assertIn('"previous_title": "hello"', messages[1]["content"])
        self.assertIn("Yeni soru", messages[1]["content"])
        persist.assert_awaited_once_with("c", "hello", "Yeni sorunun konusu")

    async def test_old_turn_does_not_rename(self):
        context = SimpleNamespace(prompt="hello", chat_id="c", channel="u", turn_id="old", redis=AsyncMock())
        context.redis.hget.return_value = "new"
        with patch.object(chat_titles, "llama_chat", AsyncMock(return_value="A title")), patch.object(chat_titles, "replace_initial_chat_title", AsyncMock()) as persist:
            await chat_titles.generate_chat_title(context, RuntimeSettings(ai_chat_titles_enabled=True))
        persist.assert_not_awaited()


class ModelSettingTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_is_validated_when_setting_is_saved(self):
        with patch.object(
            router_models, "get_model_provider", AsyncMock(return_value="gemini")
        ) as provider:
            await router_models.validate_model_updates(
                {"router_model_group": "gemini-test", "temperature": "0.3"}
            )
        provider.assert_awaited_once_with("gemini-test", "chat")
