from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from localization import load_language, save_language, translate


class LocalizationTests(unittest.TestCase):
    def test_english_static_and_dynamic_translations(self):
        self.assertEqual(translate("МОИ ПРОФИЛИ", "en"), "MY PROFILES")
        self.assertEqual(translate("ПОРЯДОК ПРОФИЛЯ", "en"), "PROFILE ORDER")
        self.assertEqual(translate("3 модов", "en"), "3 mods")
        self.assertEqual(
            translate("НАЙТИ КОЛЛЕКЦИИ / МОДПАКИ", "en"),
            "SEARCH COLLECTIONS / MOD PACKS",
        )

    def test_russian_and_unknown_text_are_preserved(self):
        self.assertEqual(translate("МОИ ПРОФИЛИ", "ru"), "МОИ ПРОФИЛИ")
        self.assertEqual(translate("Custom Workshop title", "en"), "Custom Workshop title")
        self.assertEqual(translate("Название модов и коллекции", "en"), "Название модов и коллекции")
        self.assertEqual(translate("Модпак: дата неизвестна", "en"), "Модпак: дата неизвестна")

    def test_language_preference_is_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings" / "language.json"
            self.assertIsNone(load_language(path))

            save_language(path, "en")
            self.assertEqual(load_language(path), "en")

            save_language(path, "ru")
            self.assertEqual(load_language(path), "ru")

    def test_invalid_or_corrupt_language_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "language.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(load_language(path))

            path.write_text('{"language":"de"}', encoding="utf-8")
            self.assertIsNone(load_language(path))

    def test_invalid_language_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "language.json"
            with self.assertRaises(ValueError):
                save_language(path, "de")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
