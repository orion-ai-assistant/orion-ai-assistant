import unittest
from unittest.mock import AsyncMock, Mock, patch

from orion.contracts.settings import RuntimeSettings
from orion.kernel import config
from orion.worker.services import router
from orion.api import routes


class ModelDefaultsTests(unittest.IsolatedAsyncioTestCase):
    def test_temperature_round_trip_and_explicit_zero(self):
        for value in (None, "", "default", "None", "null"):
            self.assertIsNone(RuntimeSettings(temperature=value).temperature)
        self.assertIsNone(config.build_runtime_settings({"temperature": config.serialize_setting(None)}).temperature)
        self.assertEqual(config.build_runtime_settings({"temperature": "0"}).temperature, 0)
        self.assertEqual(config.build_runtime_settings({"temperature": "0.7"}).temperature, 0.7)

    async def test_both_request_modes_omit_defaults_and_preserve_overrides(self):
        async def chunks():
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: [DONE]\n\n'

        for streaming in (False, True):
            for temperature, thinking in ((None, ""), (0, "low"), (0.8, "high")):
                with self.subTest(streaming=streaming, temperature=temperature):
                    response = AsyncMock()
                    response.status = 200
                    response.raise_for_status = Mock()
                    response.close = Mock()
                    response.content = Mock()
                    response.content.iter_any.return_value = chunks()
                    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
                    response.__aenter__.return_value = response
                    session = Mock()
                    session.post.return_value = response
                    settings = RuntimeSettings(temperature=temperature, thinking_level=thinking)
                    with patch.object(router, "get_session", AsyncMock(return_value=session)), patch.object(router, "_router_urls", return_value=["http://test/chat"]), patch.object(router, "_chat_headers", AsyncMock(return_value={})):
                        if streaming:
                            events = [event async for event in router.llama_stream_chat_typed([], settings)]
                            self.assertIn(("content", "ok"), events)
                        else:
                            self.assertEqual(await router.llama_chat([], settings), "ok")
                    payload = session.post.call_args.kwargs["json"]
                    if temperature is None:
                        self.assertNotIn("temperature", payload)
                        self.assertNotIn("thinking_level", payload)
                    else:
                        self.assertEqual(payload["temperature"], temperature)
                        self.assertEqual(payload["thinking_level"], thinking)

    async def test_nullable_temperature_keeps_admin_bounds(self):
        with patch.object(routes, "_check_admin_key"):
            constraints = await routes.get_settings_constraints(object())
        self.assertEqual(constraints["temperature"], {"type": "number", "minimum": 0, "maximum": 2, "nullable": True})

    def test_tts_model_voices_parsing_and_persistence(self):
        settings = RuntimeSettings(tts_model_voices='{"gemini-tts": "zephyr", "local-tts": "local-voice-1"}')
        self.assertEqual(settings.tts_model_voices, {"gemini-tts": "zephyr", "local-tts": "local-voice-1"})
        from_dict = RuntimeSettings(tts_model_voices={"gemini-tts": "zephyr"})
        self.assertEqual(from_dict.tts_model_voices, {"gemini-tts": "zephyr"})
        built = config.build_runtime_settings({"tts_model_voices": '{"gemini-tts": "zephyr"}'})
        self.assertEqual(built.tts_model_voices, {"gemini-tts": "zephyr"})
