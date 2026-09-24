import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from orion.contracts.settings import RuntimeSettings
from orion.kernel import config, registry


class AdminSettingConstraintTests(unittest.IsolatedAsyncioTestCase):
    def test_numeric_settings_expose_bounds_and_reject_invalid_values(self):
        properties = RuntimeSettings.model_json_schema()["properties"]
        numeric = {key: field for key, field in properties.items()
                   if field.get("type") in ("integer", "number")}
        self.assertTrue(numeric)
        for key, field in numeric.items():
            with self.subTest(key=key):
                self.assertIn("minimum", field)
                self.assertIn("maximum", field)

        with self.assertRaises(ValidationError):
            RuntimeSettings(first_token_delay_ms=-1)
        with self.assertRaises(ValidationError):
            RuntimeSettings(temperature=2.1)
        self.assertEqual(RuntimeSettings(temperature=0).temperature, 0)
        self.assertEqual(RuntimeSettings(temperature=2).temperature, 2)

    async def test_invalid_update_is_rejected_before_database_write(self):
        with patch.object(config, "upsert_setting_overrides", new_callable=AsyncMock) as upsert:
            with self.assertRaises(ValidationError):
                await config.update_runtime_settings(object(), {"first_token_delay_ms": "-1"}, "global")
            upsert.assert_not_awaited()

    def test_older_invalid_override_uses_safe_default(self):
        settings = config.build_runtime_settings({
            "first_token_delay_ms": "-1",
            "temperature": "2.5",
            "token_delay_ms": "80",
        })
        self.assertEqual(settings.first_token_delay_ms, 0)
        self.assertEqual(settings.temperature, 0.9)
        self.assertEqual(settings.token_delay_ms, 80)

    async def test_chat_rename_sends_datetime_to_postgres(self):
        connection = unittest.mock.Mock()
        connection.execute = AsyncMock()
        connection.close = AsyncMock()
        with (
            patch.object(registry, "_connect", new_callable=AsyncMock, return_value=connection),
            patch.object(registry, "_ensure_tables", new_callable=AsyncMock),
        ):
            await registry.rename_chat_db("chat-1", "Yeni ad", "2026-09-24T12:00:00+00:00", "alice")

        args = connection.execute.await_args.args
        self.assertIn("on conflict", args[0].lower())
        self.assertEqual(args[1:4], ("chat-1", "alice", "Yeni ad"))
        self.assertEqual(args[4].isoformat(), "2026-09-24T12:00:00+00:00")

    async def test_chat_touch_sends_datetime_to_postgres(self):
        connection = unittest.mock.Mock()
        connection.execute = AsyncMock()
        connection.close = AsyncMock()
        with (
            patch.object(registry, "_connect", new_callable=AsyncMock, return_value=connection),
            patch.object(registry, "_ensure_tables", new_callable=AsyncMock),
        ):
            await registry.touch_chat_db("chat-1", "2026-09-24T12:00:00+00:00")

        args = connection.execute.await_args.args
        self.assertEqual(args[1], "chat-1")
        self.assertEqual(args[2].isoformat(), "2026-09-24T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
