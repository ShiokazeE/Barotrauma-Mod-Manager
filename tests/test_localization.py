from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from localization import load_language, save_language, translate


class LocalizationTests(unittest.TestCase):
    def test_zip_check_translations(self):
        self.assertEqual(translate("ПРОВЕРКА ZIP", "en"), "ZIP CHECK")
        self.assertEqual(
            translate("ZIP-архив прошёл проверку структуры и целостности.", "en"),
            "ZIP archive structure and integrity checks passed.",
        )
        self.assertEqual(translate("filelist.xml: корректный XML.", "en"), "filelist.xml: valid XML.")

    def test_direct_launch_translations(self):
        self.assertEqual(translate("Запуск игры", "en"), "Launch game")
        self.assertEqual(translate("Ошибка запуска", "en"), "Launch error")
        self.assertEqual(
            translate("Дождись завершения текущей операции перед запуском игры.", "en"),
            "Wait for the current operation to finish before launching the game.",
        )
        self.assertEqual(
            translate("Не найден файл Barotrauma.exe: ", "en"),
            "Barotrauma.exe was not found: ",
        )

    def test_english_static_and_dynamic_translations(self):
        self.assertEqual(translate("МОИ ПРОФИЛИ", "en"), "MY PROFILES")
        self.assertEqual(translate("ПОРЯДОК ПРОФИЛЯ", "en"), "PROFILE ORDER")
        self.assertEqual(translate("3 модов", "en"), "3 mods")
        self.assertEqual(
            translate("НАЙТИ КОЛЛЕКЦИИ / МОДПАКИ", "en"),
            "SEARCH COLLECTIONS / MOD PACKS",
        )
        self.assertEqual(translate("СОХРАНИТЬ СБОРКУ", "en"), "SAVE BUILD")
        self.assertEqual(translate("ИМПОРТ СБОРКИ", "en"), "IMPORT BUILD")
        self.assertEqual(translate("МОИ СБОРКИ", "en"), "MY BUILDS")
        self.assertEqual(translate("БИБЛИОТЕКА СБОРОК", "en"), "BUILD LIBRARY")
        self.assertEqual(translate("ОБНОВИТЬ СПИСОК", "en"), "REFRESH LIST")
        self.assertEqual(translate("СРАВНИТЬ С ПРОФИЛЕМ", "en"), "COMPARE WITH PROFILE")
        self.assertEqual(translate("ПРОВЕРИТЬ STEAM COLLECTION", "en"), "CHECK STEAM COLLECTION")
        self.assertEqual(translate("ОТКРЫТЬ COLLECTION", "en"), "OPEN COLLECTION")
        self.assertEqual(translate("СРАВНИТЬ С СЕРВЕРОМ", "en"), "COMPARE WITH SERVER")
        self.assertEqual(translate("ИСТОРИЯ ПРОФИЛЯ", "en"), "PROFILE HISTORY")
        self.assertEqual(
            translate("Скачать отсутствующие моды сразу после импорта?", "en"),
            "Download missing mods immediately after import?",
        )
        self.assertEqual(translate("Сборка: ", "en"), "Build: ")
        self.assertEqual(translate("Модов в сборке: ", "en"), "Mods in build: ")
        self.assertEqual(translate("Создан профиль: ", "en"), "Created profile: ")
        self.assertEqual(
            translate("Файлов модов пока нет локально: ", "en"),
            "Mod files not installed locally yet: ",
        )
        self.assertEqual(
            translate(
                "Список и порядок сохранены. Нажми «Проверить», чтобы загрузить отсутствующие моды.",
                "en",
            ),
            "The list and order are saved. Press Check Updates to download missing mods.",
        )
        self.assertEqual(translate("ВСТАВИТЬ", "en"), "PASTE")
        self.assertEqual(
            translate(
                "В буфере обмена не найден корректный ключ Steam из 32 шестнадцатеричных символов.",
                "en",
            ),
            "The clipboard does not contain a valid 32-character hexadecimal Steam key.",
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
