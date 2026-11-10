"""Russian/English UI localization and first-run language selection."""

from __future__ import annotations

import json
import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


SUPPORTED_LANGUAGES = ("ru", "en")

_EN = {
    # Main window and navigation
    "MOD MANAGER  ·  ПРОФИЛИ И WORKSHOP": "MOD MANAGER  ·  PROFILES & WORKSHOP",
    "Сменить язык": "Change language",
    "Не удалось сохранить настройки языка: ": "Could not save language settings: ",

    "Проверка путей…": "Checking paths…",
    "⚙ НАСТРОЙКИ": "⚙ SETTINGS",
    "✕ ЗАКРЫТЬ НАСТРОЙКИ": "✕ CLOSE SETTINGS",
    "РАСПОЛОЖЕНИЕ И ИНСТРУМЕНТЫ": "LOCATIONS & TOOLS",
    "Папка игры, SteamCMD и клиентский LuaCs-патч доступны в настройках.": "The game folder, SteamCMD and the client LuaCs patch are available in Settings.",
    "ПАПКА ИГРЫ": "GAME FOLDER",
    "Обзор": "Browse",
    "УСТАНОВИТЬ STEAMCMD": "INSTALL STEAMCMD",
    "ДОПОЛНИТЕЛЬНО": "TOOLS",
    "Установить LuaCs": "Install LuaCs",
    "Откатить LuaCs": "Restore LuaCs",
    "МОИ ПРОФИЛИ": "MY PROFILES",
    "АКТИВНЫЙ ПРОФИЛЬ": "ACTIVE PROFILE",
    "+ Новый": "+ New",
    "Копия": "Duplicate",
    "Удалить профиль": "Delete profile",
    "ЗАГРУЗЧИКИ": "DOWNLOAD WORKERS",
    "потока": "workers",
    "ПОРЯДОК ПРОФИЛЯ": "PROFILE ORDER",
    "БЫСТРОЕ ДОБАВЛЕНИЕ": "QUICK ADD",
    "Вставь ссылку на Workshop или ID мода / коллекции": "Paste a Workshop link or mod / collection ID",
    "СКАЧАТЬ И ДОБАВИТЬ": "DOWNLOAD & ADD",
    "АВТОПОРЯДОК": "AUTO SORT",
    "ПРОВЕРИТЬ": "CHECK UPDATES",
    "УДАЛИТЬ": "REMOVE",
    "ИМПОРТ XML": "IMPORT XML",
    "ЭКСПОРТ XML": "EXPORT XML",
    "ПРИМЕНИТЬ В ИГРУ": "APPLY TO GAME",
    "СОСТАВ ПРОФИЛЯ": "PROFILE CONTENTS",
    "СТАТУС": "STATUS",
    "ГРУППА": "GROUP",
    "МОД": "MOD",
    "Открыть в Steam Workshop": "Open in Steam Workshop",
    "Проверить XML-конфликты": "Check XML conflicts",
    "Удалить мод…": "Remove mod…",
    "ЖУРНАЛ ОПЕРАЦИЙ": "ACTIVITY LOG",
    "Моды": "Mods",
    "Коллекции / модпаки": "Collections / mod packs",
    "Популярные": "Most popular",
    "По релевантности": "Relevance",
    "По рейтингу": "Top rated",
    "Новые": "Newest",
    "Недавно обновлённые": "Recently updated",
    "НАЙТИ": "SEARCH",
    "Введи личный Steam Web API key, чтобы искать моды и коллекции.": "Add your personal Steam Web API key to search for mods and collections.",
    "Поиск только по Barotrauma. Скачивание проверяет пакет и добавляет его в активный профиль.": "Search is limited to Barotrauma. Downloads are validated and added to the active profile.",
    "РЕЗУЛЬТАТЫ": "RESULTS",
    "ЗАГРУЗИТЬ ЕЩЁ": "LOAD MORE",
    "ИГРА НАЙДЕНА": "GAME FOUND",
    "ВЫБЕРИ ПАПКУ ИГРЫ": "SELECT GAME FOLDER",
    "STEAMCMD ГОТОВ": "STEAMCMD READY",
    "STEAMCMD НЕ НАЙДЕН": "STEAMCMD NOT FOUND",

    # Profile and status labels
    "РУЧНОЙ ПОРЯДОК": "MANUAL ORDER",
    "АВТОПРИОРИТЕТ": "AUTO PRIORITY",
    "АВТОРСКИЙ ПОРЯДОК": "AUTHOR ORDER",
    "НЕ СКАЧАН": "NOT DOWNLOADED",
    "ОБНОВИТЬ": "UPDATE",
    "НЕИЗВЕСТНО": "UNKNOWN",
    "АКТУАЛЕН": "UP TO DATE",
    "КОЛЛЕКЦИЯ В ПРОФИЛЕ": "COLLECTION IN PROFILE",
    "СКАЧАТЬ КОЛЛЕКЦИЮ ↓": "DOWNLOAD COLLECTION ↓",
    "СКАЧАТЬ ↓": "DOWNLOAD ↓",
    "УЖЕ В ПРОФИЛЕ": "IN PROFILE",
    "ДОБАВИТЬ В ПРОФИЛЬ": "ADD TO PROFILE",
    "ЗАГРУЗКА…": "DOWNLOADING…",
    "ЗАГРУЗКА КОЛЛЕКЦИИ…": "DOWNLOADING COLLECTION…",
    "СТРАНИЦА ↗": "OPEN PAGE ↗",
    "WORKSHOP": "WORKSHOP",
    "Ничего не найдено. Попробуй изменить запрос.": "No results found. Try changing your search.",
    "дата неизвестна": "date unknown",
    "автор не указан": "author not provided",

    # Steam API key dialog
    "ЛИЧНЫЙ STEAM WEB API KEY": "PERSONAL STEAM WEB API KEY",
    "Ключ нужен для поиска по каталогу Workshop. Он хранится локально в зашифрованном виде Windows DPAPI и не включается в EXE.": "This key is required to search the Workshop catalog. It is encrypted locally with Windows DPAPI and is not bundled with the EXE.",
    "Ключ должен содержать 32 шестнадцатеричных символа.": "The key must contain 32 hexadecimal characters.",
    "Не удалось безопасно сохранить ключ: ": "Could not store the key securely: ",
    "Steam API key сохранён локально.": "Steam API key saved locally.",
    "Steam API key удалён.": "Steam API key removed.",
    "ПОЛУЧИТЬ КЛЮЧ ↗": "GET API KEY ↗",
    "СОХРАНИТЬ": "SAVE",
    "УДАЛИТЬ КЛЮЧ": "REMOVE KEY",
    "ОТМЕНА": "CANCEL",
    "Сначала добавь личный Steam Web API key через кнопку API KEY.": "Add your personal Steam Web API key using the API KEY button first.",

    # Workshop details and progress
    "КОЛЛЕКЦИЯ": "COLLECTION",
    "элементов:": "items:",
    "обновлена:": "updated:",
    "обновлён:": "updated:",
    "подписок:": "subscribers:",
    "Каталог: ": "Catalog: ",
    ". Выполни поиск.": ". Start a search.",
    "Ищу ": "Searching for ",
    " в Steam Workshop…": " in Steam Workshop…",
    "Ошибка поиска:": "Search error:",
    "Показано:": "Showing:",
    " · ещё есть": " · more available",
    " Steam API key сохранён локально.": " Steam API key saved locally.",

    # Common dialogs and operations
    "Выберите папку Barotrauma": "Select the Barotrauma folder",
    "Внимание": "Warning",
    "Выберите папку с игрой!": "Select the game folder!",
    "Готово": "Done",
    "LuaCs-патч установлен. Бэкап сохранён.": "LuaCs patch installed. A backup was saved.",
    "Ошибка LuaCs": "LuaCs error",
    "Ошибка": "Error",
    "Откат": "Rollback",
    "Файлы LuaCs восстановлены.": "LuaCs files have been restored.",
    "Откат LuaCs": "LuaCs rollback",
    "Операция выполняется": "Operation in progress",
    "Сейчас выполняется операция с файлами игры или Steam. Дождитесь её завершения, чтобы не оставить файлы в промежуточном состоянии.": "An operation involving game or Steam files is currently running. Let it finish to avoid leaving files in an incomplete state.",
    "Steam Workshop": "Steam Workshop",
    "Папка игры": "Game folder",
    "Укажи существующую папку Barotrauma в верхней части окна.": "Select an existing Barotrauma folder at the top of the window.",
    "Укажи существующую папку Barotrauma перед импортом коллекции.": "Select an existing Barotrauma folder before importing a collection.",
    "После загрузки не найден LocalMods/<ID>/filelist.xml.": "LocalMods/<ID>/filelist.xml was not found after the download.",
    "Steam не вернул моды из этой коллекции.": "Steam returned no mods for this collection.",
    "Вставьте ссылку на мод, коллекцию или числовой ID.": "Enter a mod or collection link, or a numeric ID.",
    "Укажите существующую папку Barotrauma.": "Select an existing Barotrauma folder.",
    "Ссылка на мод": "Mod link",
    "Новый профиль": "New profile",
    "Имя профиля:": "Profile name:",
    "Копировать профиль": "Duplicate profile",
    "Имя профиля должно быть не длиннее 40 символов.": "The profile name must be 40 characters or fewer.",
    "Профиль с таким именем уже существует.": "A profile with that name already exists.",
    "Такой профиль уже существует — он не будет перезаписан.": "A profile with that name already exists and will not be overwritten.",
    "Удаление": "Delete",
    "Сначала выбери мод в списке.": "Select a mod from the list first.",
    "Удалить мод": "Remove mod",
    "ДА — удалить файлы из LocalMods и кэша менеджера, а также убрать мод из ВСЕХ профилей.\n\nНЕТ — только убрать мод из активного профиля, файлы останутся на диске.\n\nОТМЕНА — ничего не менять.": "YES — delete the files from LocalMods and the manager cache, and remove the mod from ALL profiles.\n\nNO — remove the mod from the active profile only; files will remain on disk.\n\nCANCEL — make no changes.",
    "Имя профиля": "Profile name",
    "Удаление мода": "Remove mod",
    "Импорт XML": "XML import",
    "Импортировать авторский ModList": "Import author ModList",
    "Все файлы": "All files",
    "Импорт авторского порядка": "Import author order",
    "ОТМЕНА — ничего не менять.": "CANCEL — make no changes.",
    "Экспортировать ModList": "Export ModList",
    "Экспорт завершён": "Export complete",
    "Автоприоритет": "Automatic priority",
    "Предпросмотр автоприоритета": "Automatic priority preview",
    "ПРЕДЛАГАЕМЫЙ ПОРЯДОК": "PROPOSED LOAD ORDER",
    "ПРИМЕНИТЬ ПОРЯДОК": "APPLY ORDER",
    "Активный профиль изменился. Повтори сортировку.": "The active profile has changed. Run sorting again.",
    "Авторский порядок": "Author order",
    "Автоприоритет изменит исходную последовательность автора. Продолжить?": "Automatic sorting will change the author's original order. Continue?",
    "Проверка обновлений": "Check for updates",
    "В текущем профиле пока нет модов.": "There are no mods in the current profile yet.",
    "Установленные моды актуальны.": "All installed mods are up to date.",
    "Проверка завершена": "Check complete",
    "Обновления Workshop": "Workshop updates",
    "Нужно обновить или загрузить ": "The following mods need to be updated or downloaded: ",
    "Продолжить?": "Continue?",
    "Детектор конфликтов XML": "XML conflict detector",
    "Конфликтов не обнаружено.\n": "No conflicts found.\n",
    "Профиль применен в config_player.xml!": "Profile applied to config_player.xml!",
    "Файл сохранён:\n": "File saved:\n",
    "Импорт завершён": "Import complete",
    "Ошибка импорта": "Import error",

    # Sort preview and conflict report
    "Lua → CsForBarotrauma → LuaCsForBarotrauma → патчи → утилиты → крупные модификации → контент → субмарины. Порядок внутри групп сохранён.": "Lua → CsForBarotrauma → LuaCsForBarotrauma → patches → utilities → major overhauls → content → submarines. Original order is preserved within each group.",
    "Lua For Barotrauma (2559634234) не найден в этом профиле — менеджер не добавляет его автоматически. Если он будет добавлен, сортировщик всегда поставит его первым.\n\n": "Lua For Barotrauma (2559634234) is not in this profile. The manager will not add it automatically. If you add it, the sorter will always place it first.\n\n",
    "Конфликтующих идентификаторов: ": "Conflicting identifiers: ",
    "Конфликтов не обнаружено.\n": "No conflicts found.\n",

    # Core / Steam / download errors that can reach the UI
    "Небезопасный путь в ZIP: ": "Unsafe path in ZIP: ",
    "Недопустимый путь Windows в ZIP: ": "Invalid Windows path in ZIP: ",
    "Символические ссылки в ZIP запрещены: ": "Symbolic links are not allowed in ZIP archives: ",
    "В архиве слишком много файлов.": "The archive contains too many files.",
    "Распакованный архив превышает допустимый размер.": "The extracted archive exceeds the allowed size.",
    "Подозрительная степень сжатия: ": "Suspicious compression ratio: ",
    "Файл пытается выйти за пределы папки: ": "The file attempts to escape the destination folder: ",
    "Размер XML-файла пустой или превышает 5 МБ.": "The XML file is empty or exceeds 5 MB.",
    "DTD и пользовательские XML-сущности в пресетах запрещены.": "DTDs and custom XML entities are not allowed in presets.",
    "Некорректный XML-файл: ": "Invalid XML file: ",
    "Это не пресет Barotrauma: ожидается корневой элемент <mods>.": "This is not a Barotrauma preset: the root <mods> element was expected.",
    "В XML не найдено ни одного корректного элемента <Workshop id=.../>.": "No valid <Workshop id=.../> elements were found in the XML.",
    "Безопасное сохранение Steam API key через DPAPI доступно только в Windows.": "Secure Steam API key storage via DPAPI is only available on Windows.",
    "Не найден SteamCMD: ": "SteamCMD not found: ",
    "SteamCMD превысил лимит времени (": "SteamCMD exceeded the time limit (",
    "Автоустановка SteamCMD поддерживается только в Windows. Укажите путь вручную.": "Automatic SteamCMD installation is supported only on Windows. Set the path manually.",
    "Архив SteamCMD неожиданно большой.": "The SteamCMD archive is unexpectedly large.",
    "В архиве не найден steamcmd.exe": "steamcmd.exe was not found in the archive.",
    "Не удалось установить SteamCMD: ": "Could not install SteamCMD: ",
    "Первый запуск SteamCMD завершился с кодом ": "The first SteamCMD run exited with code ",
    "Не удалось найти архив luacsforbarotrauma_patch_windows_client.zip на GitHub.": "Could not find luacsforbarotrauma_patch_windows_client.zip on GitHub.",
    "В выбранной папке игры не найден Barotrauma.dll!": "Barotrauma.dll was not found in the selected game folder!",
    "Бэкап LuaCs уже существует. Сначала откатите предыдущий патч.": "A LuaCs backup already exists. Roll back the previous patch first.",
    "Архив LuaCs превышает лимит 512 МБ.": "The LuaCs archive exceeds the 512 MB limit.",
    "Архив LuaCs пуст.": "The LuaCs archive is empty.",
    "Бэкап _vanilla_luacs_backup не найден в папке игры!": "The _vanilla_luacs_backup folder was not found in the game directory!",
    "Вставьте числовой ID или ссылку steamcommunity.com на Workshop.": "Enter a numeric ID or a steamcommunity.com Workshop link.",
    "Это не ссылка на элемент Steam Workshop.": "This is not a Steam Workshop item link.",
    "В ссылке не найден корректный Workshop ID.": "No valid Workshop ID was found in the link.",
    "Steam Web API key должен состоять из 32 шестнадцатеричных символов.": "The Steam Web API key must contain 32 hexadecimal characters.",
    "Неизвестный тип Workshop-каталога.": "Unknown Workshop catalog type.",
    "Steam отклонил API key. Проверь ключ на https://steamcommunity.com/dev/apikey и повтори попытку.": "Steam rejected the API key. Check it at https://steamcommunity.com/dev/apikey and try again.",
    "Steam временно ограничил частоту запросов. Повтори поиск через минуту.": "Steam has temporarily rate-limited requests. Try searching again in a minute.",
    "Steam вернул некорректный JSON при поиске Workshop.": "Steam returned invalid JSON while searching Workshop.",
    "Steam QueryFiles вернул неожиданный формат ответа.": "Steam QueryFiles returned an unexpected response format.",
    "Steam API отклонил запрос: ": "Steam API rejected the request: ",
    "Steam API не вернул список Workshop-элементов.": "Steam API did not return a list of Workshop items.",
    "Данные Workshop недоступны.": "Workshop data is unavailable.",
    "SteamCMD не создал корректный filelist.xml": "SteamCMD did not create a valid filelist.xml.",
    "Не удалось получить корректные файлы мода.": "Could not obtain valid mod files.",
    "Некорректный Workshop ID.": "Invalid Workshop ID.",
    "Не удалось подключить мод ": "Could not link mod ",
    "Файлы получены, но не удалось подключить filelist.xml в LocalMods.": "Files were downloaded, but filelist.xml could not be linked in LocalMods.",
    "Steam CDN перенаправил запрос на недоверенный адрес.": "Steam CDN redirected the request to an untrusted address.",
    "Steam CDN вернул редирект без адреса назначения.": "Steam CDN returned a redirect without a destination URL.",
    "Слишком много перенаправлений Steam CDN.": "Too many Steam CDN redirects.",
    "Steam CDN не вернул ответ на загрузку.": "Steam CDN did not return a download response.",
    "Размер файла превышает лимит прямого скачивания (2 ГБ).": "The file exceeds the direct download limit (2 GB).",
    "Файл превысил лимит прямого скачивания (2 ГБ).": "The file exceeded the direct download limit (2 GB).",
    "Steam CDN-архив не содержит filelist.xml Barotrauma.": "The Steam CDN archive does not contain a Barotrauma filelist.xml.",
    "Папка игры не найдена: ": "Game folder not found: ",
    "Не найден config_player.xml: ": "config_player.xml not found: ",
    "Не удалось получить ни одного Workshop ID.": "No Workshop IDs could be retrieved.",
    "После загрузки не найден filelist.xml.": "filelist.xml was not found after downloading.",
    "Авторский порядок сохранён. Чтобы включить доступные моды, ": "The author's order has been preserved. To enable available mods, ",
    "Для отсутствующих модов используй ": "For missing mods, use ",
    "нажми «Применить в игру». Для отсутствующих модов используй «Проверить».": "click “Apply to Game”. For missing mods, use “Check Updates”.",
}

# These replacements are intentionally specific: they translate generated status/log
# messages without translating arbitrary Workshop titles, descriptions or profile names.
_FRAGMENTS = (
    ("НАЙТИ ", "SEARCH "),
    ("КОЛЛЕКЦИИ / МОДПАКИ", "COLLECTIONS / MOD PACKS"),
    ("коллекции / модпаки", "collections / mod packs"),
    ("моды", "mods"),
    ("Каталог: ", "Catalog: "),
    (". Выполни поиск.", ". Start a search."),
    ("[ОШИБКА профилей]", "[PROFILE ERROR]"),
    ("[ОШИБКА SteamCMD]", "[STEAMCMD ERROR]"),
    ("[ОШИБКА LuaCs]", "[LUACS ERROR]"),
    ("[ОШИБКА отката LuaCs]", "[LUACS ROLLBACK ERROR]"),
    ("[ОШИБКА автоприоритета]", "[AUTO-PRIORITY ERROR]"),
    ("[ОШИБКА обновления]", "[UPDATE ERROR]"),
    ("[ОШИБКА импорта]", "[IMPORT ERROR]"),
    ("[Ошибка Workshop download]", "[WORKSHOP DOWNLOAD ERROR]"),
    ("[Ошибка импорта коллекции]", "[COLLECTION IMPORT ERROR]"),
    ("[Предупреждение]", "[Warning]"),
    ("[Приоритеты]", "[Load order]"),
    ("[Профиль]", "[Profile]"),
    ("[Удаление]", "[Removal]"),
    ("[ModList]", "[ModList]"),
    ("[Steam API]", "[Steam API]"),
    ("[SteamCMD]", "[SteamCMD]"),
    ("[LocalMods]", "[LocalMods]"),
    ("[Steam CDN]", "[Steam CDN]"),
    ("[Steam Client]", "[Steam Client]"),
    ("[✔] Проверка завершена:", "[✔] Check complete:"),
    ("[✔] Успешно обработано:", "[✔] Successfully processed:"),
    ("[✔] Коллекция обработана:", "[✔] Collection processed:"),
    ("[✔] Профиль", "[✔] Profile"),
    ("[i] Если у мода", "[i] If a mod"),
    ("Папка игры найдена автоматически:", "Game folder detected automatically:"),
    ("Автоустановка не удалась:", "Automatic installation failed:"),
    ("Попробует снова при скачивании модов.", "It will retry when downloading mods."),
    ("Установлено и включено", "installed and enabled"),
    ("модов за ", "mods in "),
    ("Порядок готов.", "Order is ready."),
    ("проверяю filelist.xml", "checking filelist.xml"),
    ("файл filelist.xml найден", "filelist.xml found"),
    ("Загрузка: ", "Downloading: "),
    ("Параллельная загрузка:", "Parallel download:"),
    ("изолированных загрузчика", "isolated workers"),
    ("Повторно использован изолированный кэш.", "Reused isolated cache."),
    ("Установлен:", "Installed:"),
    ("Ищу ", "Searching for "),
    ("Найдено ", "Found "),
    ("модов", "mods"),
    ("коллекции", "collections"),
    ("дата неизвестна", "date unknown"),
    ("автор не указан", "author not provided"),
    ("КОЛЛЕКЦИЯ  ·  ID ", "COLLECTION  ·  ID "),
    ("элементов: ", "items: "),
    ("обновлена: ", "updated: "),
    ("подписок: ", "subscribers: "),
    ("обновлён: ", "updated: "),
    ("АВТОРСКИЙ ПОРЯДОК · ", "AUTHOR ORDER · "),
    ("Не удалось скачать ", "Could not download "),
    (" шт.:", " item(s):"),
    ("Проверка завершена: загружаемых обновлений не найдено.", "Check complete: no downloadable updates were found."),
    ("Установленные моды актуальны.", "All installed mods are up to date."),
    ("Для ", "For "),
    (" модов Steam не отдал достаточно данных для сравнения.", " mods, Steam did not provide enough data for comparison."),
    ("Импортировано модов: ", "Mods imported: "),
    ("Пока не найдено локальных файлов: ", "Local files not found yet: "),
    ("Показано: ", "Showing: "),
    (" · ещё есть", " · more available"),
)

def translate(value: object, language: str = "ru") -> object:
    """Translate app-owned Russian UI text; leave unknown/user-provided text intact."""
    if language != "en" or not isinstance(value, str):
        return value

    exact = _EN.get(value)
    if exact is not None:
        return exact

    # Dynamic counters and short status lines.
    match = re.fullmatch(r"(\d+)\s+модов", value)
    if match:
        return f"{match.group(1)} mods"
    match = re.fullmatch(r"Показано:\s*(\d+)(\s*· ещё есть)?", value)
    if match:
        suffix = " · more available" if match.group(2) else ""
        return f"Showing: {match.group(1)}{suffix}"
    match = re.fullmatch(r"(\d+)\s+модов\s+·\s+Профиль:\s+(.+)", value)
    if match:
        return f"{match.group(1)} mods · Profile: {match.group(2)}"

    result = value
    for source, target in _FRAGMENTS:
        if source in result:
            result = result.replace(source, target)
    return result


def load_language(path: Path) -> str | None:
    """Return a valid saved language, or None when no usable preference exists."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    language = data.get("language") if isinstance(data, dict) else None
    return language if language in SUPPORTED_LANGUAGES else None


def save_language(path: Path, language: str) -> None:
    """Atomically persist the UI language separately from mod profiles."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump({"language": language, "schema_version": 1}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def choose_language(root: tk.Tk, path: Path) -> str:
    """Show the bilingual first-run language picker and persist the selection."""
    root.withdraw()
    selected = {"language": "ru"}
    dialog = tk.Toplevel(root)
    dialog.title("Barotrauma Mod Manager — Language")
    dialog.geometry("460x255")
    dialog.resizable(False, False)
    dialog.configure(bg="#0d131b")
    dialog.transient(root)

    panel = tk.Frame(
        dialog, bg="#151e29", padx=24, pady=20,
        highlightbackground="#29394a", highlightthickness=1,
    )
    panel.pack(fill="both", expand=True, padx=14, pady=14)

    tk.Label(
        panel, text="WELCOME  ·  ДОБРО ПОЖАЛОВАТЬ",
        bg="#151e29", fg="#65c9f1", font=("Segoe UI", 9, "bold"),
    ).pack(anchor="w")
    tk.Label(
        panel, text="Choose interface language / Выберите язык интерфейса",
        bg="#151e29", fg="#edf4fa", font=("Segoe UI", 11, "bold"),
        wraplength=390, justify="left",
    ).pack(anchor="w", pady=(12, 6))
    tk.Label(
        panel, text="Your choice is saved on this PC. / Выбор сохранится на этом ПК.",
        bg="#151e29", fg="#91a4b7", font=("Segoe UI", 8),
        wraplength=390, justify="left",
    ).pack(anchor="w", pady=(0, 18))

    buttons = tk.Frame(panel, bg="#151e29")
    buttons.pack(fill="x")

    def select(language: str) -> None:
        try:
            save_language(path, language)
        except OSError as exc:
            messagebox.showwarning(
                "Language settings / Настройки языка",
                f"Could not save the language preference / Не удалось сохранить язык:\n{exc}",
                parent=dialog,
            )
        selected["language"] = language
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(
        buttons, text="РУССКИЙ", command=lambda: select("ru"),
        bg="#234763", fg="#ffffff", activebackground="#2c6288",
        activeforeground="#ffffff", relief="flat", bd=0, cursor="hand2",
        font=("Segoe UI", 10, "bold"), padx=18, pady=11,
    ).pack(side="left", fill="x", expand=True, padx=(0, 6))
    tk.Button(
        buttons, text="ENGLISH", command=lambda: select("en"),
        bg="#176c53", fg="#ffffff", activebackground="#218b6c",
        activeforeground="#ffffff", relief="flat", bd=0, cursor="hand2",
        font=("Segoe UI", 10, "bold"), padx=18, pady=11,
    ).pack(side="left", fill="x", expand=True, padx=(6, 0))

    dialog.protocol("WM_DELETE_WINDOW", lambda: select("ru"))
    dialog.update_idletasks()
    x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2)
    y = max(0, (dialog.winfo_screenheight() - dialog.winfo_height()) // 2)
    dialog.geometry(f"+{x}+{y}")
    dialog.lift()
    dialog.focus_force()
    dialog.grab_set()
    root.wait_window(dialog)
    root.deiconify()
    return selected["language"]


def choose_or_load_language(root: tk.Tk, path: Path) -> str:
    """Load the per-PC preference, asking only on first launch or invalid settings."""
    saved = load_language(path)
    return saved if saved else choose_language(root, path)
