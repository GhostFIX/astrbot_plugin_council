import json
import tempfile
import unittest
from pathlib import Path

from core.config import (
    SettingsError,
    SettingsStore,
    default_settings,
    validate_settings,
)


class ConfigTests(unittest.TestCase):
    def test_defaults_validate(self):
        settings = validate_settings(default_settings())
        self.assertEqual(len(settings["members"]), 3)
        self.assertEqual(settings["search"]["mode"], "native")
        self.assertEqual(settings["safety"]["mode"], "standard")
        self.assertTrue(settings["safety"]["system_prompt"])
        self.assertEqual(settings["chat_context"]["mode"], "persistent")
        self.assertEqual(settings["chat_context"]["max_messages"], 50)
        self.assertTrue(settings["chat_context"]["vision_enabled"])
        self.assertTrue(settings["chat_context"]["stt_enabled"])

    def test_duplicate_member_name(self):
        data = default_settings()
        data["members"][1]["display_name"] = data["members"][0]["display_name"]
        with self.assertRaises(SettingsError):
            validate_settings(data)

    def test_too_few_members(self):
        data = default_settings()
        data["members"] = data["members"][:1]
        with self.assertRaises(SettingsError):
            validate_settings(data)

    def test_native_rounds_cannot_exceed_rounds(self):
        data = default_settings()
        data["discussion"]["rounds"] = 2
        data["search"]["native_rounds"] = 3
        with self.assertRaises(SettingsError):
            validate_settings(data)

    def test_chat_context_zero_allowed(self):
        data = default_settings()
        data["chat_context"]["max_messages"] = 0
        settings = validate_settings(data)
        self.assertEqual(settings["chat_context"]["max_messages"], 0)

    def test_unknown_provider_rejected(self):
        data = default_settings()
        data["members"][0]["provider_id"] = "missing"
        with self.assertRaises(SettingsError):
            validate_settings(data, provider_ids={"p1", "p2"})

    def test_store_atomic_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            store = SettingsStore(path)
            first = store.save(default_settings())
            self.assertTrue(path.exists())
            first["discussion"]["rounds"] = 4
            second = store.save(first)
            self.assertEqual(second["discussion"]["rounds"], 4)
            self.assertTrue(path.with_suffix(".json.bak").exists())
            loaded = store.load()
            self.assertEqual(loaded["discussion"]["rounds"], 4)

    def test_bad_json_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            store = SettingsStore(path)
            loaded = store.load()
            self.assertEqual(len(loaded["members"]), 3)
            self.assertTrue(store.last_error)


if __name__ == "__main__":
    unittest.main()
