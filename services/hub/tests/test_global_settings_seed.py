import unittest
from unittest.mock import AsyncMock, patch

from orion.contracts.constants import SETTINGS_DEFAULT_USER
from orion.kernel import config


class GlobalSettingsSeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_global_settings_gain_new_keys_without_replacing_overrides(self):
        existing = {
            "system_prompt": "Custom prompt",
            "temperature": "0.5",
        }
        redis = object()
        with (
            patch.object(config, "migrate_compact_setting", new_callable=AsyncMock, return_value=0),
            patch.object(config, "fetch_setting_overrides", new_callable=AsyncMock, return_value=existing),
            patch.object(config, "insert_missing_setting_overrides", new_callable=AsyncMock) as insert_missing,
            patch.object(config, "upsert_setting_overrides", new_callable=AsyncMock) as upsert,
            patch.object(config, "refresh_runtime_settings", new_callable=AsyncMock) as refresh,
        ):
            await config.seed_database_settings(redis)

        insert_missing.assert_awaited_once()
        user_id, missing = insert_missing.await_args.args
        self.assertEqual(user_id, SETTINGS_DEFAULT_USER)
        self.assertEqual(missing["ai_chat_titles_enabled"], "False")
        self.assertNotIn("system_prompt", missing)
        self.assertNotIn("temperature", missing)
        upsert.assert_not_awaited()
        refresh.assert_awaited_once_with(redis, SETTINGS_DEFAULT_USER)

    async def test_complete_global_settings_are_left_untouched(self):
        existing = {key: str(value) for key, value in config.settings.model_dump().items()}
        redis = object()
        with (
            patch.object(config, "migrate_compact_setting", new_callable=AsyncMock, return_value=0),
            patch.object(config, "fetch_setting_overrides", new_callable=AsyncMock, return_value=existing),
            patch.object(config, "insert_missing_setting_overrides", new_callable=AsyncMock) as insert_missing,
            patch.object(config, "upsert_setting_overrides", new_callable=AsyncMock) as upsert,
            patch.object(config, "refresh_runtime_settings", new_callable=AsyncMock) as refresh,
        ):
            await config.seed_database_settings(redis)

        insert_missing.assert_not_awaited()
        upsert.assert_not_awaited()
        refresh.assert_not_awaited()

    async def test_the_old_compaction_threshold_is_migrated_and_the_settings_refreshed(self):
        existing = {key: str(value) for key, value in config.settings.model_dump().items()}
        redis = object()
        with (
            patch.object(config, "migrate_compact_setting", new_callable=AsyncMock, return_value=1) as migrate,
            patch.object(config, "fetch_setting_overrides", new_callable=AsyncMock, return_value=existing),
            patch.object(config, "insert_missing_setting_overrides", new_callable=AsyncMock),
            patch.object(config, "upsert_setting_overrides", new_callable=AsyncMock),
            patch.object(config, "refresh_runtime_settings", new_callable=AsyncMock) as refresh,
        ):
            await config.seed_database_settings(redis)
        migrate.assert_awaited_once()
        refresh.assert_awaited_once_with(redis, SETTINGS_DEFAULT_USER)

    def test_the_new_compaction_settings_have_the_agreed_defaults_and_the_old_one_is_gone(self):
        defaults = config.settings.model_dump()
        self.assertEqual((defaults["chat_compact_idle_percent"], defaults["chat_compact_idle_minutes"],
                          defaults["chat_compact_force_percent"]), (40, 30, 90))
        self.assertNotIn("chat_compact_percent", defaults)


if __name__ == "__main__":
    unittest.main()
