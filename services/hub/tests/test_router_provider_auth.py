import unittest
from unittest.mock import AsyncMock, patch

from orion.contracts.settings import RuntimeSettings
from orion.kernel.router_models import ModelNotFoundError
from orion.worker.services import router
from orion.worker.services.job_processor import _format_router_fallback_message


class RouterProviderAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_provider_wins_over_model_name(self):
        for name in ("gpt-oss-120b", "openai/gpt-oss-120b", "google/gemini-test"):
            with self.subTest(name=name), patch.object(
                router, "get_model_provider", AsyncMock(return_value="openrouter")
            ) as lookup:
                headers = await router._chat_headers(RuntimeSettings(
                    router_model_group=name, router_api_key="sk-orion-test"
                ))
                self.assertEqual(headers["x-orion-provider"], "openrouter")
                self.assertEqual(headers["Authorization"], "Bearer sk-orion-test")
                self.assertEqual(headers["x-orion-api-key"], "sk-orion-test")
                lookup.assert_awaited_once_with(name, "chat")

    async def test_group_is_resolved_by_router_without_provider_hint(self):
        with patch.object(router, "get_model_provider", AsyncMock(return_value=None)):
            headers = await router._chat_headers(RuntimeSettings(router_model_group="gpt-group"))
        self.assertNotIn("x-orion-provider", headers)

    async def test_catalog_outage_does_not_guess_provider(self):
        with patch.object(router, "get_model_provider", AsyncMock(side_effect=RuntimeError("offline"))):
            headers = await router._chat_headers(RuntimeSettings(router_model_group="gpt-oss-120b"))
        self.assertNotIn("x-orion-provider", headers)

    async def test_unknown_model_is_not_silently_rerouted(self):
        with patch.object(router, "get_model_provider", AsyncMock(side_effect=ModelNotFoundError("missing"))):
            with self.assertRaises(ModelNotFoundError):
                await router._chat_headers(RuntimeSettings(router_model_group="gpt-missing"))

    async def test_local_default_still_works_without_catalog(self):
        with patch.object(router, "get_model_provider", AsyncMock()) as lookup:
            headers = await router._chat_headers(RuntimeSettings(router_model_group="local-chat"))
        self.assertEqual(headers["x-orion-provider"], "local")
        lookup.assert_not_awaited()

    def test_openrouter_401_explains_correct_credential_boundary(self):
        error = RuntimeError('OpenRouter HTTP Error 401: {"error":{"message":"Missing Authentication header"}}')
        message = _format_router_fallback_message(error, RuntimeSettings())
        self.assertIn("anahtar havuzunu", message)
        self.assertIn("Hub isteği Orion Router’a ulaştı", message)
        self.assertNotIn("yeniden başlat", message)
        self.assertNotIn("```", message)

    def test_other_errors_keep_original_detail(self):
        message = _format_router_fallback_message(RuntimeError("test provider error"), RuntimeSettings())
        self.assertIn("test provider error", message)
