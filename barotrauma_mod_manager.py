import io
import json
import ctypes
from ctypes import wintypes
import os
import queue
import re
import shutil
import subprocess
import sys
import stat
import tempfile
import webbrowser
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urljoin, urlparse
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from localization import choose_or_load_language, save_language, translate
import requests
try:
    from PIL import Image, ImageTk
except ImportError:  # Thumbnails are optional; Workshop search/download still works.
    Image = None
    ImageTk = None
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BAROTRAUMA_APP_ID = "602960"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

STEAMCMD_ZIP_URLS = [
    "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
    "https://media.steampowered.com/installer/steamcmd.zip",
    "https://cdn.cloudflare.steamstatic.com/client/installer/steamcmd.zip",
]
LUACS_DIRECT_PATCH_URL = (
    "https://github.com/evilfactory/LuaCsForBarotrauma/releases/download/latest/"
    "luacsforbarotrauma_patch_windows_client.zip"
)
LUACS_GITHUB_API_RELEASES = "https://api.github.com/repos/evilfactory/LuaCsForBarotrauma/releases"

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

if os.name == "nt":
    _data_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
else:
    _data_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

APP_DATA_DIR = _data_root / "BarotraumaModManager"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = APP_DATA_DIR / "baro_profiles.json"
LANGUAGE_FILE = APP_DATA_DIR / "language.json"
STEAM_API_KEY_FILE = APP_DATA_DIR / "steam_api_key.dpapi"
LEGACY_STATE_FILE = APP_DIR / "baro_profiles.json"
if not STATE_FILE.exists() and LEGACY_STATE_FILE.exists():
    try:
        shutil.copy2(LEGACY_STATE_FILE, STATE_FILE)
    except OSError:
        pass

DEFAULT_STEAMCMD_DIR = APP_DATA_DIR / "steamcmd"
DEFAULT_STEAMCMD_EXE = DEFAULT_STEAMCMD_DIR / "steamcmd.exe"
MAX_DIRECT_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024
MAX_COLLECTION_DEPTH = 5
STEAMCMD_LOCK = threading.RLock()

CORE_FRAMEWORK_IDS = {
    "2559634234",  # Lua For Barotrauma
    "2795927223",  # CsForBarotrauma
    "2959574986",  # LuaCsForBarotrauma
}

PATCH_KEYWORDS = ("patch", "compat", "compatibility", "fix", "hotfix", "bridge", "патч", "фикс")
OVERHAUL_KEYWORDS = (
    "neurotrauma", "dynamic europa", "baroverhaul", "enhanced armaments",
    "real sonar", "hungry europans", "barotraumatic",
)

# These IDs are deliberately ordered, not alphabetically sorted. Lua must be first.
CORE_FRAMEWORK_ORDER = (
    "2559634234",  # Lua For Barotrauma — always first when present
    "2795927223",  # CsForBarotrauma
    "2959574986",  # LuaCsForBarotrauma
)
CORE_FRAMEWORK_NAMES = {
    "lua for barotrauma": "2559634234",
    "csforbarotrauma": "2795927223",
    "luacsforbarotrauma": "2959574986",
}
UTILITY_KEYWORDS = (
    "performance", "optimization", "optimisation", "enhanced loading",
    "entitydisabledistance", "dont crash", "don't crash", "debug console",
    "fix stuck", "stun remover", "performance enhancement",
)
MODLIST_MAX_BYTES = 5 * 1024 * 1024
MODLIST_MAX_ITEMS = 2000


def safe_extract_zip(zf: zipfile.ZipFile, destination: Path,
                     max_unpacked_bytes: int = MAX_ZIP_UNPACKED_BYTES) -> None:
    """Extract an archive while rejecting path traversal, symlinks and zip bombs."""
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    total_size = 0
    file_count = 0

    for info in zf.infolist():
        if not info.filename:
            continue
        normalized = info.filename.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or any(part in ("..", "") for part in pure.parts):
            raise ValueError(f"Небезопасный путь в ZIP: {info.filename}")
        if any(":" in part for part in pure.parts):
            raise ValueError(f"Недопустимый путь Windows в ZIP: {info.filename}")

        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise ValueError(f"Символические ссылки в ZIP запрещены: {info.filename}")

        file_count += 1
        if file_count > 100_000:
            raise ValueError("В архиве слишком много файлов.")
        total_size += int(info.file_size or 0)
        if total_size > max_unpacked_bytes:
            raise ValueError("Распакованный архив превышает допустимый размер.")
        if info.file_size > 0 and info.compress_size > 0:
            if info.file_size / info.compress_size > 1000:
                raise ValueError(f"Подозрительная степень сжатия: {info.filename}")

        target = destination.joinpath(*pure.parts)
        target_resolved = target.resolve()
        if os.path.commonpath((str(base), str(target_resolved))) != str(base):
            raise ValueError(f"Файл пытается выйти за пределы папки: {info.filename}")

        if info.is_dir() or normalized.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def is_trusted_steam_cdn_url(url: str) -> bool:
    """Only accept HTTPS URLs hosted by known Steam Workshop content domains."""
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and (
                host == "steamusercontent.com"
                or host.endswith(".steamusercontent.com")
                or host == "steamusercontent-a.akamaihd.net"
            )
        )
    except ValueError:
        return False


def find_filelist_root(path: Path, max_depth: int = 4) -> Path | None:
    """Find the actual Barotrauma package root, even if the ZIP has an outer folder."""
    direct = path / "filelist.xml"
    if direct.is_file():
        return path
    try:
        for candidate in path.rglob("filelist.xml"):
            try:
                depth = len(candidate.relative_to(path).parts)
            except ValueError:
                continue
            if depth <= max_depth:
                return candidate.parent
    except OSError:
        return None
    return None


def parse_barotrauma_modlist(xml_path: Path) -> tuple[str, list[dict]]:
    """Parse a Barotrauma ModLists preset (<mods><Vanilla/><Workshop .../></mods>)."""
    xml_path = Path(xml_path)
    size = xml_path.stat().st_size
    if size <= 0 or size > MODLIST_MAX_BYTES:
        raise ValueError("Размер XML-файла пустой или превышает 5 МБ.")
    raw = xml_path.read_bytes()
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD и пользовательские XML-сущности в пресетах запрещены.")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Некорректный XML-файл: {exc}") from exc

    root_tag = str(root.tag).split("}")[-1].casefold()
    if root_tag != "mods":
        raise ValueError("Это не пресет Barotrauma: ожидается корневой элемент <mods>.")

    preset_name = str(root.attrib.get("name") or xml_path.stem).strip()[:100]
    result: list[dict] = []
    seen: set[str] = set()
    for child in list(root):
        tag = str(child.tag).split("}")[-1].casefold()
        if tag != "workshop":
            # <Vanilla/> is the game's base package and is not a Workshop item.
            continue
        mod_id = str(child.attrib.get("id", "")).strip()
        if not mod_id.isdigit() or not (5 <= len(mod_id) <= 20):
            continue
        if mod_id in seen:
            continue
        seen.add(mod_id)
        title = str(child.attrib.get("name") or f"Workshop_{mod_id}").strip()
        title = title.replace("\r", " ").replace("\n", " ")[:200]
        result.append({"id": mod_id, "title": title, "dependencies": []})
        if len(result) > MODLIST_MAX_ITEMS:
            raise ValueError(f"В пресете больше {MODLIST_MAX_ITEMS} модов — импорт остановлен.")

    if not result:
        raise ValueError("В XML не найдено ни одного корректного элемента <Workshop id=.../>.")
    return preset_name, result


def serialize_barotrauma_modlist(mods: list[dict], preset_name: str) -> bytes:
    """Serialize the current profile into Barotrauma's shareable ModLists format."""
    name = str(preset_name or "Barotrauma ModList").strip()[:100] or "Barotrauma ModList"
    root = ET.Element("mods", {"name": name})
    ET.SubElement(root, "Vanilla")
    seen: set[str] = set()
    for mod in mods:
        mod_id = str(mod.get("id", "")).strip()
        if not mod_id.isdigit() or not (5 <= len(mod_id) <= 20) or mod_id in seen:
            continue
        seen.add(mod_id)
        title = str(mod.get("title") or f"Workshop_{mod_id}").replace("\r", " ").replace("\n", " ").strip()[:200]
        ET.SubElement(root, "Workshop", {"name": title or f"Workshop_{mod_id}", "id": mod_id})
        if len(seen) > MODLIST_MAX_ITEMS:
            raise ValueError(f"В профиле больше {MODLIST_MAX_ITEMS} модов — экспорт остановлен.")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)



class WindowsSecretStore:
    """Store a personal Steam Web API key encrypted with the current Windows user DPAPI."""

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def _crypt(cls, payload: bytes, protect: bool) -> bytes:
        if os.name != "nt":
            raise OSError("Безопасное сохранение Steam API key через DPAPI доступно только в Windows.")
        buffer = ctypes.create_string_buffer(payload)
        data_in = cls._DataBlob(
            len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        data_out = cls._DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt_fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        crypt_fn.restype = wintypes.BOOL
        crypt_fn.argtypes = [
            ctypes.POINTER(cls._DataBlob),
            wintypes.LPCWSTR if protect else ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(cls._DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(cls._DataBlob),
        ]
        description = ctypes.c_wchar_p("Barotrauma Mod Manager Steam API key") if protect else None
        ok = crypt_fn(
            ctypes.byref(data_in), description, None, None, None,
            0x1, ctypes.byref(data_out),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
        try:
            return ctypes.string_at(data_out.pbData, data_out.cbData)
        finally:
            if data_out.pbData:
                local_free = kernel32.LocalFree
                local_free.argtypes = [ctypes.c_void_p]
                local_free.restype = ctypes.c_void_p
                local_free(ctypes.cast(data_out.pbData, ctypes.c_void_p))

    def load(self) -> str:
        if os.name != "nt" or not self.path.is_file():
            return ""
        try:
            encrypted = self.path.read_bytes()
            if not encrypted:
                return ""
            return self._crypt(encrypted, protect=False).decode("utf-8").strip()
        except Exception:
            return ""

    def save(self, secret: str) -> None:
        secret = secret.strip()
        if not secret:
            self.delete()
            return
        encrypted = self._crypt(secret.encode("utf-8"), protect=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_bytes(encrypted)
        os.replace(temp_path, self.path)

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


class ProfileStore:
    """Thread-safe, atomic persistence for profiles and installed Workshop versions."""

    def __init__(self, path: Path, default_profile: str = "Основной"):
        self.path = path
        self.default_profile = default_profile
        self._lock = threading.RLock()
        self.data = {
            "schema_version": 3,
            "game_dir": "",
            "steamcmd_path": str(DEFAULT_STEAMCMD_EXE) if DEFAULT_STEAMCMD_EXE.exists() else "",
            "active_profile": self.default_profile,
            "installed_versions": {},
            "profiles": {self.default_profile: []},
            "profile_sources": {},
            "parallel_downloads": 2,
        }
        self.load()

    def load(self):
        with self._lock:
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
                except (OSError, json.JSONDecodeError, TypeError) as exc:
                    # Не затираем автоматически повреждённый профиль: пользователь сможет
                    # восстановить его из .corrupt-файла.
                    try:
                        damaged = self.path.with_name(
                            f"{self.path.stem}.corrupt-{int(time.time())}{self.path.suffix}"
                        )
                        shutil.copy2(self.path, damaged)
                    except OSError:
                        pass
                    self.data["load_warning"] = str(exc)

            profiles = self.data.get("profiles")
            if not isinstance(profiles, dict) or not profiles:
                profiles = {self.default_profile: []}
            normalized_profiles = {}
            for name, mods in profiles.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                if not isinstance(mods, list):
                    mods = []
                valid_mods = []
                for mod in mods:
                    if not isinstance(mod, dict):
                        continue
                    mod_id = str(mod.get("id", ""))
                    if mod_id.isdigit():
                        item = dict(mod)
                        item["id"] = mod_id
                        valid_mods.append(item)
                normalized_profiles[name.strip()] = valid_mods

            if not normalized_profiles:
                normalized_profiles = {self.default_profile: []}
            self.data["profiles"] = normalized_profiles

            raw_sources = self.data.get("profile_sources")
            if not isinstance(raw_sources, dict):
                raw_sources = {}
            self.data["profile_sources"] = {
                str(name): dict(source)
                for name, source in raw_sources.items()
                if str(name) in normalized_profiles and isinstance(source, dict)
            }

            active = self.data.get("active_profile")
            if active not in normalized_profiles:
                self.data["active_profile"] = next(iter(normalized_profiles))

            versions = self.data.get("installed_versions")
            if not isinstance(versions, dict):
                versions = {}
            self.data["installed_versions"] = {
                str(k): int(v) for k, v in versions.items()
                if str(k).isdigit() and str(v).isdigit()
            }
            self.data["schema_version"] = 3
            try:
                parallel = int(self.data.get("parallel_downloads", 2))
            except (TypeError, ValueError):
                parallel = 2
            self.data["parallel_downloads"] = max(1, min(3, parallel))

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_name(self.path.name + ".tmp")
            with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)

    def get_active_mods(self) -> list[dict]:
        with self._lock:
            profile = self.data["active_profile"]
            return self.data["profiles"].setdefault(profile, [])

    def get_profile_mods(self, profile_name: str) -> list[dict]:
        with self._lock:
            return [dict(mod) for mod in self.data["profiles"].get(profile_name, [])]

    def set_active_mods(self, mods: list[dict]):
        with self._lock:
            profile = self.data["active_profile"]
            self.data["profiles"][profile] = [dict(mod) for mod in mods]
            self.save()

    def set_profile_mods(self, profile_name: str, mods: list[dict]):
        with self._lock:
            if profile_name not in self.data["profiles"]:
                raise KeyError(f"Профиль «{profile_name}» больше не существует.")
            self.data["profiles"][profile_name] = [dict(mod) for mod in mods]
            self.save()

    def set_profile_source(self, profile_name: str, source: dict | None):
        with self._lock:
            sources = self.data.setdefault("profile_sources", {})
            if source is None:
                sources.pop(profile_name, None)
            else:
                sources[profile_name] = dict(source)
            self.save()

    def get_profile_source(self, profile_name: str) -> dict:
        with self._lock:
            source = self.data.setdefault("profile_sources", {}).get(profile_name, {})
            return dict(source) if isinstance(source, dict) else {}

    def mark_mod_updated(self, mod_id: str, steam_time_updated: int):
        if steam_time_updated > 0:
            with self._lock:
                self.data["installed_versions"][str(mod_id)] = int(steam_time_updated)


class BaroModManagerCore:
    def __init__(self, game_dir: str, steamcmd_path: str, log_callback=print):
        self.game_dir = Path(game_dir).expanduser() if game_dir else Path()
        self.steamcmd_path = Path(steamcmd_path).expanduser() if steamcmd_path else DEFAULT_STEAMCMD_EXE
        self.steamcmd_dir = self.steamcmd_path.parent
        self.workshop_content_dir = self.steamcmd_dir / "steamapps" / "workshop" / "content" / BAROTRAUMA_APP_ID
        self.local_mods_dir = self.game_dir / "LocalMods"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "BarotraumaModManager/2.6"})
        retry = Retry(
            total=4, connect=3, read=3, status=3, backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST", "HEAD"}),
            respect_retry_after_header=True, raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        appdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            self.user_workshop_dir = (
                Path(appdata) / "Daedalic Entertainment GmbH" / "Barotrauma"
                / "WorkshopMods" / "Installed"
            )
            self.config_path = Path(appdata) / "Daedalic Entertainment GmbH" / "Barotrauma" / "config_player.xml"
        else:
            self.user_workshop_dir = Path.home() / ".local" / "share" / "Barotrauma" / "WorkshopMods" / "Installed"
            self.config_path = self.game_dir / "config_player.xml"

        game_config = self.game_dir / "config_player.xml"
        if game_config.exists():
            self.config_path = game_config
        self.backup_luacs_dir = self.game_dir / "_vanilla_luacs_backup"
        self.log = log_callback
        self.workers = 2  # Умеренная параллельность по умолчанию; третий воркер доступен в настройках.

    def _set_steamcmd(self, exe: Path):
        self.steamcmd_path = exe
        self.steamcmd_dir = exe.parent
        self.workshop_content_dir = self.steamcmd_dir / "steamapps" / "workshop" / "content" / BAROTRAUMA_APP_ID

    def _run_steamcmd(self, args, on_line=None, timeout: int = 1800) -> int:
        with STEAMCMD_LOCK:
            return self._run_steamcmd_locked(args, on_line=on_line, timeout=timeout)

    def _run_steamcmd_locked(self, args, on_line=None, timeout: int = 1800) -> int:
        if not self.steamcmd_path.is_file():
            raise FileNotFoundError(f"Не найден SteamCMD: {self.steamcmd_path}")
        self.steamcmd_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.steamcmd_path)] + list(args)
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.steamcmd_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )

        reader_errors = []
        def read_output():
            try:
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        line = raw_line.strip()
                        if line and on_line:
                            on_line(line)
            except Exception as exc:
                reader_errors.append(exc)

        reader = threading.Thread(target=read_output, name="SteamCMD-output", daemon=True)
        reader.start()
        try:
            return_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait(timeout=10)
            reader.join(timeout=5)
            raise TimeoutError(f"SteamCMD превысил лимит времени ({timeout} сек.) и был остановлен.") from exc
        finally:
            reader.join(timeout=10)
            if proc.stdout:
                proc.stdout.close()

        if reader_errors:
            self.log(f"[SteamCMD] Ошибка чтения вывода: {reader_errors[0]}")
        return int(return_code)
    def ensure_steamcmd_installed(self) -> Path:
        with STEAMCMD_LOCK:
            return self._ensure_steamcmd_installed_locked()

    def _ensure_steamcmd_installed_locked(self) -> Path:
        """Загружает SteamCMD в пользовательскую папку и безопасно распаковывает архив."""
        if self.steamcmd_path.is_file():
            return self.steamcmd_path
        if os.name != "nt":
            raise RuntimeError("Автоустановка SteamCMD поддерживается только в Windows. Укажите путь вручную.")

        target_dir = DEFAULT_STEAMCMD_DIR
        target_exe = DEFAULT_STEAMCMD_EXE
        target_dir.mkdir(parents=True, exist_ok=True)
        last_error = None

        for url in STEAMCMD_ZIP_URLS:
            try:
                self.log(f"[SteamCMD] Скачивание: {url}")
                with self.session.get(url, timeout=(15, 90)) as response:
                    response.raise_for_status()
                    content = response.content
                if len(content) > 100 * 1024 * 1024:
                    raise ValueError("Архив SteamCMD неожиданно большой.")
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    safe_extract_zip(archive, target_dir, max_unpacked_bytes=300 * 1024 * 1024)
                if target_exe.is_file():
                    break
                raise FileNotFoundError("В архиве не найден steamcmd.exe")
            except Exception as exc:
                last_error = exc
                self.log(f"[SteamCMD] Не удалось скачать/распаковать ({exc}); пробую зеркало...")
        else:
            raise RuntimeError(f"Не удалось установить SteamCMD: {last_error}")

        self._set_steamcmd(target_exe)
        self.log("[SteamCMD] Первый запуск: обновление клиента...")
        return_code = self._run_steamcmd(["+quit"], timeout=300)
        if return_code != 0:
            raise RuntimeError(f"Первый запуск SteamCMD завершился с кодом {return_code}.")
        self.log(f"[SteamCMD] Готов: {target_exe}")
        return target_exe
    _libs_cache = None

    @classmethod
    def find_steam_libraries(cls) -> list:
        if cls._libs_cache is None:
            cls._libs_cache = cls._scan_steam_libraries()
        return cls._libs_cache

    @staticmethod
    def _scan_steam_libraries() -> list:
        """Папки библиотек Steam (клиент Steam, если установлен)."""
        roots = []
        if os.name == "nt":
            try:
                import winreg
                for hive, key, val in (
                    (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                ):
                    try:
                        with winreg.OpenKey(hive, key) as k:
                            roots.append(Path(winreg.QueryValueEx(k, val)[0]))
                    except OSError:
                        pass
            except ImportError:
                pass
        else:
            roots += [Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"]

        libs, seen = [], set()

        def add(path: Path):
            key = str(path).lower()
            if key not in seen and path.exists():
                seen.add(key)
                libs.append(path)

        for root in roots:
            add(root)
            vdf = root / "steamapps" / "libraryfolders.vdf"
            if vdf.exists():
                text = vdf.read_text(encoding="utf-8", errors="ignore")
                found_paths = re.findall(r'"path"\s+"([^"]+)"', text)
                # Старый формат libraryfolders.vdf: "1" "D:\SteamLibrary".
                found_paths += re.findall(r'"\d+"\s+"([A-Za-z]:\\[^" ]+)"', text)
                for library_path in found_paths:
                    add(Path(library_path.replace("\\\\", "\\")))
        return libs

    @classmethod
    def detect_game_dir(cls):
        for lib in cls.find_steam_libraries():
            cand = lib / "steamapps" / "common" / "Barotrauma"
            if (cand / "Barotrauma.dll").exists() or (cand / "Barotrauma.exe").exists():
                return cand
        return None

    @staticmethod
    def _dir_has_files(path: Path) -> bool:
        try:
            return path.is_dir() and any(path.iterdir())
        except OSError:
            return False

    def _steam_client_source(self, mod_id: str):
        # Barotrauma хранит подписанные Workshop-моды в AppData, а не только
        # в стандартной steamapps/workshop/content папке.
        candidates = [self.user_workshop_dir / str(mod_id)]
        for library in self.find_steam_libraries():
            candidates.append(
                library / "steamapps" / "workshop" / "content" / BAROTRAUMA_APP_ID / str(mod_id)
            )
        for candidate in candidates:
            root = find_filelist_root(candidate) if candidate.is_dir() else None
            if root:
                return root
        return None
    def _find_source(self, mod_id: str):
        source = self._steam_client_source(mod_id)
        if source:
            return source
        candidate = self.workshop_content_dir / str(mod_id)
        return find_filelist_root(candidate) if candidate.is_dir() else None
    def _resolve_luacs_patch_url(self) -> str:
        try:
            with self.session.head(LUACS_DIRECT_PATCH_URL, allow_redirects=True, timeout=10) as head_resp:
                if head_resp.status_code == 200:
                    return LUACS_DIRECT_PATCH_URL
        except Exception:
            pass

        with self.session.get(LUACS_GITHUB_API_RELEASES, timeout=15) as resp:
            resp.raise_for_status()
            releases = resp.json()
        for release in releases:
            for asset in release.get("assets", []):
                name = asset.get("name", "").lower()
                if "patch_windows_client" in name and name.endswith(".zip"):
                    return asset["browser_download_url"]
        raise RuntimeError("Не удалось найти архив luacsforbarotrauma_patch_windows_client.zip на GitHub.")

    def install_luacs_client_patch(self):
        if not self.game_dir.is_dir() or not (self.game_dir / "Barotrauma.dll").is_file():
            raise FileNotFoundError("В выбранной папке игры не найден Barotrauma.dll!")
        if self.backup_luacs_dir.exists():
            raise RuntimeError("Бэкап LuaCs уже существует. Сначала откатите предыдущий патч.")

        patch_url = self._resolve_luacs_patch_url()
        self.log(f"[LuaCs] Скачивание клиентского патча: {patch_url}")
        archive_bytes = bytearray()
        with self.session.get(patch_url, stream=True, timeout=(15, 90)) as response:
            response.raise_for_status()
            declared_size = int(response.headers.get("Content-Length", "0") or 0)
            if declared_size > 512 * 1024 * 1024:
                raise ValueError("Архив LuaCs превышает лимит 512 МБ.")
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    archive_bytes.extend(block)
                    if len(archive_bytes) > 512 * 1024 * 1024:
                        raise ValueError("Архив LuaCs превышает лимит 512 МБ.")

        with tempfile.TemporaryDirectory(prefix="baro-luacs-") as tmp_name:
            unpack_dir = Path(tmp_name) / "unpacked"
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                safe_extract_zip(archive, unpack_dir, max_unpacked_bytes=1024 * 1024 * 1024)

            files = [path for path in unpack_dir.rglob("*") if path.is_file()]
            if not files:
                raise RuntimeError("Архив LuaCs пуст.")

            backup_root = self.backup_luacs_dir
            backup_root.mkdir(parents=True, exist_ok=False)
            manifest = {"version": 1, "backed_up": [], "installed": []}
            try:
                for patch_file in files:
                    relative = patch_file.relative_to(unpack_dir)
                    target = self.game_dir / relative
                    # Не допускаем выход из каталога игры даже при нестандартных путях.
                    if os.path.commonpath((str(self.game_dir.resolve()), str(target.resolve()))) != str(self.game_dir.resolve()):
                        raise ValueError(f"Путь патча выходит за каталог игры: {relative}")
                    if target.is_file():
                        backup_file = backup_root / relative
                        backup_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_file)
                        manifest["backed_up"].append(relative.as_posix())
                    manifest["installed"].append(relative.as_posix())

                (backup_root / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                for patch_file in files:
                    relative = patch_file.relative_to(unpack_dir)
                    target = self.game_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(patch_file, target)
            except Exception:
                # Best-effort rollback if copying the patch fails midway.
                for relative_name in reversed(manifest["installed"]):
                    target = self.game_dir / Path(relative_name)
                    backup_file = backup_root / Path(relative_name)
                    if backup_file.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_file, target)
                    elif target.is_file():
                        target.unlink()
                shutil.rmtree(backup_root, ignore_errors=True)
                raise

        self.log(f"[LuaCs] Патч установлен. Сохранён бэкап {len(manifest['backed_up'])} файлов.")
    def restore_luacs_backup(self):
        if not self.backup_luacs_dir.is_dir():
            raise FileNotFoundError("Бэкап _vanilla_luacs_backup не найден в папке игры!")
        manifest_path = self.backup_luacs_dir / "manifest.json"
        if not manifest_path.is_file():
            # Совместимость с бэкапами старой версии менеджера.
            restored = 0
            for backup_file in self.backup_luacs_dir.rglob("*"):
                if backup_file.is_file():
                    relative = backup_file.relative_to(self.backup_luacs_dir)
                    if relative.name == "manifest.json":
                        continue
                    target_file = self.game_dir / relative
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, target_file)
                    restored += 1
            shutil.rmtree(self.backup_luacs_dir, ignore_errors=True)
            self.log(f"[LuaCs] Восстановлено файлов из старого бэкапа: {restored}. Новые файлы старого патча могли остаться.")
            return

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        restored = 0
        removed = 0
        for relative_name in reversed(manifest.get("installed", [])):
            normalized = str(relative_name).replace("\\", "/")
            relative_posix = PurePosixPath(normalized)
            if (relative_posix.is_absolute() or ".." in relative_posix.parts
                    or (relative_posix.parts and ":" in relative_posix.parts[0])):
                raise ValueError(f"Некорректный путь в manifest LuaCs: {relative_name}")
            relative = Path(*relative_posix.parts)
            target = self.game_dir / relative
            backup_file = self.backup_luacs_dir / relative
            game_root = self.game_dir.resolve()
            if os.path.commonpath((str(game_root), str(target.resolve()))) != str(game_root):
                raise ValueError(f"Путь manifest выходит за каталог игры: {relative_name}")
            if backup_file.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, target)
                restored += 1
            elif target.is_file():
                target.unlink()
                removed += 1
        shutil.rmtree(self.backup_luacs_dir, ignore_errors=True)
        self.log(f"[LuaCs] Откат завершён: восстановлено {restored}, удалено новых файлов патча {removed}.")
    def extract_steam_id(self, url_or_id: str) -> str:
        value = str(url_or_id).strip()
        if value.isdigit() and 5 <= len(value) <= 20:
            return value
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host not in {"steamcommunity.com", "www.steamcommunity.com"}:
            raise ValueError("Вставьте числовой ID или ссылку steamcommunity.com на Workshop.")
        if not parsed.path.rstrip("/").endswith("/sharedfiles/filedetails"):
            raise ValueError("Это не ссылка на элемент Steam Workshop.")
        ids = parse_qs(parsed.query).get("id", [])
        if not ids or not ids[0].isdigit() or not (5 <= len(ids[0]) <= 20):
            raise ValueError("В ссылке не найден корректный Workshop ID.")
        return ids[0]
    def fetch_collection_items(self, collection_id: str, _depth: int = 0) -> list[str]:
        """Expand nested Steam collections without loops or duplicate IDs."""
        collection_id = self.extract_steam_id(collection_id)
        visited_collections: set[str] = set()
        result: list[str] = []

        def visit(current_id: str, depth: int):
            if current_id in visited_collections:
                return
            visited_collections.add(current_id)
            if depth > MAX_COLLECTION_DEPTH:
                self.log(f"[Steam API] Превышена глубина вложенности коллекций у {current_id}.")
                return

            url = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/"
            payload = {"collectioncount": 1, "publishedfileids[0]": current_id}
            response = self.session.post(url, data=payload, timeout=(10, 40))
            response.raise_for_status()
            data = response.json().get("response", {}).get("collectiondetails", [])
            if not data:
                result.append(current_id)
                return
            entry = data[0]
            children = entry.get("children")
            if not isinstance(children, list):
                # Вставили ссылку на одиночный мод, а не на коллекцию.
                result.append(current_id)
                return

            for child in children:
                child_id = str(child.get("publishedfileid", ""))
                if not child_id.isdigit():
                    continue
                # Steam EPublishedFileType: 2 == Collection.
                if int(child.get("filetype", -1)) == 2:
                    visit(child_id, depth + 1)
                else:
                    result.append(child_id)

        visit(collection_id, _depth)
        return list(dict.fromkeys(result))
    def fetch_mods_metadata(self, mod_ids: list[str]) -> dict[str, dict]:
        """Fetch public Workshop metadata in Steam's supported 100-item batches."""
        ids = list(dict.fromkeys(str(mid) for mid in mod_ids if str(mid).isdigit()))
        if not ids:
            return {}
        url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
        metadata: dict[str, dict] = {}

        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            payload = {"itemcount": len(chunk)}
            payload.update({f"publishedfileids[{index}]": mid for index, mid in enumerate(chunk)})
            response = self.session.post(url, data=payload, timeout=(10, 45))
            response.raise_for_status()
            body = response.json().get("response", {})
            items = body.get("publishedfiledetails", [])
            seen = set()

            for item in items:
                mid = str(item.get("publishedfileid", ""))
                if not mid.isdigit():
                    continue
                seen.add(mid)
                result_code = int(item.get("result", 0) or 0)
                consumer_app_id = int(item.get("consumer_app_id", 0) or 0)
                available = result_code == 1 and consumer_app_id in (0, int(BAROTRAUMA_APP_ID))
                title = item.get("title") or f"Workshop_{mid}"
                if consumer_app_id not in (0, int(BAROTRAUMA_APP_ID)):
                    error = f"Элемент относится к AppID {consumer_app_id}, а не к Barotrauma."
                elif result_code != 1:
                    error = f"Steam вернул код результата {result_code}."
                else:
                    error = ""

                tags_data = item.get("tags") or []
                tags = [str(tag.get("tag", "")) for tag in tags_data if isinstance(tag, dict) and tag.get("tag")]
                metadata[mid] = {
                    "id": mid,
                    "title": title,
                    "description": item.get("description", ""),
                    "preview_url": item.get("preview_url", ""),
                    "file_url": item.get("file_url", ""),
                    "file_size": int(item.get("file_size", 0) or 0),
                    "time_updated": int(item.get("time_updated", 0) or 0),
                    "subscriptions": int(item.get("subscriptions", 0) or 0),
                    "creator": str(item.get("creator", "")),
                    "tags": tags,
                    # GetPublishedFileDetails.children describes collection contents;
                    # it is not a reliable required-mod dependency list.
                    "dependencies": [],
                    "available": available,
                    "api_error": error,
                }

            for mid in chunk:
                if mid not in seen:
                    metadata[mid] = {
                        "id": mid,
                        "title": f"Недоступный мод {mid}",
                        "dependencies": [],
                        "available": False,
                        "api_error": "Steam не вернул сведения об элементе.",
                        "time_updated": 0,
                    }
        return metadata
    def query_workshop(
        self, search_text: str, api_key: str, sort_mode: str = "relevance",
        cursor: str = "*", per_page: int = 24, content_type: str = "items",
    ) -> tuple[list[dict], str | None]:
        """Search Barotrauma Workshop items or collections with QueryFiles."""
        key = str(api_key or "").strip()
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", key):
            raise ValueError("Steam Web API key должен состоять из 32 шестнадцатеричных символов.")

        search_text = str(search_text or "").strip()[:120]
        sort_types = {
            "relevance": 12,  # RankedByTextSearch
            "popular": 9,     # RankedByTotalUniqueSubscriptions
            "rated": 0,       # RankedByVote
            "newest": 1,      # RankedByPublicationDate
            "updated": 21,    # RankedByLastUpdatedDate
        }
        query_type = sort_types.get(sort_mode, 9)
        content_type = str(content_type or "items").strip().lower()
        if content_type not in {"items", "collections"}:
            raise ValueError("Неизвестный тип Workshop-каталога.")
        matching_file_type = 1 if content_type == "collections" else 0
        payload = {
            "query_type": query_type,
            "page": 1,
            "cursor": cursor or "*",
            "numperpage": max(1, min(int(per_page), 50)),
            "creator_appid": int(BAROTRAUMA_APP_ID),
            "appid": int(BAROTRAUMA_APP_ID),
            "requiredtags": [],
            "excludedtags": [],
            "match_all_tags": False,
            "required_flags": [],
            "omitted_flags": [],
            "search_text": search_text,
            "filetype": matching_file_type,
            "child_publishedfileid": "0",
            "days": 7,
            "include_recent_votes_only": False,
            "cache_max_age_seconds": 60,
            "language": 0,
            "required_kv_tags": [],
            "totalonly": False,
            "ids_only": False,
            "return_vote_data": True,
            "return_tags": True,
            "return_kv_tags": False,
            "return_previews": True,
            "return_children": content_type == "collections",
            "return_short_description": True,
            "return_for_sale_data": False,
            "return_metadata": False,
            "return_playtime_stats": 0,
        }
        endpoint = "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/"
        response = self.session.get(
            endpoint,
            params={"key": key, "input_json": json.dumps(payload, ensure_ascii=False)},
            timeout=(12, 45),
        )
        if response.status_code in (401, 403):
            raise RuntimeError(
                "Steam отклонил API key. Проверь ключ на https://steamcommunity.com/dev/apikey и повтори попытку."
            )
        if response.status_code == 429:
            raise RuntimeError("Steam временно ограничил частоту запросов. Повтори поиск через минуту.")
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("Steam вернул некорректный JSON при поиске Workshop.") from exc
        api_response = body.get("response", {}) if isinstance(body, dict) else {}
        if not isinstance(api_response, dict):
            raise RuntimeError("Steam QueryFiles вернул неожиданный формат ответа.")
        result_code = api_response.get("result")
        if result_code not in (None, 1, "1"):
            message = api_response.get("message") or f"код {result_code}"
            raise RuntimeError(f"Steam API отклонил запрос: {message}")

        raw_items = api_response.get("publishedfiledetails", [])
        if not isinstance(raw_items, list):
            raise RuntimeError("Steam API не вернул список Workshop-элементов.")

        normalized: list[dict] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("publishedfileid", ""))
            if not mid.isdigit():
                continue
            try:
                consumer_app = int(item.get("consumer_app_id", BAROTRAUMA_APP_ID) or BAROTRAUMA_APP_ID)
            except (TypeError, ValueError):
                consumer_app = 0
            if consumer_app not in (0, int(BAROTRAUMA_APP_ID)):
                continue
            previews = item.get("previews") or []
            preview_url = str(item.get("preview_url", "") or "")
            if not preview_url and previews and isinstance(previews[0], dict):
                preview_url = str(previews[0].get("url") or previews[0].get("preview_url") or "")
            vote_data = item.get("vote_data") if isinstance(item.get("vote_data"), dict) else {}
            tags = item.get("tags") or []
            raw_children = item.get("children") or []
            child_ids = []
            if isinstance(raw_children, list):
                for child in raw_children:
                    child_id = child.get("publishedfileid") if isinstance(child, dict) else child
                    child_id = str(child_id or "")
                    if child_id.isdigit():
                        child_ids.append(child_id)
            normalized.append({
                "id": mid,
                "title": str(item.get("title") or f"Workshop {mid}").replace("\n", " ").strip(),
                "description": str(item.get("short_description") or item.get("file_description") or item.get("description") or "").strip(),
                "preview_url": preview_url,
                "time_updated": int(item.get("time_updated", 0) or 0),
                "subscriptions": int(item.get("subscriptions", item.get("lifetime_subscriptions", 0)) or 0),
                "file_size": int(item.get("file_size", 0) or 0),
                "creator": str(item.get("creator", "") or ""),
                "tags": [str(tag.get("tag", "")) for tag in tags if isinstance(tag, dict) and tag.get("tag")],
                "votes_up": int(vote_data.get("votes_up", item.get("votes_up", 0)) or 0),
                "votes_down": int(vote_data.get("votes_down", item.get("votes_down", 0)) or 0),
                "file_url": str(item.get("file_url", "") or ""),
                "time_created": int(item.get("time_created", 0) or 0),
                "update_status": "OK" if find_filelist_root(self.local_mods_dir / mid) else "MISSING",
                "dependencies": [],
                "is_collection": content_type == "collections",
                "children": list(dict.fromkeys(child_ids)),
                "children_count": len(set(child_ids)),
            })
        next_cursor = api_response.get("next_cursor")
        next_cursor_text = str(next_cursor).strip() if isinstance(next_cursor, (str, int)) else ""
        # Steam may echo the cursor on the last page; some responses use "*"
        # as the terminal marker after the first page. Never restart page 1.
        if (
            not next_cursor_text
            or next_cursor_text == str(cursor)
            or (next_cursor_text == "*" and str(cursor) != "*")
        ):
            next_cursor = None
        else:
            next_cursor = next_cursor_text
        return normalized, str(next_cursor) if next_cursor else None

    def resolve_all_dependencies(self, initial_ids: list[str]) -> dict[str, dict]:
        """Resolve dependency IDs only when the metadata source explicitly exposes them.

        Steam's public GetPublishedFileDetails endpoint does not expose Barotrauma's
        required-item list consistently. Collection children are resolved separately
        by fetch_collection_items and are deliberately not mislabeled as dependencies.
        """
        all_meta: dict[str, dict] = {}
        pending = list(dict.fromkeys(str(mid) for mid in initial_ids if str(mid).isdigit()))
        while pending:
            batch = [mid for mid in pending if mid not in all_meta]
            if not batch:
                break
            fetched = self.fetch_mods_metadata(batch)
            all_meta.update(fetched)
            next_batch = []
            for item in fetched.values():
                for dependency_id in item.get("dependencies", []):
                    dependency_id = str(dependency_id)
                    if dependency_id.isdigit() and dependency_id not in all_meta:
                        next_batch.append(dependency_id)
            pending = list(dict.fromkeys(next_batch))
        return all_meta
    def get_local_mod_timestamp(self, mod_id: str, installed_versions: dict) -> int:
        filelist = self.local_mods_dir / str(mod_id) / "filelist.xml"
        if not filelist.is_file():
            return 0
        try:
            recorded = int(installed_versions.get(str(mod_id), 0) or 0)
        except (TypeError, ValueError):
            recorded = 0
        # mtime reflects the local extraction/copy time, not Steam time_updated.
        # Without a recorded Steam timestamp, update status must remain UNKNOWN.
        return recorded if recorded > 0 else 0
    def check_updates_for_mods(self, mods_list: list[dict], installed_versions: dict) -> tuple[list[dict], list[dict]]:
        mod_ids = [str(mod.get("id", "")) for mod in mods_list if str(mod.get("id", "")).isdigit()]
        fresh_meta = self.fetch_mods_metadata(mod_ids)
        updated_mods = []
        outdated = []

        for mod in mods_list:
            mid = str(mod.get("id", ""))
            item = dict(mod)
            remote = fresh_meta.get(mid, {})
            if remote.get("title"):
                item["title"] = remote["title"]
            for key in ("file_url", "file_size", "preview_url", "description", "subscriptions", "tags"):
                if key in remote:
                    item[key] = remote[key]
            remote_time = int(remote.get("time_updated", 0) or 0)
            local_time = self.get_local_mod_timestamp(mid, installed_versions)
            item["time_updated"] = remote_time or int(item.get("time_updated", 0) or 0)

            local_filelist = self.local_mods_dir / mid / "filelist.xml"
            if not local_filelist.is_file():
                item["update_status"] = "MISSING"
                item["api_error"] = remote.get("api_error", "")
                outdated.append(item)
            elif not remote.get("available", True):
                item["update_status"] = "UNKNOWN"
                item["api_error"] = remote.get("api_error", "Данные Workshop недоступны.")
            elif not remote_time or not local_time:
                item["update_status"] = "UNKNOWN"
            elif remote_time > local_time:
                item["update_status"] = "OUTDATED"
                outdated.append(item)
            else:
                item["update_status"] = "OK"
            updated_mods.append(item)
        return updated_mods, outdated
    def _download_mods_steamcmd_sequential(
        self, mod_ids: list[str], attempts: int = 3, force_refresh: bool = False
    ) -> dict[str, str]:
        """Download with one SteamCMD process to avoid shared-directory races.

        Earlier versions launched up to 16 SteamCMD instances against the same
        steamapps/config/cache directories. They could overwrite each other's state
        and produce incomplete workshop folders.
        """
        ids = list(dict.fromkeys(str(mid) for mid in mod_ids if str(mid).isdigit()))
        if not ids:
            return {}
        self.ensure_steamcmd_installed()
        self.workshop_content_dir.mkdir(parents=True, exist_ok=True)

        def has_valid_source(mid: str) -> bool:
            if force_refresh:
                # При обновлении проверяем именно кеш SteamCMD: старая копия
                # из Steam Client не должна маскировать неудачную загрузку.
                return find_filelist_root(self.workshop_content_dir / mid) is not None
            return self._find_source(mid) is not None

        pending = list(ids) if force_refresh else [mid for mid in ids if not has_valid_source(mid)]
        reasons: dict[str, str] = {}
        total = len(pending)
        if not pending:
            return {}

        for attempt in range(1, max(1, attempts) + 1):
            if not pending:
                break
            self.log(f"[SteamCMD] Попытка {attempt}/{attempts}: {len(pending)} модов, один процесс.")
            args = ["+@NoPromptForPassword", "1", "+login", "anonymous"]
            for mid in pending:
                args += ["+workshop_download_item", BAROTRAUMA_APP_ID, mid]
            args.append("+quit")

            failed_lines: dict[str, str] = {}
            last_progress: dict[str, int] = {}
            completed_lines = set()

            def on_line(line: str):
                match = re.search(r"Success\. Downloaded item (\d+)", line, re.IGNORECASE)
                if match:
                    mid = match.group(1)
                    completed_lines.add(mid)
                    self.log(f"  [SteamCMD] ✓ Получен ответ Steam для {mid}; проверяю filelist.xml...")
                    return
                match = re.search(r"Download item (\d+) failed \((.*?)\)", line, re.IGNORECASE)
                if match:
                    failed_lines[match.group(1)] = match.group(2).strip()
                    self.log(f"  [SteamCMD] ✗ {match.group(1)}: {match.group(2).strip()}")
                    return
                match = re.search(r"progress: ([\d.]+)", line, re.IGNORECASE)
                if match:
                    percent = max(0, min(100, int(float(match.group(1)))))
                    # Не заспамливаем лог одинаковыми значениями прогресса.
                    key = pending[0] if len(pending) == 1 else "batch"
                    step = percent // 25
                    if step > last_progress.get(key, -1):
                        last_progress[key] = step
                        self.log(f"  [SteamCMD] Загрузка: {percent}%")

            timeout = max(900, min(4 * 60 * 60, len(pending) * 120))
            try:
                return_code = self._run_steamcmd(args, on_line, timeout=timeout)
                if return_code != 0:
                    self.log(f"[SteamCMD] Процесс завершился с кодом {return_code}.")
            except Exception as exc:
                self.log(f"[SteamCMD] Ошибка процесса: {exc}")
                for mid in pending:
                    reasons[mid] = str(exc)

            remaining = []
            for mid in pending:
                reported_success = mid in completed_lines
                if has_valid_source(mid) and (not force_refresh or reported_success):
                    reasons.pop(mid, None)
                    self.log(f"  [SteamCMD] ✓ {mid}: файл filelist.xml найден.")
                else:
                    reason = failed_lines.get(mid) or reasons.get(mid) or "SteamCMD не создал корректный filelist.xml"
                    reasons[mid] = reason
                    remaining.append(mid)
            pending = remaining

            if pending:
                hard_errors = ("access denied", "no subscription", "file not found", "private")
                if all(any(term in reasons.get(mid, "").lower() for term in hard_errors) for mid in pending):
                    self.log("[SteamCMD] Остались элементы без доступа; дальнейшие повторы не помогут.")
                    break
                if attempt < attempts:
                    time.sleep(min(2 ** attempt, 8))

        return {mid: reasons.get(mid, "Не удалось получить корректные файлы мода.") for mid in pending}

    @staticmethod
    def _remove_path_entry(path: Path) -> None:
        """Remove a file, directory, symlink or Windows junction without following links."""
        try:
            is_link = path.is_symlink() or bool(
                getattr(path.lstat(), "st_file_attributes", 0) & 0x400
            )
        except OSError:
            is_link = False
        if is_link:
            try:
                os.rmdir(path)
            except OSError:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def _prepare_isolated_steamcmd_worker(self, worker_index: int) -> tuple[Path, Path]:
        """Create a private SteamCMD installation/cache for one parallel worker."""
        source_exe = self.steamcmd_path.resolve()
        source_root = source_exe.parent
        if not source_exe.is_file():
            raise FileNotFoundError(f"Не найден SteamCMD: {source_exe}")

        source_stat = source_exe.stat()
        signature = f"{source_exe}|{source_stat.st_size}|{source_stat.st_mtime_ns}"
        workers_root = APP_DATA_DIR / "steamcmd_workers"
        worker_root = workers_root / f"worker_{worker_index + 1}"
        worker_exe = worker_root / source_exe.name
        stamp_file = worker_root / ".manager_source_stamp"
        workers_root.mkdir(parents=True, exist_ok=True)

        try:
            if worker_exe.is_file() and stamp_file.read_text(encoding="utf-8") == signature:
                return worker_exe, worker_root
        except OSError:
            pass

        staging_parent = Path(tempfile.mkdtemp(prefix=f"steamcmd-worker-{worker_index + 1}-", dir=workers_root))
        staging_root = staging_parent / "install"

        def ignore_mutable_state(directory: str, names: list[str]):
            # SteamCMD's package/runtime files are copied. Its cache, settings and
            # downloaded Workshop content are deliberately not shared between workers.
            if Path(directory).resolve() == source_root:
                excluded = {"steamapps", "logs", "dumps", "appcache", "userdata", "config"}
                return {name for name in names if name.casefold() in excluded}
            return set()

        try:
            shutil.copytree(
                source_root, staging_root,
                ignore=ignore_mutable_state,
                symlinks=True,
            )
            staged_exe = staging_root / source_exe.name
            if not staged_exe.is_file():
                raise RuntimeError("Не удалось скопировать исполняемый файл SteamCMD в изолированный каталог.")
            (staging_root / ".manager_source_stamp").write_text(signature, encoding="utf-8")

            old_root = workers_root / f"worker_{worker_index + 1}.previous"
            self._remove_path_entry(old_root)
            if worker_root.exists():
                os.replace(worker_root, old_root)
            try:
                os.replace(staging_root, worker_root)
            except Exception:
                if old_root.exists() and not worker_root.exists():
                    os.replace(old_root, worker_root)
                raise
            self._remove_path_entry(old_root)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

        worker_exe = worker_root / source_exe.name
        if not worker_exe.is_file():
            raise RuntimeError("Изолированный SteamCMD не прошёл проверку после копирования.")
        return worker_exe, worker_root

    @staticmethod
    def _run_isolated_steamcmd(executable: Path, args, on_line=None, timeout: int = 1800) -> int:
        """Run a SteamCMD clone whose working/cache directory is not shared."""
        command = [str(executable)] + list(args)
        proc = subprocess.Popen(
            command,
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        reader_errors = []

        def read_output():
            try:
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        line = raw_line.strip()
                        if line and on_line:
                            on_line(line)
            except Exception as exc:
                reader_errors.append(exc)

        reader = threading.Thread(target=read_output, name="SteamCMD-isolated-output", daemon=True)
        reader.start()
        try:
            return_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait(timeout=10)
            reader.join(timeout=5)
            raise TimeoutError(f"SteamCMD превысил лимит времени ({timeout} сек.) и был остановлен.") from exc
        finally:
            reader.join(timeout=10)
            if proc.stdout:
                proc.stdout.close()
        if reader_errors:
            raise RuntimeError(f"Ошибка чтения вывода SteamCMD: {reader_errors[0]}")
        return int(return_code)

    def _publish_worker_package(self, mod_id: str, package_root: Path) -> None:
        """Stage a verified worker package, then atomically replace the central cache."""
        self.workshop_content_dir.mkdir(parents=True, exist_ok=True)
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{mod_id}.stage-", dir=self.workshop_content_dir))
        staged_package = staging_parent / "package"
        destination = self.workshop_content_dir / mod_id
        backup = self.workshop_content_dir / f".{mod_id}.previous"
        moved_old = False
        try:
            shutil.copytree(package_root, staged_package)
            if not find_filelist_root(staged_package):
                raise RuntimeError(f"Пакет {mod_id} из изолированного SteamCMD не содержит filelist.xml.")
            self._remove_path_entry(backup)
            if destination.exists() or destination.is_symlink() or self._is_link(destination):
                os.replace(destination, backup)
                moved_old = True
            try:
                os.replace(staged_package, destination)
            except Exception:
                if moved_old and backup.exists() and not destination.exists():
                    os.replace(backup, destination)
                raise
            if moved_old:
                self._remove_path_entry(backup)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    def _download_mods_steamcmd_isolated(
        self, mod_ids: list[str], attempts: int, force_refresh: bool, worker_count: int,
        size_hints: dict[str, int] | None = None,
    ) -> dict[str, str]:
        ids = list(dict.fromkeys(str(mid) for mid in mod_ids if str(mid).isdigit()))
        if not ids:
            return {}
        self.ensure_steamcmd_installed()
        self.workshop_content_dir.mkdir(parents=True, exist_ok=True)

        workers = [self._prepare_isolated_steamcmd_worker(index) for index in range(worker_count)]
        worker_content_dirs = [
            root / "steamapps" / "workshop" / "content" / BAROTRAUMA_APP_ID
            for _exe, root in workers
        ]

        pending = list(ids) if force_refresh else [mid for mid in ids if not self._find_source(mid)]
        reasons: dict[str, str] = {}
        if not pending:
            return {}

        # Reuse a verified item cache from one of the isolated workers, if present.
        if not force_refresh:
            still_pending = []
            for mid in pending:
                cached = None
                for content_dir in worker_content_dirs:
                    candidate = find_filelist_root(content_dir / mid)
                    if candidate:
                        cached = candidate
                        break
                if cached:
                    try:
                        self._publish_worker_package(mid, cached)
                        self.log(f"  [SteamCMD] ✓ {mid}: повторно использован изолированный кэш.")
                    except Exception as exc:
                        reasons[mid] = str(exc)
                        still_pending.append(mid)
                else:
                    still_pending.append(mid)
            pending = still_pending

        for attempt in range(1, max(1, attempts) + 1):
            if not pending:
                break
            partitions = [[] for _ in range(worker_count)]
            partition_loads = [0] * worker_count
            known_sizes = {
                str(mid): max(0, int(size))
                for mid, size in (size_hints or {}).items()
                if str(mid).isdigit()
            }
            # Largest known files go first to the currently lightest worker.
            # Unknown sizes count as one item, so they are still distributed.
            ordered_pending = sorted(
                pending,
                key=lambda mid: (-known_sizes.get(mid, 0), mid),
            )
            for mid in ordered_pending:
                worker_index = min(
                    range(worker_count),
                    key=lambda index: (partition_loads[index], len(partitions[index])),
                )
                partitions[worker_index].append(mid)
                partition_loads[worker_index] += known_sizes.get(mid, 1) or 1
            partitions = [(index, chunk) for index, chunk in enumerate(partitions) if chunk]
            self.log(
                f"[SteamCMD] Параллельная загрузка: {len(pending)} модов, "
                f"{len(partitions)} изолированных загрузчика (попытка {attempt}/{attempts})."
            )

            def run_partition(worker_index: int, chunk: list[str]):
                executable, worker_root = workers[worker_index]
                content_dir = worker_content_dirs[worker_index]
                content_dir.mkdir(parents=True, exist_ok=True)
                for mid in chunk:
                    partial = content_dir / mid
                    if partial.exists() or partial.is_symlink():
                        self._remove_path_entry(partial)
                args = ["+@NoPromptForPassword", "1", "+login", "anonymous"]
                for mid in chunk:
                    args += ["+workshop_download_item", BAROTRAUMA_APP_ID, mid]
                args.append("+quit")
                completed: set[str] = set()
                failed_lines: dict[str, str] = {}
                last_progress = -1

                def on_line(line: str):
                    nonlocal last_progress
                    match = re.search(r"Success\. Downloaded item (\d+)", line, re.IGNORECASE)
                    if match:
                        completed.add(match.group(1))
                        self.log(f"  [SteamCMD #{worker_index + 1}] ✓ {match.group(1)}: ответ получен.")
                        return
                    match = re.search(r"Download item (\d+) failed \((.*?)\)", line, re.IGNORECASE)
                    if match:
                        failed_lines[match.group(1)] = match.group(2).strip()
                        self.log(f"  [SteamCMD #{worker_index + 1}] ✗ {match.group(1)}: {match.group(2).strip()}")
                        return
                    progress = re.search(r"progress: ([\d.]+)", line, re.IGNORECASE)
                    if progress:
                        percent = max(0, min(100, int(float(progress.group(1)))))
                        step = percent // 25
                        if step > last_progress:
                            last_progress = step
                            self.log(f"  [SteamCMD #{worker_index + 1}] Загрузка партии: {percent}%")

                timeout = max(900, min(4 * 60 * 60, len(chunk) * 150))
                error = ""
                return_code = -1
                try:
                    return_code = self._run_isolated_steamcmd(executable, args, on_line, timeout)
                    if return_code != 0:
                        error = f"SteamCMD завершился с кодом {return_code}."
                except Exception as exc:
                    error = str(exc)
                return completed, failed_lines, error

            with ThreadPoolExecutor(max_workers=len(partitions)) as executor:
                futures = {
                    executor.submit(run_partition, index, chunk): (index, chunk)
                    for index, chunk in partitions
                }
                partition_results = {}
                for future, (index, chunk) in futures.items():
                    try:
                        partition_results[index] = future.result()
                    except Exception as exc:
                        partition_results[index] = (set(), {}, str(exc))

            remaining = []
            for worker_index, chunk in partitions:
                completed, failed_lines, process_error = partition_results[worker_index]
                content_dir = worker_content_dirs[worker_index]
                for mid in chunk:
                    source = find_filelist_root(content_dir / mid)
                    reported_success = mid in completed
                    if source and (not force_refresh or reported_success):
                        try:
                            self._publish_worker_package(mid, source)
                            reasons.pop(mid, None)
                            self.log(f"  [SteamCMD] ✓ {mid}: проверен filelist.xml и перенесён в основной кэш.")
                            continue
                        except Exception as exc:
                            reason = str(exc)
                    else:
                        reason = failed_lines.get(mid) or process_error or "SteamCMD не создал корректный filelist.xml."
                    reasons[mid] = reason
                    remaining.append(mid)
            pending = remaining

            if pending:
                hard_errors = ("access denied", "no subscription", "file not found", "private")
                if all(any(term in reasons.get(mid, "").lower() for term in hard_errors) for mid in pending):
                    self.log("[SteamCMD] Остались элементы без доступа; дальнейшие повторы не помогут.")
                    break
                if attempt < attempts:
                    time.sleep(min(2 ** attempt, 8))

        return {mid: reasons.get(mid, "Не удалось получить корректные файлы мода.") for mid in pending}

    def download_mods_steamcmd(
        self, mod_ids: list[str], attempts: int = 3, force_refresh: bool = False,
        size_hints: dict[str, int] | None = None,
    ) -> dict[str, str]:
        """Download in isolated parallel caches; fall back to the stable serial path if needed."""
        ids = list(dict.fromkeys(str(mid) for mid in mod_ids if str(mid).isdigit()))
        workers = max(1, min(3, int(getattr(self, "workers", 1) or 1)))
        if workers <= 1 or len(ids) <= 1:
            return self._download_mods_steamcmd_sequential(ids, attempts, force_refresh)
        try:
            return self._download_mods_steamcmd_isolated(
                ids, attempts, force_refresh, min(workers, len(ids)), size_hints=size_hints
            )
        except Exception as exc:
            self.log(
                f"[SteamCMD] Не удалось подготовить изолированные загрузчики ({exc}); "
                "перехожу на проверенный последовательный режим."
            )
            return self._download_mods_steamcmd_sequential(ids, attempts, force_refresh)

    def remove_mod_files(self, mod_id: str) -> int:
        """Remove this manager's LocalMods entry and private SteamCMD cache, never follow links."""
        mid = str(mod_id).strip()
        if not mid.isdigit():
            raise ValueError("Некорректный Workshop ID.")
        removed = 0
        targets = [self.local_mods_dir / mid, self.workshop_content_dir / mid]
        worker_root = APP_DATA_DIR / "steamcmd_workers"
        for index in range(1, 4):
            targets.append(
                worker_root / f"worker_{index}" / "steamapps" / "workshop" / "content" / BAROTRAUMA_APP_ID / mid
            )
        for target in targets:
            try:
                exists = target.exists() or target.is_symlink() or self._is_link(target)
                if exists:
                    self._remove_path_entry(target)
                    removed += 1
            except OSError as exc:
                raise RuntimeError(f"Не удалось удалить {target}: {exc}") from exc
        return removed

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)  # Windows junction
        except OSError:
            return False

    @staticmethod
    def _remove_link(path: Path):
        try:
            os.rmdir(path)
        except OSError:
            os.unlink(path)

    def link_to_local_mods(
        self,
        mod_ids: list[str],
        force_refresh: bool = False,
        source_overrides: dict[str, Path] | None = None,
    ):
        self.local_mods_dir.mkdir(parents=True, exist_ok=True)
        source_overrides = source_overrides or {}
        for mid in list(dict.fromkeys(str(value) for value in mod_ids)):
            if not mid.isdigit():
                self.log(f"[LocalMods] Пропущен некорректный Workshop ID: {mid}")
                continue
            src_path = source_overrides.get(mid) or self._find_source(mid)
            if not src_path:
                self.log(f"[LocalMods] {mid}: исходная папка с filelist.xml не найдена.")
                continue

            dst = self.local_mods_dir / mid
            if dst.is_symlink() or self._is_link(dst):
                try:
                    same = dst.resolve() == src_path.resolve()
                except OSError:
                    same = False
                if same:
                    continue
                self._remove_link(dst)
            elif dst.exists():
                if not force_refresh:
                    if find_filelist_root(dst):
                        continue
                    raise RuntimeError(f"{dst} существует, но не содержит корректный filelist.xml.")
                shutil.rmtree(dst)

            try:
                if os.name == "nt":
                    subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(dst), str(src_path)],
                        check=True,
                        capture_output=True,
                        text=True,
                        creationflags=CREATE_NO_WINDOW,
                    )
                else:
                    os.symlink(src_path, dst, target_is_directory=True)
            except Exception as link_error:
                try:
                    shutil.copytree(src_path, dst)
                except Exception as copy_error:
                    if dst.exists():
                        shutil.rmtree(dst, ignore_errors=True)
                    raise RuntimeError(
                        f"Не удалось подключить мод {mid}: ссылка ({link_error}); копирование ({copy_error})."
                    ) from copy_error
    def acquire_mods(
        self,
        mod_ids: list[str],
        metadata: dict[str, dict] | None = None,
        force_refresh_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, str]:
        """Acquire Workshop files using parallel trusted CDN requests, then SteamCMD."""
        ids = list(dict.fromkeys(str(mid) for mid in mod_ids if str(mid).isdigit()))
        if not ids:
            return {}
        metadata = metadata or {}
        force_ids = {str(mid) for mid in (force_refresh_ids or ()) if str(mid).isdigit()}
        pending_steamcmd: list[str] = []
        direct_candidates: list[str] = []
        direct_completed: set[str] = set()
        failed: dict[str, str] = {}

        for mid in ids:
            if mid not in force_ids and self._steam_client_source(mid):
                self.log(f"  [Steam Client] {mid}: уже загружен в WorkshopMods/Installed.")
                continue
            file_url = str(metadata.get(mid, {}).get("file_url", "") or "").strip()
            if is_trusted_steam_cdn_url(file_url):
                direct_candidates.append(mid)
            else:
                pending_steamcmd.append(mid)

        if direct_candidates:
            direct_workers = max(1, min(3, int(getattr(self, "workers", 1) or 1), len(direct_candidates)))
            self.log(f"[Steam CDN] Пробую прямое скачивание для {len(direct_candidates)} модов ({direct_workers} потока).")

            def direct_download(mid: str):
                try:
                    with requests.Session() as http:
                        http.headers.update({"User-Agent": "BarotraumaModManager/2.6"})
                        return self._download_mod_from_cdn(
                            mid, metadata.get(mid, {}), http_session=http
                        )
                except Exception as exc:
                    self.log(f"  [Steam CDN] {mid}: прямая загрузка не удалась ({exc}); перехожу к SteamCMD.")
                    return False

            with ThreadPoolExecutor(max_workers=direct_workers) as executor:
                results = list(executor.map(direct_download, direct_candidates))
            for mid, result in zip(direct_candidates, results):
                if result:
                    direct_completed.add(mid)
                else:
                    pending_steamcmd.append(mid)

        force_steamcmd = bool(force_ids.intersection(pending_steamcmd))
        size_hints = {
            mid: max(0, int(metadata.get(mid, {}).get("file_size", 0) or 0))
            for mid in pending_steamcmd
        }
        steamcmd_failed = self.download_mods_steamcmd(
            pending_steamcmd,
            force_refresh=force_steamcmd,
            size_hints=size_hints,
        ) if pending_steamcmd else {}
        failed.update(steamcmd_failed)

        successful = [mid for mid in ids if mid not in failed and self._find_source(mid)]
        prefer_fresh_cache = direct_completed | (force_ids - set(steamcmd_failed))
        source_overrides = {
            mid: package_root
            for mid in prefer_fresh_cache
            if (package_root := find_filelist_root(self.workshop_content_dir / mid)) is not None
        }
        self.link_to_local_mods(
            successful, force_refresh=True, source_overrides=source_overrides
        )

        for mid in successful:
            if not find_filelist_root(self.local_mods_dir / mid):
                failed[mid] = "Файлы получены, но не удалось подключить filelist.xml в LocalMods."
        return failed

    def _download_mod_from_cdn(self, mod_id: str, metadata: dict, http_session=None) -> bool:
        file_url = str(metadata.get("file_url", "") or "").strip()
        http = http_session or self.session
        if not is_trusted_steam_cdn_url(file_url):
            self.log(f"  [Steam CDN] {mod_id}: ссылка не относится к доверенному Steam CDN; использую SteamCMD.")
            return False

        self.workshop_content_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"baro-{mod_id}-", dir=str(self.workshop_content_dir.parent)
        ) as temp_name:
            temp_root = Path(temp_name)
            archive_path = temp_root / "workshop.zip"
            unpack_dir = temp_root / "unpacked"
            downloaded = 0
            current_url = file_url
            response = None

            # Do not let requests follow redirects before checking their hosts.
            # This prevents a Workshop metadata URL from becoming an arbitrary
            # outbound request through a malicious redirect.
            for redirect_count in range(6):
                if not is_trusted_steam_cdn_url(current_url):
                    raise ValueError("Steam CDN перенаправил запрос на недоверенный адрес.")
                response = http.get(
                    current_url, stream=True, timeout=(15, 90), allow_redirects=False
                )
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    response.close()
                    if not location:
                        raise ValueError("Steam CDN вернул редирект без адреса назначения.")
                    current_url = urljoin(current_url, location)
                    if redirect_count == 5:
                        raise ValueError("Слишком много перенаправлений Steam CDN.")
                    continue
                break

            if response is None:
                raise RuntimeError("Steam CDN не вернул ответ на загрузку.")

            with response:
                response.raise_for_status()
                declared_size = int(response.headers.get("Content-Length", "0") or 0)
                if declared_size > MAX_DIRECT_DOWNLOAD_BYTES:
                    raise ValueError("Размер файла превышает лимит прямого скачивания (2 ГБ).")
                with archive_path.open("wb") as stream:
                    for block in response.iter_content(chunk_size=1024 * 1024):
                        if not block:
                            continue
                        downloaded += len(block)
                        if downloaded > MAX_DIRECT_DOWNLOAD_BYTES:
                            raise ValueError("Файл превысил лимит прямого скачивания (2 ГБ).")
                        stream.write(block)

            try:
                with zipfile.ZipFile(archive_path) as archive:
                    safe_extract_zip(archive, unpack_dir)
            except zipfile.BadZipFile:
                return False

            package_root = find_filelist_root(unpack_dir)
            if not package_root:
                raise ValueError("Steam CDN-архив не содержит filelist.xml Barotrauma.")

            staged_dir = temp_root / "ready"
            shutil.copytree(package_root, staged_dir)
            final_dir = self.workshop_content_dir / mod_id
            old_dir = self.workshop_content_dir / f"{mod_id}.previous"
            if old_dir.exists():
                shutil.rmtree(old_dir, ignore_errors=True)
            if final_dir.exists():
                os.replace(final_dir, old_dir)
            try:
                os.replace(staged_dir, final_dir)
            except Exception:
                if old_dir.exists() and not final_dir.exists():
                    os.replace(old_dir, final_dir)
                raise
            if old_dir.exists():
                shutil.rmtree(old_dir, ignore_errors=True)
            self.log(f"  [Steam CDN] ✓ {mod_id}: загружен напрямую ({downloaded / 1048576:.1f} МБ).")
            return True

    def inspect_local_mod_xml(self, mod_id: str) -> dict:
        mod_dir = self.local_mods_dir / str(mod_id)
        filelist_path = mod_dir / "filelist.xml"
        info = {"name": f"Mod_{mod_id}", "is_submarine_only": False,
                "has_lua_or_cs": False, "has_overrides": False}
        if not filelist_path.is_file():
            return info
        try:
            root = ET.parse(filelist_path).getroot()
            info["name"] = root.attrib.get("name", info["name"])
            child_tags = [str(child.tag).lower() for child in root]
            if child_tags and all(tag in ("submarine", "enemy", "wreck", "beaconstation") for tag in child_tags):
                info["is_submarine_only"] = True
        except (ET.ParseError, OSError):
            return info

        try:
            for path in mod_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".lua", ".dll"):
                    info["has_lua_or_cs"] = True
                    break
        except OSError:
            pass

        try:
            for xml_file in mod_dir.rglob("*.xml"):
                if xml_file.name.lower() == "filelist.xml":
                    continue
                try:
                    for _, elem in ET.iterparse(xml_file, events=("start",)):
                        if str(elem.tag).lower() == "override":
                            info["has_overrides"] = True
                            break
                    if info["has_overrides"]:
                        break
                except (ET.ParseError, OSError):
                    continue
        except OSError:
            pass
        return info
    def detect_conflicts(self, mods_list: list[dict]) -> dict[str, list[tuple[str, str]]]:
        # identifier — это устойчивый ключ контента. Атрибут name часто повторяется
        # легально (имена предметов/персонажей), поэтому его нельзя считать конфликтом.
        identifier_map: dict[str, list[tuple[str, str]]] = {}
        for mod in mods_list:
            mid = str(mod.get("id", ""))
            if not mid.isdigit():
                continue
            title = mod.get("title", f"Mod_{mid}")
            mod_dir = self.local_mods_dir / mid
            if not mod_dir.is_dir():
                continue
            try:
                for xml_file in mod_dir.rglob("*.xml"):
                    if xml_file.name.lower() == "filelist.xml":
                        continue
                    try:
                        for _, elem in ET.iterparse(xml_file, events=("start",)):
                            identifier = elem.attrib.get("identifier")
                            if not identifier or len(identifier.strip()) <= 1:
                                continue
                            clean = identifier.strip().casefold()
                            owner = (title, mid)
                            owners = identifier_map.setdefault(clean, [])
                            if owner not in owners:
                                owners.append(owner)
                    except (ET.ParseError, OSError):
                        continue
            except OSError:
                continue
        return {identifier: owners for identifier, owners in identifier_map.items() if len(owners) > 1}
    def compute_priority_order(self, mods_list: list[dict]) -> list[dict]:
        """Apply the manager's conservative author-style order while preserving in-group order.

        Groups: Lua/frameworks -> compatibility patches -> performance/utilities ->
        large overhauls -> ordinary content -> submarine-only packages.
        The order inside each group remains the current profile order, so an imported
        author ModList or a user's manual arrangement is not destroyed by alphabetizing.
        """
        metadata: dict[str, dict] = {}
        original_position: dict[str, int] = {}
        for position, raw_mod in enumerate(mods_list):
            if not isinstance(raw_mod, dict):
                continue
            mid = str(raw_mod.get("id", "")).strip()
            if not mid.isdigit() or mid in metadata:
                continue
            metadata[mid] = dict(raw_mod)
            original_position[mid] = position

        if not metadata:
            return []

        # Local filelist inspection is used only for package traits (e.g. submarine-only),
        # not to invent dependency edges that the public Workshop response does not expose.
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(metadata)))) as executor:
            xml_data = dict(zip(metadata.keys(), executor.map(self.inspect_local_mod_xml, metadata.keys())))

        for mid, mod in metadata.items():
            local_name = str(xml_data[mid].get("name", "") or "")
            current_title = str(mod.get("title") or "")
            if current_title.startswith(("Mod_", "Workshop_")) and local_name and not local_name.startswith("Mod_"):
                mod["title"] = local_name

        core_rank = {mod_id: index for index, mod_id in enumerate(CORE_FRAMEWORK_ORDER)}
        normalized_core_names = {
            re.sub(r"[^a-z0-9а-я]+", "", name.casefold()): mod_id
            for name, mod_id in CORE_FRAMEWORK_NAMES.items()
        }

        def category(mid: str) -> tuple[int, str, str]:
            mod = metadata[mid]
            xml = xml_data[mid]
            title = str(mod.get("title") or "")
            local_name = str(xml.get("name") or "")
            combined = f"{title} {local_name}".casefold()
            compact_title = re.sub(r"[^a-z0-9а-я]+", "", title.casefold())
            core_id = mid if mid in core_rank else normalized_core_names.get(compact_title)
            if core_id in core_rank:
                labels = ("FRAMEWORK", "Фреймворк")
                return 0, labels[0], labels[1]
            # Submarine-only packages remain at the bottom even if their title contains
            # words like "patch" or "fix".
            if xml.get("is_submarine_only"):
                return 5, "SUBMARINE", "Только субмарины"
            # Specific performance/tooling names take precedence over the generic
            # "fix" keyword, otherwise "Performance Fix" is misclassified as a patch.
            if any(word in combined for word in UTILITY_KEYWORDS):
                return 2, "UTILITY", "Исправления и производительность"
            if any(word in combined for word in PATCH_KEYWORDS):
                return 1, "PATCH", "Совместимость и патчи"
            if (
                any(word in combined for word in OVERHAUL_KEYWORDS)
                or xml.get("has_lua_or_cs")
                or xml.get("has_overrides")
            ):
                return 3, "OVERHAUL", "Крупные модификации"
            return 4, "CONTENT", "Обычный контент"

        decorated = []
        for mid, mod in metadata.items():
            rank, tier_label, reason = category(mid)
            if rank == 0:
                # Explicit order: Lua first, then the two related frameworks.
                name = str(mod.get("title") or xml_data[mid].get("name") or "").casefold()
                compact = re.sub(r"[^a-z0-9а-я]+", "", name)
                resolved_core_id = mid if mid in core_rank else normalized_core_names.get(compact, mid)
                secondary_rank = core_rank.get(resolved_core_id, len(core_rank))
                stable_key = (secondary_rank, original_position[mid])
            else:
                stable_key = (original_position[mid],)
            decorated.append((rank, stable_key, mid, tier_label, reason))

        decorated.sort(key=lambda entry: (entry[0], entry[1]))
        result: list[dict] = []
        for _rank, _stable, mid, tier_label, reason in decorated:
            item = dict(metadata[mid])
            item["tier_label"] = tier_label
            item["sort_reason"] = reason
            result.append(item)

        counts: dict[str, int] = {}
        for item in result:
            counts[item["tier_label"]] = counts.get(item["tier_label"], 0) + 1
        summary = ", ".join(f"{name}: {count}" for name, count in counts.items())
        self.log(f"[Приоритеты] Порядок готов. {summary}. Lua закреплён первым среди имеющихся фреймворков; порядок внутри групп сохранён.")
        return result

    def apply_to_config_player(self, mods_list: list[dict]):
        if not self.game_dir.is_dir():
            raise FileNotFoundError(f"Папка игры не найдена: {self.game_dir}")
        if not self.config_path.is_file():
            raise FileNotFoundError(f"Не найден config_player.xml: {self.config_path}")

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = self.config_path.with_suffix(self.config_path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(self.config_path, backup_path)

        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(self.config_path, parser=parser)
        root = tree.getroot()
        content_packages = root.find("contentpackages")
        if content_packages is None:
            content_packages = ET.SubElement(root, "contentpackages")
        regular_packages = content_packages.find("regularpackages")
        if regular_packages is None:
            regular_packages = ET.SubElement(content_packages, "regularpackages")
        else:
            # Сохраняем комментарии и неизвестные игре дополнительные элементы.
            for child in list(regular_packages):
                if isinstance(child.tag, str) and child.tag.lower() == "package":
                    regular_packages.remove(child)

        seen_ids = set()
        for mod in mods_list:
            mid = str(mod.get("id", ""))
            if not mid.isdigit() or mid in seen_ids:
                continue
            seen_ids.add(mid)
            filelist = self.local_mods_dir / mid / "filelist.xml"
            if filelist.is_file():
                package = ET.SubElement(regular_packages, "package")
                package.set("path", f"LocalMods/{mid}/filelist.xml")

        ET.indent(tree, space="  ")
        temp_path = self.config_path.with_name(self.config_path.name + ".tmp")
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        os.replace(temp_path, self.config_path)

class ModManagerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.language = choose_or_load_language(self, LANGUAGE_FILE)
        self._install_dialog_localization()
        self.title("Barotrauma Mod Manager 2.7.1")
        self.geometry("1260x860")
        self.minsize(1020, 720)
        self.configure(bg="#101318")

        self._closing = False
        self._profile_job_running = False
        self._lua_job_running = False
        self._steamcmd_job_running = False
        self._workshop_job_running = False
        self._workshop_results: dict[str, dict] = {}
        self._workshop_buttons: dict[str, tk.Button] = {}
        self._workshop_image_refs: dict[str, object] = {}
        # Coalesce expensive canvas/frame geometry updates while the window is resized.
        self._workshop_canvas_resize_after_id = None
        self._workshop_scrollregion_after_id = None
        self._workshop_pending_canvas_width = None
        self._workshop_last_canvas_width = 0
        self._workshop_wheel_remainder = 0.0
        self._api_key_store = WindowsSecretStore(STEAM_API_KEY_FILE)
        self._steam_api_key = self._api_key_store.load()
        self._log_queue = queue.Queue()
        default_profile = "Main" if self.language == "en" else "Основной"
        self.store = ProfileStore(STATE_FILE, default_profile=default_profile)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        detected_game = None
        if not self.store.data.get("game_dir"):
            detected_game = BaroModManagerCore.detect_game_dir()
            if detected_game:
                self.store.data["game_dir"] = str(detected_game)
                self.store.save()

        self.bind_all("<Map>", self._on_widget_map, add="+")
        self._build_ui()
        self._localize_widget_tree(self)
        if hasattr(self, "mod_context_menu"):
            self._localize_widget_tree(self.mod_context_menu)
        self._watch_localized_variables()
        self._refresh_profile_selector()
        self._refresh_mod_listbox()
        self.after(100, self._drain_log)

        if self.store.data.get("load_warning"):
            self.log(f"[ОШИБКА профилей] Файл настроек повреждён; копия сохранена рядом. {self.store.data['load_warning']}")
        if detected_game:
            self.log(f"[Steam] Папка игры найдена автоматически: {detected_game}")
        self._bootstrap_steamcmd_background()
    def _tr(self, value):
        return translate(value, self.language)

    def _install_dialog_localization(self):
        """Translate native Tk dialogs without changing their return values."""
        def wrap_messagebox(name):
            original = getattr(messagebox, name)

            def localized(*args, **kwargs):
                args = list(args)
                if len(args) > 0 and isinstance(args[0], str):
                    args[0] = self._tr(args[0])
                if len(args) > 1 and isinstance(args[1], str):
                    args[1] = self._tr(args[1])
                for key in ("title", "message", "detail"):
                    if isinstance(kwargs.get(key), str):
                        kwargs[key] = self._tr(kwargs[key])
                return original(*args, **kwargs)

            setattr(messagebox, name, localized)

        for name in ("showinfo", "showwarning", "showerror", "askyesno",
                     "askyesnocancel", "askokcancel", "askretrycancel"):
            wrap_messagebox(name)

        original_askstring = simpledialog.askstring

        def localized_askstring(title, prompt, *args, **kwargs):
            return original_askstring(
                self._tr(title), self._tr(prompt), *args, **kwargs
            )

        simpledialog.askstring = localized_askstring

        for name in ("askdirectory", "askopenfilename", "asksaveasfilename"):
            original = getattr(filedialog, name)

            def make_file_dialog(fn):
                def localized_file_dialog(*args, **kwargs):
                    if isinstance(kwargs.get("title"), str):
                        kwargs["title"] = self._tr(kwargs["title"])
                    filetypes = kwargs.get("filetypes")
                    if filetypes:
                        kwargs["filetypes"] = tuple(
                            (self._tr(label), pattern) for label, pattern in filetypes
                        )
                    return fn(*args, **kwargs)
                return localized_file_dialog

            setattr(filedialog, name, make_file_dialog(original))

    def _patch_widget_localization(self, widget):
        """Translate subsequent text/values updates on this widget."""
        if getattr(widget, "_bmm_i18n_patched", False):
            return
        try:
            original_configure = widget.configure
        except (AttributeError, tk.TclError):
            return

        def localized_configure(cnf=None, **kwargs):
            preserve_profile_names = widget is getattr(self, "profile_combo", None)
            if isinstance(cnf, dict):
                cnf = dict(cnf)
                if isinstance(cnf.get("text"), str):
                    cnf["text"] = self._tr(cnf["text"])
                if not preserve_profile_names and isinstance(cnf.get("values"), (tuple, list)):
                    cnf["values"] = tuple(self._tr(v) for v in cnf["values"])
            if isinstance(kwargs.get("text"), str):
                kwargs["text"] = self._tr(kwargs["text"])
            if not preserve_profile_names and isinstance(kwargs.get("values"), (tuple, list)):
                kwargs["values"] = tuple(self._tr(v) for v in kwargs["values"])
            return original_configure(cnf, **kwargs)

        try:
            widget.configure = localized_configure
            widget.config = localized_configure
            widget._bmm_i18n_patched = True
        except (AttributeError, tk.TclError):
            pass

        if isinstance(widget, ttk.Treeview):
            original_insert = widget.insert

            def localized_tree_insert(parent, index, iid=None, **kwargs):
                # The profile table columns are number/status/group/title/id.
                # Translate app-owned status/category labels only; mod titles are
                # Workshop data and must remain byte-for-byte as received.
                values = kwargs.get("values")
                if isinstance(values, (tuple, list)) and len(values) >= 3:
                    translated = list(values)
                    translated[1] = self._tr(translated[1])
                    translated[2] = self._tr(translated[2])
                    kwargs["values"] = tuple(translated)
                return original_insert(parent, index, iid=iid, **kwargs)

            widget.insert = localized_tree_insert

        elif isinstance(widget, tk.Text):
            original_insert = widget.insert

            def localized_text_insert(index, chars, *args, **kwargs):
                if isinstance(chars, str):
                    chars = self._tr(chars)
                return original_insert(index, chars, *args, **kwargs)

            widget.insert = localized_text_insert

        if isinstance(widget, tk.Menu):
            original_add_command = widget.add_command
            original_entryconfigure = widget.entryconfigure

            def localized_add_command(*args, **kwargs):
                if isinstance(kwargs.get("label"), str):
                    kwargs["label"] = self._tr(kwargs["label"])
                return original_add_command(*args, **kwargs)

            def localized_entryconfigure(index, cnf=None, **kwargs):
                if isinstance(cnf, dict):
                    cnf = dict(cnf)
                    if isinstance(cnf.get("label"), str):
                        cnf["label"] = self._tr(cnf["label"])
                if isinstance(kwargs.get("label"), str):
                    kwargs["label"] = self._tr(kwargs["label"])
                return original_entryconfigure(index, cnf, **kwargs)

            widget.add_command = localized_add_command
            widget.entryconfigure = localized_entryconfigure

    def _localize_widget_tree(self, root):
        if self.language != "en":
            return
        # Workshop titles/descriptions and profile names are user-controlled data.
        if getattr(root, "_bmm_skip_i18n", False):
            return
        try:
            if isinstance(root, (tk.Tk, tk.Toplevel)):
                root.title(self._tr(root.title()))
            if isinstance(root, ttk.Notebook):
                for tab_id in root.tabs():
                    label = root.tab(tab_id, "text")
                    translated = self._tr(label)
                    if translated != label:
                        root.tab(tab_id, text=translated)
            if isinstance(root, ttk.Treeview):
                for column in root["columns"]:
                    caption = root.heading(column, "text")
                    translated = self._tr(caption)
                    if translated != caption:
                        root.heading(column, text=translated)
            if isinstance(root, tk.Menu):
                end = root.index("end")
                if end is not None:
                    for index in range(end + 1):
                        try:
                            label = root.entrycget(index, "label")
                            translated = self._tr(label)
                            if translated != label:
                                root.entryconfigure(index, label=translated)
                        except tk.TclError:
                            continue
            self._patch_widget_localization(root)
            try:
                text = root.cget("text")
                translated = self._tr(text)
                if translated != text:
                    root.configure(text=translated)
            except (tk.TclError, AttributeError):
                pass
            if isinstance(root, ttk.Combobox) and root is not getattr(self, "profile_combo", None):
                values = root.cget("values")
                current = root.get()
                translated_values = tuple(self._tr(value) for value in values)
                if tuple(values) != translated_values:
                    root.configure(values=translated_values)
                translated_current = self._tr(current)
                if current != translated_current:
                    root.set(translated_current)
            if isinstance(root, tk.Entry):
                current = root.get()
                translated_current = self._tr(current)
                if current and current != translated_current:
                    root.delete(0, tk.END)
                    root.insert(0, translated_current)
                # Entry widgets become user input fields after their initial placeholder.
                root._bmm_skip_i18n = True
            for child in root.winfo_children():
                self._localize_widget_tree(child)
        except tk.TclError:
            return

    def _on_widget_map(self, event):
        if self.language == "en":
            self._localize_widget_tree(event.widget)

    def _watch_localized_variables(self):
        """Translate display-only StringVars; leave internal selection values untouched."""
        for name in (
            "paths_summary_var", "profile_order_info_var",
            "mod_count_var", "workshop_status_var",
        ):
            variable = getattr(self, name, None)
            if variable is None:
                continue

            def localize_variable(*_args, var=variable):
                try:
                    value = var.get()
                    translated = self._tr(value)
                    if value != translated:
                        var.set(translated)
                except (tk.TclError, TypeError):
                    pass

            variable.trace_add("write", localize_variable)
            localize_variable()

    def _change_language(self):
        target = "en" if self.language == "ru" else "ru"
        question = (
            "Switch the interface to English and restart the manager?"
            if target == "en"
            else "Переключить интерфейс на русский и перезапустить менеджер?"
        )
        if not messagebox.askyesno(self._tr("Сменить язык"), question, parent=self):
            return
        try:
            save_language(LANGUAGE_FILE, target)
        except OSError as exc:
            messagebox.showerror(
                self._tr("Ошибка"),
                self._tr("Не удалось сохранить настройки языка: ") + str(exc),
                parent=self,
            )
            return

        # os.execl replaces this process, avoiding two manager instances.
        if getattr(sys, "frozen", False):
            os.execl(sys.executable, sys.executable, *sys.argv[1:])
        else:
            os.execl(
                sys.executable, sys.executable,
                os.path.abspath(sys.argv[0]), *sys.argv[1:],
            )

    def _build_ui(self):
        BG = "#0d131b"
        PANEL = "#151e29"
        PANEL_ALT = "#1d2a38"
        BORDER = "#29394a"
        TEXT = "#edf4fa"
        MUTED = "#91a4b7"
        ACCENT = "#65c9f1"
        TEAL = "#67d7b5"
        GREEN = "#1b9b75"

        self.configure(bg=BG)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox", fieldbackground=PANEL_ALT, background=PANEL_ALT,
            foreground=TEXT, arrowcolor=ACCENT, bordercolor=BORDER,
            lightcolor=BORDER, darkcolor=BORDER, padding=(8, 6),
        )
        style.map(
            "TCombobox", fieldbackground=[("readonly", PANEL_ALT)],
            foreground=[("readonly", TEXT)],
        )
        style.configure(
            "Baro.TNotebook", background=BG, borderwidth=0, relief="flat",
            bordercolor=BG, lightcolor=BG, darkcolor=BG,
            tabmargins=(0, 0, 0, 0), padding=0,
        )
        style.configure(
            "Baro.TNotebook.Tab", background=PANEL_ALT, foreground=MUTED,
            padding=(20, 11), font=("Segoe UI", 9, "bold"), borderwidth=0,
            bordercolor=BG, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT,
        )
        style.map(
            "Baro.TNotebook.Tab", background=[("selected", "#24485d")],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Baro.Treeview", background="#101923", foreground=TEXT,
            fieldbackground="#101923", rowheight=31, borderwidth=0, relief="flat",
            bordercolor="#101923", lightcolor="#101923", darkcolor="#101923",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Baro.Treeview.Heading", background=PANEL_ALT, foreground=ACCENT,
            font=("Segoe UI", 8, "bold"), relief="flat", padding=(9, 9),
            bordercolor=PANEL_ALT, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT,
        )
        style.map(
            "Baro.Treeview", background=[("selected", "#244b63")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Baro.Vertical.TScrollbar", background=PANEL_ALT, troughcolor="#101923",
            bordercolor=PANEL, arrowcolor=MUTED, relief="flat",
        )

        main = tk.Frame(self, bg=BG, padx=18, pady=14)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        def card(parent, **kwargs):
            kwargs.setdefault("padx", 13)
            kwargs.setdefault("pady", 10)
            return tk.Frame(
                parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1,
                **kwargs,
            )

        def label(parent, text, size=9, color=MUTED, bold=False, **kwargs):
            return tk.Label(
                parent, text=text, bg=parent.cget("bg"), fg=color,
                font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs,
            )

        self._profile_action_widgets = []

        def button(parent, text, command, color=PANEL_ALT, hover="#2c4053",
                   width=None, pady=6, track=False, foreground=TEXT):
            widget = tk.Button(
                parent, text=text, command=command, bg=color, fg=foreground,
                activebackground=hover, activeforeground="#ffffff",
                relief="flat", bd=0, cursor="hand2",
                font=("Segoe UI", 8, "bold"), padx=11, pady=pady,
                width=width, highlightthickness=0, disabledforeground="#647486",
            )
            if track:
                self._profile_action_widgets.append(widget)
            return widget

        entry_config = {
            "bg": "#101923", "fg": TEXT, "insertbackground": TEXT,
            "relief": "flat", "bd": 0, "font": ("Segoe UI", 10),
            "highlightthickness": 1, "highlightbackground": BORDER,
            "highlightcolor": ACCENT,
        }

        # Header: strong title, concise subtitle, and a small settings control.
        header = tk.Frame(main, bg=BG)
        header.grid(row=0, column=0, sticky="we", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        identity = tk.Frame(header, bg=BG)
        identity.grid(row=0, column=0, sticky="w")
        tk.Label(
            identity, text="BT", bg="#1d4053", fg="#8bdcff",
            font=("Segoe UI", 17, "bold"), width=3, height=1,
        ).pack(side="left", padx=(0, 12))
        identity_text = tk.Frame(identity, bg=BG)
        identity_text.pack(side="left")
        tk.Label(
            identity_text, text="BAROTRAUMA", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            identity_text, text="MOD MANAGER  ·  ПРОФИЛИ И WORKSHOP",
            bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(1, 0))
        header_actions = tk.Frame(header, bg=BG)
        header_actions.grid(row=0, column=1, sticky="e")
        self.paths_summary_var = tk.StringVar(value="Проверка путей…")
        tk.Label(
            header_actions, textvariable=self.paths_summary_var, bg=BG, fg=MUTED,
            font=("Segoe UI", 8), anchor="e",
        ).pack(side="left", padx=(0, 10))
        self.language_btn = button(
            header_actions, "RU / EN", self._change_language,
            color="#234763", hover="#2c6288", pady=8,
        )
        self.language_btn.pack(side="right", padx=(0, 7))
        self.paths_toggle_btn = button(
            header_actions, "⚙ НАСТРОЙКИ", self.toggle_paths_panel,
            color="#203b4d", hover="#2a536b", pady=8,
        )
        self.paths_toggle_btn.pack(side="right")

        # Installation paths are tucked away until needed to keep the main screen clean.
        paths_bar = card(main, padx=12, pady=7)
        paths_bar.grid(row=1, column=0, sticky="we", pady=(0, 8))
        paths_bar.columnconfigure(0, weight=1)
        label(paths_bar, "РАСПОЛОЖЕНИЕ И ИНСТРУМЕНТЫ", size=8, color=ACCENT, bold=True).grid(
            row=0, column=0, sticky="w")
        label(paths_bar, "Папка игры, SteamCMD и клиентский LuaCs-патч доступны в настройках.",
              size=8, color=MUTED).grid(row=0, column=1, sticky="e")

        self.paths_details = card(main)
        self.paths_details.grid(row=2, column=0, sticky="we", pady=(0, 8))
        self.paths_details.columnconfigure(1, weight=1)
        label(self.paths_details, "ПАПКА ИГРЫ", size=8, color=ACCENT, bold=True).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 7))
        label(self.paths_details, "Barotrauma", color=TEXT).grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=3)
        self.game_dir_var = tk.StringVar(value=self.store.data.get("game_dir", ""))
        tk.Entry(self.paths_details, textvariable=self.game_dir_var, **entry_config).grid(
            row=1, column=1, sticky="we", padx=(0, 8), ipady=5, pady=2)
        button(self.paths_details, "Обзор", self.browse_game).grid(row=1, column=2, padx=(0, 5))
        label(self.paths_details, "SteamCMD", color=TEXT).grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=3)
        saved_steamcmd = self.store.data.get("steamcmd_path", "")
        if not saved_steamcmd and DEFAULT_STEAMCMD_EXE.exists():
            saved_steamcmd = str(DEFAULT_STEAMCMD_EXE)
        self.steamcmd_var = tk.StringVar(value=saved_steamcmd)
        tk.Entry(self.paths_details, textvariable=self.steamcmd_var, **entry_config).grid(
            row=2, column=1, sticky="we", padx=(0, 8), ipady=5, pady=2)
        button(self.paths_details, "Обзор", self.browse_steamcmd).grid(row=2, column=2, padx=(0, 5))
        self.steamcmd_btn = button(
            self.paths_details, "УСТАНОВИТЬ STEAMCMD", self.auto_download_steamcmd,
            color="#244b63", hover="#2c607d",
        )
        self.steamcmd_btn.grid(row=2, column=3, sticky="e")
        tools_row = tk.Frame(self.paths_details, bg=PANEL)
        tools_row.grid(row=3, column=0, columnspan=4, sticky="we", pady=(8, 0))
        label(tools_row, "ДОПОЛНИТЕЛЬНО", size=8, color=MUTED, bold=True).pack(side="left", padx=(0, 12))
        self.luacs_btn = button(
            tools_row, "Установить LuaCs", self.install_luacs_patch,
            color="#57427e", hover="#6d519a",
        )
        self.luacs_btn.pack(side="left", padx=(0, 6))
        self.restore_luacs_btn = button(tools_row, "Откатить LuaCs", self.restore_luacs_patch)
        self.restore_luacs_btn.pack(side="left")
        self._paths_expanded = False
        self.paths_details.grid_remove()

        # Main navigation.
        self.notebook = ttk.Notebook(main, style="Baro.TNotebook")
        self.notebook.grid(row=3, column=0, sticky="nsew")
        self.profile_tab = tk.Frame(self.notebook, bg=BG, padx=1, pady=8)
        self.workshop_tab = tk.Frame(self.notebook, bg=BG, padx=1, pady=8)
        self.notebook.add(self.profile_tab, text="  МОИ ПРОФИЛИ  ")
        self.notebook.add(self.workshop_tab, text="  STEAM WORKSHOP  ")

        # Profile header: one compact strip instead of several competing cards.
        profile = self.profile_tab
        profile.columnconfigure(0, weight=1)
        profile.rowconfigure(2, weight=1)
        profile_bar = card(profile, padx=12, pady=8)
        profile_bar.grid(row=0, column=0, sticky="we", pady=(0, 8))
        label(profile_bar, "АКТИВНЫЙ ПРОФИЛЬ", size=8, color=ACCENT, bold=True).pack(anchor="w", pady=(0, 6))
        profile_actions = tk.Frame(profile_bar, bg=PANEL)
        profile_actions.pack(fill="x")
        self.profile_var = tk.StringVar(value=self.store.data["active_profile"])
        self.profile_combo = ttk.Combobox(
            profile_actions, textvariable=self.profile_var, state="readonly",
            width=23, font=("Segoe UI", 9),
        )
        self.profile_combo.pack(side="left", padx=(0, 7))
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_switch)
        button(profile_actions, "+ Новый", self.create_profile, track=True).pack(side="left", padx=(0, 4))
        button(profile_actions, "Копия", self.duplicate_profile, track=True).pack(side="left", padx=(0, 4))
        button(profile_actions, "Удалить профиль", self.delete_profile,
               color="#52313a", hover="#70404c", track=True).pack(side="left", padx=(0, 12))
        tk.Frame(profile_actions, bg=BORDER, width=1, height=24).pack(side="left", padx=(0, 12), fill="y")
        label(profile_actions, "ЗАГРУЗЧИКИ", size=8, color=MUTED, bold=True).pack(side="left", padx=(0, 7))
        saved_workers = max(1, min(3, int(self.store.data.get("parallel_downloads", 2) or 2)))
        self.download_workers_var = tk.StringVar(value=str(saved_workers))
        self.download_workers_combo = ttk.Combobox(
            profile_actions, textvariable=self.download_workers_var, state="readonly",
            width=3, values=("1", "2", "3"),
        )
        self.download_workers_combo.pack(side="left")
        label(profile_actions, "потока", size=8, color=MUTED).pack(side="left", padx=(6, 0))
        self.profile_order_info_var = tk.StringVar(value="ПОРЯДОК ПРОФИЛЯ")
        self.profile_order_info_label = tk.Label(
            profile_actions, textvariable=self.profile_order_info_var,
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="e",
        )
        self.profile_order_info_label.pack(side="right", padx=(10, 0))

        add_frame = card(profile, padx=12, pady=9)
        add_frame.grid(row=1, column=0, sticky="we", pady=(0, 8))
        add_frame.columnconfigure(0, weight=1)
        label(add_frame, "БЫСТРОЕ ДОБАВЛЕНИЕ", size=8, color=ACCENT, bold=True).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.workshop_var = tk.StringVar()
        ent_ws = tk.Entry(add_frame, textvariable=self.workshop_var, **entry_config)
        ent_ws.grid(row=1, column=0, sticky="we", padx=(0, 8), ipady=7)
        ent_ws.insert(0, "Вставь ссылку на Workshop или ID мода / коллекции")
        ent_ws.bind(
            "<FocusIn>", lambda _event: ent_ws.delete(0, tk.END)
            if ent_ws.get().startswith("Вставь ссылку") else None,
        )
        self.dl_btn = button(
            add_frame, "СКАЧАТЬ И ДОБАВИТЬ", self.download_and_add_to_profile,
            color="#17785d", hover="#218e70", pady=8,
        )
        self.dl_btn.grid(row=1, column=1, sticky="e")

        list_panel = card(profile, padx=12, pady=10)
        list_panel.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        list_panel.columnconfigure(0, weight=1)
        list_panel.rowconfigure(2, weight=1)

        list_toolbar = tk.Frame(list_panel, bg=PANEL)
        list_toolbar.grid(row=0, column=0, sticky="we", pady=(0, 9))
        button(list_toolbar, "↑", lambda: self.move_mod(-1), width=3, pady=6,
               track=True).pack(side="left", padx=(0, 4))
        button(list_toolbar, "↓", lambda: self.move_mod(1), width=3, pady=6,
               track=True).pack(side="left", padx=(0, 8))
        button(list_toolbar, "АВТОПОРЯДОК", self.auto_sort_current_profile,
               color="#4b3b69", hover="#655087", track=True).pack(side="left", padx=(0, 5))
        self.upd_btn = button(
            list_toolbar, "ПРОВЕРИТЬ", self.check_and_update_mods,
            color="#244b63", hover="#2c607d",
        )
        self.upd_btn.pack(side="left", padx=(0, 5))
        button(list_toolbar, "УДАЛИТЬ", self.remove_selected_mod,
               color="#52313a", hover="#70404c", track=True).pack(side="left", padx=(0, 5))
        button(list_toolbar, "ИМПОРТ XML", self.import_modlist_xml,
               color="#263e52", hover="#31536d", track=True).pack(side="left", padx=(0, 5))
        button(list_toolbar, "ЭКСПОРТ XML", self.export_modlist_xml,
               color="#263e52", hover="#31536d", track=True).pack(side="left", padx=(0, 5))
        button(list_toolbar, "ПРИМЕНИТЬ В ИГРУ", self.apply_active_profile_to_game,
               color=GREEN, hover="#22b889", track=True).pack(side="right")

        list_header = tk.Frame(list_panel, bg=PANEL)
        list_header.grid(row=1, column=0, sticky="we", pady=(0, 7))
        label(list_header, "СОСТАВ ПРОФИЛЯ", size=8, color=ACCENT, bold=True).pack(side="left")
        self.mod_count_var = tk.StringVar(value="0 модов")
        tk.Label(
            list_header, textvariable=self.mod_count_var, bg=PANEL, fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")

        list_area = tk.Frame(list_panel, bg=PANEL)
        list_area.grid(row=2, column=0, sticky="nsew")
        list_area.columnconfigure(0, weight=1)
        list_area.rowconfigure(0, weight=1)
        self.mod_listbox = ttk.Treeview(
            list_area, columns=("number", "status", "group", "title", "id"),
            show="headings", selectmode="browse", style="Baro.Treeview",
        )
        columns = (
            ("number", "№", 48, "center", False),
            ("status", "СТАТУС", 112, "center", False),
            ("group", "ГРУППА", 125, "center", False),
            ("title", "МОД", 520, "w", True),
            ("id", "WORKSHOP ID", 125, "center", False),
        )
        for column, heading, width, anchor, stretch in columns:
            self.mod_listbox.heading(column, text=heading)
            self.mod_listbox.column(column, width=width, anchor=anchor, stretch=stretch,
                                    minwidth=42 if column != "title" else 220)
        self.mod_listbox.tag_configure("ok", foreground="#7bdcb2")
        self.mod_listbox.tag_configure("missing", foreground="#f3c77e")
        self.mod_listbox.tag_configure("outdated", foreground="#ff9999")
        self.mod_listbox.tag_configure("unknown", foreground="#b9c6d3")
        self.mod_listbox.grid(row=0, column=0, sticky="nsew")
        self.mod_listbox.bind("<Double-Button-1>", lambda _event: self.open_selected_workshop_item())
        self.mod_listbox.bind("<Control-Up>", lambda _event: self._move_mod_key(-1))
        self.mod_listbox.bind("<Control-Down>", lambda _event: self._move_mod_key(1))
        self.mod_listbox.bind("<Delete>", lambda _event: self.remove_selected_mod())
        self.mod_listbox.bind("<Button-3>", self.show_mod_context_menu)
        self.mod_listbox.bind("<Shift-F10>", self.show_mod_context_menu)
        self.mod_context_menu = tk.Menu(
            self, tearoff=False, bg=PANEL_ALT, fg=TEXT,
            activebackground="#244b63", activeforeground="#ffffff",
            relief="flat", bd=0,
        )
        self.mod_context_menu.add_command(label="Открыть в Steam Workshop", command=self.open_selected_workshop_item)
        self.mod_context_menu.add_command(label="Проверить XML-конфликты", command=self.run_conflict_detector)
        self.mod_context_menu.add_separator()
        self.mod_context_menu.add_command(label="Удалить мод…", command=self.remove_selected_mod)
        scrollbar = ttk.Scrollbar(
            list_area, orient="vertical", command=self.mod_listbox.yview,
            style="Baro.Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.mod_listbox.configure(yscrollcommand=scrollbar.set)

        log_panel = card(profile, padx=12, pady=8)
        log_panel.grid(row=3, column=0, sticky="we")
        label(log_panel, "ЖУРНАЛ ОПЕРАЦИЙ", size=8, color=ACCENT, bold=True).pack(anchor="w", pady=(0, 5))
        self.log_box = scrolledtext.ScrolledText(
            log_panel, bg="#0b1118", fg="#b7c7d7", font=("Consolas", 8),
            relief="flat", height=4, insertbackground=TEXT, wrap="word",
            borderwidth=0, highlightthickness=1, highlightbackground=BORDER,
        )
        self.log_box.pack(fill="both", expand=True)
        self.log_box.tag_configure("error", foreground="#ff8585")
        self.log_box.tag_configure("success", foreground="#6fe0ad")
        self.log_box.tag_configure("warning", foreground="#f4c77b")

        # Workshop tab. Search and results keep the same palette and spacing.
        workshop = self.workshop_tab
        workshop.columnconfigure(0, weight=1)
        workshop.rowconfigure(1, weight=1)
        search_panel = card(workshop)
        search_panel.grid(row=0, column=0, sticky="we", pady=(0, 8))
        search_panel.columnconfigure(0, weight=1)
        label(search_panel, "STEAM WORKSHOP", size=9, color=ACCENT, bold=True).grid(
            row=0, column=0, sticky="w", pady=(0, 7))
        search_row = tk.Frame(search_panel, bg=PANEL)
        search_row.grid(row=1, column=0, sticky="we")
        search_row.columnconfigure(0, weight=1)
        self.workshop_search_var = tk.StringVar()
        self.workshop_search_entry = tk.Entry(search_row, textvariable=self.workshop_search_var, **entry_config)
        self.workshop_search_entry.grid(row=0, column=0, sticky="we", padx=(0, 8), ipady=7)
        self.workshop_search_entry.bind("<Return>", lambda _event: self.search_workshop())
        self.workshop_type_var = tk.StringVar(value="Моды")
        self.workshop_type_combo = ttk.Combobox(
            search_row, textvariable=self.workshop_type_var, state="readonly", width=20,
            values=("Моды", "Коллекции / модпаки"),
        )
        self.workshop_type_combo.grid(row=0, column=1, padx=(0, 8))
        self.workshop_type_combo.bind("<<ComboboxSelected>>", self._on_workshop_type_changed)
        self.workshop_sort_var = tk.StringVar(value="Популярные")
        self.workshop_sort_combo = ttk.Combobox(
            search_row, textvariable=self.workshop_sort_var, state="readonly", width=16,
            values=("Популярные", "По релевантности", "По рейтингу", "Новые", "Недавно обновлённые"),
        )
        self.workshop_sort_combo.grid(row=0, column=2, padx=(0, 8))
        self.workshop_search_btn = button(
            search_row, "НАЙТИ", self.search_workshop,
            color="#17785d", hover="#218e70", pady=8,
        )
        self.workshop_search_btn.grid(row=0, column=3, padx=(0, 6))
        self.workshop_key_btn = button(
            search_row, "API KEY ⚙", self.configure_steam_api_key,
            color="#244b63", hover="#2c607d", pady=8,
        )
        self.workshop_key_btn.grid(row=0, column=4)
        self.workshop_status_var = tk.StringVar(
            value="Введи личный Steam Web API key, чтобы искать моды и коллекции."
        )
        label(
            search_panel,
            "Поиск только по Barotrauma. Скачивание проверяет пакет и добавляет его в активный профиль.",
            size=8, color=MUTED, wraplength=1000, justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(7, 2))

        results_panel = card(workshop)
        results_panel.grid(row=1, column=0, sticky="nsew")
        results_panel.columnconfigure(0, weight=1)
        results_panel.rowconfigure(1, weight=1)
        results_header = tk.Frame(results_panel, bg=PANEL)
        results_header.grid(row=0, column=0, sticky="we", pady=(0, 7))
        label(results_header, "РЕЗУЛЬТАТЫ", size=8, color=ACCENT, bold=True).pack(side="left")
        tk.Label(
            results_header, textvariable=self.workshop_status_var, bg=PANEL, fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")

        scroll_host = tk.Frame(results_panel, bg=PANEL)
        scroll_host.grid(row=1, column=0, sticky="nsew")
        scroll_host.columnconfigure(0, weight=1)
        scroll_host.rowconfigure(0, weight=1)
        self.workshop_canvas = tk.Canvas(
            scroll_host, bg="#101923", highlightthickness=0, bd=0,
        )
        self.workshop_canvas.grid(row=0, column=0, sticky="nsew")
        ws_scroll = ttk.Scrollbar(
            scroll_host, orient="vertical", command=self.workshop_canvas.yview,
            style="Baro.Vertical.TScrollbar",
        )
        ws_scroll.grid(row=0, column=1, sticky="ns")
        self.workshop_canvas.configure(yscrollcommand=ws_scroll.set)
        self.workshop_results_frame = tk.Frame(self.workshop_canvas, bg="#101923")
        self._workshop_window = self.workshop_canvas.create_window(
            (0, 0), window=self.workshop_results_frame, anchor="nw",
        )
        # The result frame may receive many Configure events while cards wrap/reflow.
        # Debounce scrollregion calculations instead of calling bbox() for every event.
        self._workshop_canvas_resize_after_id = None
        self._workshop_scrollregion_after_id = None
        self._workshop_pending_canvas_width = None
        self._workshop_last_canvas_width = 0
        self._workshop_wheel_remainder = 0.0
        self.workshop_results_frame.bind(
            "<Configure>", self._schedule_workshop_scrollregion_update, add="+",
        )
        self.workshop_canvas.bind(
            "<Configure>", self._schedule_workshop_canvas_resize, add="+",
        )

        # Mouse-wheel events do not bubble from Labels/Buttons to their parent Canvas.
        # Bind at the toplevel and filter by the event widget's path so scrolling works
        # anywhere inside Workshop cards, but never hijacks the profile list or log.
        self.bind_all("<MouseWheel>", self._on_workshop_mousewheel, add="+")
        # Linux/X11 wheel events (harmless on Windows; they simply never fire there).
        self.bind_all("<Button-4>", self._on_workshop_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_workshop_mousewheel, add="+")
        self.workshop_next_cursor = None
        self.workshop_last_query = ""
        self.workshop_last_sort = "popular"
        self.workshop_last_type = "items"
        self.workshop_append_results = False
        self.workshop_more_btn = button(
            results_panel, "ЗАГРУЗИТЬ ЕЩЁ", self.load_more_workshop,
            color="#244b63", hover="#2c607d",
        )
        self.workshop_more_btn.grid(row=2, column=0, sticky="e", pady=(8, 0))
        self.workshop_more_btn.config(state="disabled")
        self._update_path_summary()

    def toggle_paths_panel(self):
        self._paths_expanded = not self._paths_expanded
        if self._paths_expanded:
            self.paths_details.grid()
            self.paths_toggle_btn.config(text="✕ ЗАКРЫТЬ НАСТРОЙКИ")
        else:
            self.paths_details.grid_remove()
            self.paths_toggle_btn.config(text="⚙ НАСТРОЙКИ")

    def _update_path_summary(self):
        game_var = getattr(self, "game_dir_var", None)
        steamcmd_var = getattr(self, "steamcmd_var", None)
        game = str(game_var.get()).strip() if game_var is not None else ""
        steamcmd = str(steamcmd_var.get()).strip() if steamcmd_var is not None else ""
        game_ok = bool(game and Path(game).is_dir())
        steam_ok = bool(steamcmd and Path(steamcmd).is_file()) or DEFAULT_STEAMCMD_EXE.is_file()
        game_label = "ИГРА НАЙДЕНА" if game_ok else "ВЫБЕРИ ПАПКУ ИГРЫ"
        steam_label = "STEAMCMD ГОТОВ" if steam_ok else "STEAMCMD НЕ НАЙДЕН"
        self.paths_summary_var.set(f"● {game_label}   ·   ● {steam_label}")
    def _save_paths(self):
        self.store.data["game_dir"] = self.game_dir_var.get().strip()
        selected_steamcmd = self.steamcmd_var.get().strip()
        self.store.data["steamcmd_path"] = selected_steamcmd or str(DEFAULT_STEAMCMD_EXE)
        try:
            self.store.data["parallel_downloads"] = max(1, min(3, int(self.download_workers_var.get())))
        except (TypeError, ValueError, AttributeError):
            self.store.data["parallel_downloads"] = 2
        self.store.save()
        if hasattr(self, "paths_summary_var"):
            self._update_path_summary()
    def _get_core(self) -> BaroModManagerCore:
        self._save_paths()
        core = BaroModManagerCore(self.store.data["game_dir"], self.store.data["steamcmd_path"], log_callback=self.log)
        core.workers = max(1, min(3, int(self.store.data.get("parallel_downloads", 2) or 2)))
        return core

    def log(self, msg: str):
        # Worker threads only enqueue text; all Tk updates stay on the GUI thread.
        self._log_queue.put(self._tr(msg))

    def _drain_log(self):
        if self._closing:
            return
        try:
            while True:
                msg = self._log_queue.get_nowait()
                lowered = msg.casefold()
                tag = "error" if ("ошибка" in lowered or "error" in lowered or "✘" in msg) else (
                    "success" if ("готово" in lowered or "ready" in lowered or "complete" in lowered or "✓" in msg or "✔" in msg) else (
                        "warning" if ("предупреж" in lowered or "warning" in lowered or "недоступ" in lowered or "unavailable" in lowered) else ""))
                self.log_box.insert(tk.END, msg + "\n", tag)
                self.log_box.see(tk.END)
        except queue.Empty:
            pass
        try:
            self.after(120, self._drain_log)
        except tk.TclError:
            pass
    def _ask(self, fn):
        """Run a Tk dialog on the GUI thread and return its value to a worker."""
        if self._closing:
            return None
        result, event = {}, threading.Event()
        def run():
            try:
                if not self._closing:
                    result["value"] = fn()
            except Exception as exc:
                result["error"] = exc
            finally:
                event.set()
        self.after(0, run)
        if not event.wait(timeout=90):
            return None
        if "error" in result:
            raise result["error"]
        return result.get("value")
    def _bootstrap_steamcmd_background(self):
        core = self._get_core()
        if core.steamcmd_path.is_file() or os.name != "nt":
            return
        self._steamcmd_job_running = True
        self.steamcmd_btn.config(state="disabled")

        def work():
            try:
                core.ensure_steamcmd_installed()
                exe = str(core.steamcmd_path)
                if not self._closing:
                    self.after(0, lambda: self.steamcmd_var.set(exe))
                    self.after(0, self._save_paths)
            except Exception as exc:
                self.log(f"[SteamCMD] Автоустановка не удалась: {exc}. Попробует снова при скачивании модов.")
            finally:
                if not self._closing:
                    def finish_bootstrap():
                        self._steamcmd_job_running = False
                        self.steamcmd_btn.config(state="normal")
                    self.after(0, finish_bootstrap)

        threading.Thread(target=work, daemon=True).start()

    def browse_game(self):
        if self._profile_job_running:
            return
        folder = filedialog.askdirectory(title="Выберите папку Barotrauma")
        if folder:
            self.game_dir_var.set(folder)
            self._save_paths()
            self._refresh_mod_listbox()
            self._refresh_workshop_card_states()

    def browse_steamcmd(self):
        if self._profile_job_running:
            return
        file = filedialog.askopenfilename(filetypes=[("Executable", "*.exe"), ("All", "*.*")])
        if file:
            self.steamcmd_var.set(file)
            self._save_paths()

    def auto_download_steamcmd(self):
        if self._steamcmd_job_running or self._profile_job_running or self._lua_job_running:
            return
        core = self._get_core()
        self._steamcmd_job_running = True
        self.steamcmd_btn.config(state="disabled")
        threading.Thread(target=self._worker_download_steamcmd, args=(core,), daemon=True).start()
    def _worker_download_steamcmd(self, core: BaroModManagerCore):
        try:
            exe_path = core.ensure_steamcmd_installed()
            self.log(f"[SteamCMD] Установлен: {exe_path}")
            if not self._closing:
                self.after(0, lambda: self.steamcmd_var.set(str(exe_path)))
                self.after(0, self._save_paths)
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА SteamCMD] {error}")
            if not self._closing:
                self.after(0, lambda error=error: messagebox.showerror("SteamCMD", error))
        finally:
            if not self._closing:
                def finish_download():
                    self._steamcmd_job_running = False
                    self.steamcmd_btn.config(state="normal")
                self.after(0, finish_download)
    def install_luacs_patch(self):
        if self._lua_job_running:
            return
        if not self.game_dir_var.get().strip():
            messagebox.showwarning("Внимание", "Выберите папку с игрой!")
            return
        core = self._get_core()
        self._lua_job_running = True
        self.luacs_btn.config(state="disabled")
        self.restore_luacs_btn.config(state="disabled")
        threading.Thread(target=self._worker_install_luacs, args=(core,), daemon=True).start()
    def _worker_install_luacs(self, core: BaroModManagerCore):
        try:
            core.install_luacs_client_patch()
            if not self._closing:
                self.after(0, lambda: messagebox.showinfo("Готово", "LuaCs-патч установлен. Бэкап сохранён."))
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА LuaCs] {error}")
            if not self._closing:
                self.after(0, lambda error=error: messagebox.showerror("Ошибка LuaCs", error))
        finally:
            if not self._closing:
                def finish_install():
                    self._lua_job_running = False
                    self.luacs_btn.config(state="normal")
                    self.restore_luacs_btn.config(state="normal")
                self.after(0, finish_install)
    def restore_luacs_patch(self):
        if self._lua_job_running:
            return
        core = self._get_core()
        self._lua_job_running = True
        self.luacs_btn.config(state="disabled")
        self.restore_luacs_btn.config(state="disabled")
        threading.Thread(target=self._worker_restore_luacs, args=(core,), daemon=True).start()

    def _worker_restore_luacs(self, core: BaroModManagerCore):
        try:
            core.restore_luacs_backup()
            if not self._closing:
                self.after(0, lambda: messagebox.showinfo("Откат", "Файлы LuaCs восстановлены."))
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА отката LuaCs] {error}")
            if not self._closing:
                self.after(0, lambda error=error: messagebox.showerror("Откат LuaCs", error))
        finally:
            if not self._closing:
                def finish_restore():
                    self._lua_job_running = False
                    self.luacs_btn.config(state="normal")
                    self.restore_luacs_btn.config(state="normal")
                self.after(0, finish_restore)
    def _refresh_profile_selector(self):
        names = list(self.store.data["profiles"].keys())
        if not names:
            names = [self.store.default_profile]
            self.store.data["profiles"] = {self.store.default_profile: []}
        if self.store.data["active_profile"] not in names:
            self.store.data["active_profile"] = names[0]
        self.profile_combo["values"] = names
        self.profile_var.set(self.store.data["active_profile"])
        self.profile_combo.config(state="disabled" if self._profile_job_running else "readonly")
        self._update_profile_order_indicator()

    def _update_profile_order_indicator(self):
        """Show whether this profile follows an author, manual, or auto order."""
        variable = getattr(self, "profile_order_info_var", None)
        if variable is None:
            return
        profile_name = str(self.store.data.get("active_profile", self.store.default_profile))
        source = self.store.get_profile_source(profile_name)
        mode = str(source.get("order_mode", ""))
        if source.get("format") == "Barotrauma ModList" and mode == "author":
            source_name = str(source.get("source_name") or source.get("source_file") or "ModList")
            value = f"АВТОРСКИЙ ПОРЯДОК · {source_name}"
        elif source.get("format") == "Barotrauma ModList" and mode == "manual":
            value = "РУЧНОЙ ПОРЯДОК"
        elif source.get("format") == "Barotrauma ModList" and mode == "automatic":
            value = "АВТОПРИОРИТЕТ"
        else:
            value = "ПОРЯДОК ПРОФИЛЯ"
        variable.set(value[:64])
    def _selected_mod_index(self) -> int | None:
        selected = self.mod_listbox.selection() if hasattr(self, "mod_listbox") else ()
        if not selected:
            return None
        try:
            return int(self.mod_listbox.index(selected[0]))
        except (tk.TclError, ValueError, TypeError):
            return None

    def _refresh_mod_listbox(self):
        selected_id = None
        selected_index = self._selected_mod_index()
        if selected_index is not None:
            current = self.store.get_active_mods()
            if 0 <= selected_index < len(current):
                selected_id = str(current[selected_index].get("id", ""))

        for row_id in self.mod_listbox.get_children():
            self.mod_listbox.delete(row_id)
        mods = self.store.get_active_mods()
        self.mod_count_var.set(f"{len(mods)} модов")
        selected_row = None
        game_dir = Path(self.game_dir_var.get().strip()) if self.game_dir_var.get().strip() else None
        for index, mod in enumerate(mods, 1):
            mid = str(mod.get("id", ""))
            tier = str(mod.get("tier_label", "MOD"))
            has_files = bool(game_dir and find_filelist_root(game_dir / "LocalMods" / mid))
            status = str(mod.get("update_status", "OK" if has_files else "MISSING"))
            if not has_files or status == "MISSING":
                badge, tag = "НЕ СКАЧАН", "missing"
            elif status == "OUTDATED":
                badge, tag = "ОБНОВИТЬ", "outdated"
            elif status == "UNKNOWN":
                badge, tag = "НЕИЗВЕСТНО", "unknown"
            else:
                badge, tag = "АКТУАЛЕН", "ok"
            title = str(mod.get("title", mid)).replace("\n", " ").replace("\r", " ").strip()
            row_id = f"mod_{mid}_{index}"
            self.mod_listbox.insert(
                "", "end", iid=row_id,
                values=(f"{index:02d}", badge, tier, title, mid),
                tags=(tag,),
            )
            if selected_id and mid == selected_id and selected_row is None:
                selected_row = row_id

        if selected_row:
            self.mod_listbox.selection_set(selected_row)
            self.mod_listbox.focus(selected_row)
            self.mod_listbox.see(selected_row)

    def _move_mod_key(self, direction: int):
        self.move_mod(direction)
        return "break"

    def _set_profile_job(self, running: bool):
        self._profile_job_running = running
        state = "disabled" if running else "normal"
        for widget_name in ("dl_btn", "upd_btn"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.config(state=state)
        self.profile_combo.config(state="disabled" if running else "readonly")
        for widget in self._profile_action_widgets:
            widget.config(state=state)
        if not running:
            self._refresh_workshop_card_states()

    def _on_close(self):
        if self._profile_job_running or self._lua_job_running or self._steamcmd_job_running or self._workshop_job_running:
            messagebox.showwarning(
                "Операция выполняется",
                "Сейчас выполняется операция с файлами игры или Steam. Дождитесь её завершения, "
                "чтобы не оставить файлы в промежуточном состоянии.",
                parent=self,
            )
            return
        self._closing = True
        try:
            self._save_paths()
        except Exception:
            pass
        self.destroy()

    @staticmethod
    def _widget_path_is_within(widget, parent) -> bool:
        """Return True when a Tk widget is parented by *parent* (including itself)."""
        try:
            widget_path = getattr(widget, "_w", None) or str(widget)
            parent_path = getattr(parent, "_w", None) or str(parent)
            return widget_path == parent_path or widget_path.startswith(parent_path + ".")
        except Exception:
            return False

    def _on_workshop_mousewheel(self, event):
        """Scroll Workshop when the pointer is over any card, not only the scrollbar."""
        try:
            if self.notebook.select() != str(self.workshop_tab):
                return None

            target = getattr(event, "widget", None)
            if not (
                self._widget_path_is_within(target, self.workshop_canvas)
                or self._widget_path_is_within(target, self.workshop_results_frame)
            ):
                # Let other widgets and the native scrollbar process their own events.
                return None

            event_number = getattr(event, "num", None)
            if event_number == 4:
                units = -3
            elif event_number == 5:
                units = 3
            else:
                delta = float(getattr(event, "delta", 0) or 0)
                # A wheel notch is normally 120 on Windows. The accumulator also
                # handles high-resolution wheels/trackpads that send smaller deltas.
                self._workshop_wheel_remainder += -(delta / 120.0) * 3.0
                units = int(self._workshop_wheel_remainder)  # truncates toward zero
                self._workshop_wheel_remainder -= units

            if units:
                self.workshop_canvas.yview_scroll(units, "units")
            return "break"
        except (tk.TclError, AttributeError, TypeError, ValueError):
            return None

    def _schedule_workshop_canvas_resize(self, event):
        """Debounce canvas width changes during edge-drag window resizing."""
        try:
            width = int(event.width)
            if width < 2:
                return
            baseline = self._workshop_pending_canvas_width
            if baseline is None:
                baseline = self._workshop_last_canvas_width
            if baseline and abs(width - baseline) < 8:
                return
            self._workshop_pending_canvas_width = width
            pending = self._workshop_canvas_resize_after_id
            if pending:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
            # Wait until the user pauses resizing. This prevents every pixel of an
            # edge drag from forcing all Workshop cards to run geometry negotiation.
            self._workshop_canvas_resize_after_id = self.after(
                85, self._apply_workshop_canvas_resize,
            )
        except (tk.TclError, AttributeError, TypeError, ValueError):
            return

    def _apply_workshop_canvas_resize(self):
        self._workshop_canvas_resize_after_id = None
        width = self._workshop_pending_canvas_width
        self._workshop_pending_canvas_width = None
        if self._closing or not width:
            return
        try:
            if abs(int(width) - self._workshop_last_canvas_width) < 4:
                return
            self.workshop_canvas.itemconfigure(self._workshop_window, width=max(1, int(width)))
            self._workshop_last_canvas_width = int(width)
            self._schedule_workshop_scrollregion_update()
        except tk.TclError:
            return

    def _schedule_workshop_scrollregion_update(self, _event=None):
        """Coalesce card layout changes into one scrollregion calculation."""
        if self._closing:
            return
        pending = self._workshop_scrollregion_after_id
        if pending:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        try:
            self._workshop_scrollregion_after_id = self.after(
                65, self._apply_workshop_scrollregion,
            )
        except tk.TclError:
            self._workshop_scrollregion_after_id = None

    def _apply_workshop_scrollregion(self):
        self._workshop_scrollregion_after_id = None
        if self._closing:
            return
        try:
            bounds = self.workshop_canvas.bbox("all")
            if bounds:
                self.workshop_canvas.configure(scrollregion=bounds)
        except tk.TclError:
            return

    @staticmethod
    def _workshop_sort_code(label: str) -> str:
        return {
            "Популярные": "popular",
            "По релевантности": "relevance",
            "По рейтингу": "rated",
            "Новые": "newest",
            "Недавно обновлённые": "updated",
            "Most popular": "popular",
            "Relevance": "relevance",
            "Top rated": "rated",
            "Newest": "newest",
            "Recently updated": "updated",
        }.get(label, "popular")

    def _workshop_card_label(self, mod_id: str) -> str:
        """Return the correct action label for a Workshop item or collection."""
        item = getattr(self, "_workshop_results", {}).get(str(mod_id), {})
        if item.get("is_collection"):
            child_ids = {str(value) for value in item.get("children", []) if str(value).isdigit()}
            active_ids = {str(mod.get("id", "")) for mod in self.store.get_active_mods()}
            if child_ids and child_ids.issubset(active_ids):
                return "КОЛЛЕКЦИЯ В ПРОФИЛЕ"
            return "СКАЧАТЬ КОЛЛЕКЦИЮ ↓"

        game_dir_text = self.game_dir_var.get().strip()
        installed = bool(
            game_dir_text
            and find_filelist_root(Path(game_dir_text) / "LocalMods" / str(mod_id))
        )
        if not installed:
            return "СКАЧАТЬ ↓"
        active_ids = {str(mod.get("id", "")) for mod in self.store.get_active_mods()}
        return "УЖЕ В ПРОФИЛЕ" if str(mod_id) in active_ids else "ДОБАВИТЬ В ПРОФИЛЬ"

    def _refresh_workshop_card_states(self):
        for mod_id, card_button in self._workshop_buttons.items():
            try:
                label = self._workshop_card_label(mod_id)
                card_button.config(
                    text=label,
                    state="disabled" if label in ("УЖЕ В ПРОФИЛЕ", "КОЛЛЕКЦИЯ В ПРОФИЛЕ") else "normal",
                )
            except tk.TclError:
                continue

    def configure_steam_api_key(self):
        dialog = tk.Toplevel(self)
        dialog.title("Steam Web API key")
        dialog.configure(bg="#191f28")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("520x215")

        tk.Label(dialog, text="ЛИЧНЫЙ STEAM WEB API KEY", bg="#191f28", fg="#48a7ff",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=18, pady=(16, 8))
        tk.Label(dialog, text="Ключ нужен для поиска по каталогу Workshop. Он хранится локально в зашифрованном виде Windows DPAPI и не включается в EXE.",
                 bg="#191f28", fg="#97a6b8", font=("Segoe UI", 8), wraplength=480,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        key_var = tk.StringVar(value=self._steam_api_key)
        entry = tk.Entry(dialog, textvariable=key_var, show="●", bg="#111720", fg="#edf2f7",
                         insertbackground="#edf2f7", relief="flat", font=("Consolas", 10),
                         highlightthickness=1, highlightbackground="#2b3543")
        entry.pack(fill="x", padx=18, ipady=7)
        entry.focus_set()
        buttons = tk.Frame(dialog, bg="#191f28")
        buttons.pack(fill="x", padx=18, pady=14)

        def save_key():
            value = key_var.get().strip()
            if not re.fullmatch(r"[A-Fa-f0-9]{32}", value):
                messagebox.showerror("Steam API key", "Ключ должен содержать 32 шестнадцатеричных символа.", parent=dialog)
                return
            try:
                self._api_key_store.save(value)
                self._steam_api_key = value
                self.workshop_status_var.set("Steam API key сохранён локально.")
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("Steam API key", f"Не удалось безопасно сохранить ключ: {exc}", parent=dialog)

        def clear_key():
            self._api_key_store.delete()
            self._steam_api_key = ""
            key_var.set("")
            self.workshop_status_var.set("Steam API key удалён.")

        tk.Button(buttons, text="ПОЛУЧИТЬ КЛЮЧ ↗", command=lambda: webbrowser.open("https://steamcommunity.com/dev/apikey"),
                  bg="#234763", fg="white", activebackground="#2c6288",
                  relief="flat", padx=12, pady=7).pack(side="left", padx=(0, 7))
        tk.Button(buttons, text="СОХРАНИТЬ", command=save_key, bg="#176c53", fg="white",
                  activebackground="#218b6c", relief="flat", padx=14, pady=7).pack(side="left")
        tk.Button(buttons, text="УДАЛИТЬ КЛЮЧ", command=clear_key, bg="#57323a", fg="white",
                  activebackground="#77434f", relief="flat", padx=14, pady=7).pack(side="left", padx=8)
        tk.Button(buttons, text="ОТМЕНА", command=dialog.destroy, bg="#202834", fg="white",
                  activebackground="#344154", relief="flat", padx=14, pady=7).pack(side="right")

    def _on_workshop_type_changed(self, _event=None):
        kind = "Коллекции / модпаки" if self.workshop_type_var.get() not in ("Моды", "Mods") else "Моды"
        self.workshop_search_btn.config(text=f"НАЙТИ {kind.upper()}")
        self.workshop_status_var.set(f"Каталог: {kind.lower()}. Выполни поиск.")

    def search_workshop(self):
        if self._workshop_job_running or self._profile_job_running or self._steamcmd_job_running:
            return
        if not self._steam_api_key:
            messagebox.showinfo("Steam Workshop", "Сначала добавь личный Steam Web API key через кнопку API KEY.")
            self.configure_steam_api_key()
            return
        query = self.workshop_search_var.get().strip()
        sort_code = self._workshop_sort_code(self.workshop_sort_var.get())
        self.workshop_last_query = query
        self.workshop_last_sort = sort_code
        self.workshop_last_type = "collections" if self.workshop_type_var.get() not in ("Моды", "Mods") else "items"
        self.workshop_append_results = False
        self.workshop_next_cursor = None
        self._start_workshop_search(cursor="*")

    def load_more_workshop(self):
        if self._workshop_job_running or not self.workshop_next_cursor:
            return
        self.workshop_append_results = True
        self._start_workshop_search(cursor=self.workshop_next_cursor)

    def _start_workshop_search(self, cursor: str):
        self._workshop_job_running = True
        self.workshop_search_btn.config(state="disabled")
        self.workshop_more_btn.config(state="disabled")
        for download_button in self._workshop_buttons.values():
            download_button.config(state="disabled")
        type_label = "коллекции" if self.workshop_last_type == "collections" else "моды"
        self.workshop_status_var.set(f"Ищу {type_label} в Steam Workshop…")
        core = self._get_core()
        query = self.workshop_last_query
        sort_code = self.workshop_last_sort
        api_key = self._steam_api_key
        append = self.workshop_append_results
        content_type = self.workshop_last_type
        threading.Thread(
            target=self._worker_search_workshop,
            args=(core, query, api_key, sort_code, cursor, append, content_type),
            daemon=True,
        ).start()

    def _worker_search_workshop(self, core, query, api_key, sort_code, cursor, append, content_type="items"):
        try:
            items, next_cursor = core.query_workshop(
                query, api_key, sort_code, cursor=cursor, content_type=content_type
            )
            self.after(0, lambda: self._display_workshop_results(items, next_cursor, append))
        except Exception as exc:
            error = str(exc)
            self.log(f"[Workshop Search] {error}")
            if not self._closing:
                self.after(0, lambda error=error: self.workshop_status_var.set(f"Ошибка поиска: {error}"))
                if "key" in error.lower() or "api" in error.lower() or "steam" in error.lower():
                    self.after(0, lambda error=error: messagebox.showerror("Поиск Steam Workshop", error))
        finally:
            if not self._closing:
                self.after(0, self._finish_workshop_search)

    def _finish_workshop_search(self):
        self._workshop_job_running = False
        self.workshop_search_btn.config(state="normal")
        self.workshop_more_btn.config(state="normal" if self.workshop_next_cursor else "disabled")
        self._refresh_workshop_card_states()

    def _display_workshop_results(self, items: list[dict], next_cursor: str | None, append: bool):
        if not append:
            for child in self.workshop_results_frame.winfo_children():
                child.destroy()
            self._workshop_results.clear()
            self._workshop_buttons.clear()
            self._workshop_image_refs.clear()
        if not items and not self._workshop_results:
            tk.Label(self.workshop_results_frame, text="Ничего не найдено. Попробуй изменить запрос.",
                     bg="#111720", fg="#97a6b8", font=("Segoe UI", 10)).pack(pady=35)
        for item in items:
            mid = str(item.get("id", ""))
            if not mid.isdigit() or mid in self._workshop_results:
                continue
            self._workshop_results[mid] = item
            self._create_workshop_card(item)
        self.workshop_next_cursor = next_cursor
        count = len(self._workshop_results)
        self.workshop_status_var.set(f"Показано: {count} · ещё есть" if next_cursor else f"Показано: {count}")
        self.workshop_more_btn.config(state="normal" if next_cursor else "disabled")

    def _create_workshop_card(self, item: dict):
        mid = str(item["id"])
        card_frame = tk.Frame(self.workshop_results_frame, bg="#19232f",
                              highlightbackground="#2b3543", highlightthickness=1, padx=11, pady=9)
        card_frame.pack(fill="x", padx=8, pady=(8, 0))
        card_frame.columnconfigure(1, weight=1)

        preview = tk.Label(card_frame, text="WORKSHOP", bg="#111720", fg="#61758d",
                           font=("Segoe UI", 8, "bold"), width=17, height=5,
                           relief="flat", bd=0)
        preview.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 12))

        info = tk.Frame(card_frame, bg="#19232f")
        info.grid(row=0, column=1, sticky="nsew")
        info.columnconfigure(0, weight=1)
        title = str(item.get("title") or f"Workshop {mid}")
        title_label = tk.Label(
            info, text=title, bg="#19232f", fg="#edf2f7",
            font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
            wraplength=700,
        )
        title_label._bmm_skip_i18n = True
        title_label.grid(row=0, column=0, sticky="we")
        updated = int(item.get("time_updated", 0) or 0)
        try:
            date_text = datetime.fromtimestamp(updated).strftime("%d.%m.%Y") if updated else "дата неизвестна"
        except (ValueError, OSError, OverflowError):
            date_text = "дата неизвестна"
        subs = int(item.get("subscriptions", 0) or 0)
        creator_id = str(item.get("creator") or "")
        author_label = f"SteamID: {creator_id}" if creator_id.isdigit() else (creator_id or "автор не указан")
        tags = ", ".join(item.get("tags", [])[:4])
        if self.language == "en":
            date_text = self._tr(date_text)
            author_label = self._tr(author_label)
            if item.get("is_collection"):
                meta_text = (
                    f"COLLECTION  ·  ID {mid}  ·  {author_label}  ·  "
                    f"items: {int(item.get('children_count', 0) or 0)}  ·  updated: {date_text}"
                )
            else:
                meta_text = (
                    f"ID {mid}  ·  {author_label}  ·  subscribers: {subs:,}  ·  "
                    f"updated: {date_text}"
                )
        elif item.get("is_collection"):
            meta_text = f"КОЛЛЕКЦИЯ  ·  ID {mid}  ·  {author_label}  ·  элементов: {int(item.get('children_count', 0) or 0)}  ·  обновлена: {date_text}"
        else:
            meta_text = f"ID {mid}  ·  {author_label}  ·  подписок: {subs:,}  ·  обновлён: {date_text}"
        if tags:
            meta_text += f"  ·  {tags}"
        meta_label = tk.Label(
            info, text=meta_text, bg="#19232f", fg="#8fa2b8",
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=700,
        )
        meta_label._bmm_skip_i18n = True
        meta_label.grid(row=1, column=0, sticky="we", pady=(4, 4))
        description = re.sub(r"\s+", " ", str(item.get("description", "") or "")).strip()
        if description:
            if len(description) > 360:
                description = description[:357].rstrip() + "…"
            description_label = tk.Label(
                info, text=description, bg="#19232f", fg="#c0cad6",
                font=("Segoe UI", 8), anchor="w", justify="left", wraplength=700,
            )
            description_label._bmm_skip_i18n = True
            description_label.grid(row=2, column=0, sticky="we", pady=(0, 6))

        actions = tk.Frame(card_frame, bg="#19232f")
        actions.grid(row=0, column=2, rowspan=4, sticky="e", padx=(12, 0))
        tk.Button(actions, text="СТРАНИЦА ↗",
                  command=lambda mod=item: self.open_workshop_item(mod),
                  bg="#234763", fg="#edf2f7", activebackground="#2c6288",
                  relief="flat", cursor="hand2", padx=12, pady=5,
                  font=("Segoe UI", 8, "bold")).pack(fill="x", pady=(0, 6))
        download_label = self._workshop_card_label(mid)
        download_action = self.download_workshop_collection if item.get("is_collection") else self.download_workshop_item
        download_button = tk.Button(
            actions, text=download_label,
            command=lambda mod=item, action=download_action: action(mod),
            bg="#176c53", fg="#ffffff", activebackground="#218b6c",
            relief="flat", cursor="hand2", padx=12, pady=5,
            font=("Segoe UI", 8, "bold"),
        )
        download_button.pack(fill="x")
        self._workshop_buttons[mid] = download_button
        download_button.config(
            state="disabled" if download_label in ("УЖЕ В ПРОФИЛЕ", "КОЛЛЕКЦИЯ В ПРОФИЛЕ") else "normal"
        )

        preview_url = str(item.get("preview_url", "") or "")
        if Image is not None and ImageTk is not None and preview_url:
            threading.Thread(
                target=self._worker_load_workshop_preview,
                args=(mid, preview_url), daemon=True,
            ).start()

    def _worker_load_workshop_preview(self, mod_id: str, preview_url: str):
        try:
            parsed = urlparse(preview_url)
            host = (parsed.hostname or "").lower()
            allowed = (
                host == "steamusercontent.com"
                or host.endswith(".steamusercontent.com")
                or host == "steamuserimages-a.akamaihd.net"
                or host.endswith(".steamstatic.com")
            )
            if parsed.scheme != "https" or not allowed:
                return
            data = bytearray()
            with requests.get(preview_url, stream=True, timeout=(5, 15), allow_redirects=False) as response:
                if response.is_redirect:
                    return
                response.raise_for_status()
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    data.extend(chunk)
                    if len(data) > 5 * 1024 * 1024:
                        return
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 20_000_000:
                    return
                image = image.convert("RGB")
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                image.thumbnail((136, 82), resampling)
                image_copy = image.copy()
            if not self._closing:
                self.after(0, lambda image=image_copy: self._apply_workshop_preview(mod_id, image))
        except Exception:
            return

    def _apply_workshop_preview(self, mod_id: str, image):
        if self._closing or mod_id not in self._workshop_buttons:
            return
        try:
            photo = ImageTk.PhotoImage(image)
            # Find the card's left preview label by searching the widget tree.
            button = self._workshop_buttons[mod_id]
            card_frame = button.master.master
            preview_label = card_frame.grid_slaves(row=0, column=0)
            if preview_label:
                preview_label[0].configure(image=photo, text="", width=136, height=82)
                self._workshop_image_refs[mod_id] = photo
        except (tk.TclError, KeyError, AttributeError):
            pass

    def open_workshop_item(self, item: dict):
        mid = str(item.get("id", ""))
        if mid.isdigit():
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mid}")

    def download_workshop_item(self, item: dict):
        if self._profile_job_running or self._workshop_job_running or self._steamcmd_job_running:
            return
        core = self._get_core()
        if not core.game_dir.is_dir() or not (core.game_dir / "Barotrauma.dll").exists() and not (core.game_dir / "Barotrauma.exe").exists():
            messagebox.showwarning("Папка игры", "Укажи существующую папку Barotrauma в верхней части окна.")
            return
        mid = str(item.get("id", ""))
        if not mid.isdigit():
            return
        profile_name = self.store.data["active_profile"]
        existing = self.store.get_profile_mods(profile_name)
        self._set_profile_job(True)
        self._workshop_job_running = True
        self.workshop_search_btn.config(state="disabled")
        self.workshop_more_btn.config(state="disabled")
        for download_button in self._workshop_buttons.values():
            download_button.config(state="disabled")
        button = self._workshop_buttons.get(mid)
        if button is not None:
            button.config(state="disabled", text="ЗАГРУЗКА…")
        threading.Thread(
            target=self._worker_download_workshop_item,
            args=(core, dict(item), profile_name, existing),
            daemon=True,
        ).start()

    def _worker_download_workshop_item(self, core, item, profile_name, existing_mods):
        mid = str(item["id"])
        error = None
        try:
            self.log(f"[Workshop] Загружаю «{item.get('title', mid)}» ({mid})…")
            package = core.local_mods_dir / mid
            # Always initialize metadata. If the mod is already in LocalMods,
            # downloading is skipped but the item still must enter the profile.
            details = dict(item)

            if not (package / "filelist.xml").is_file():
                try:
                    fetched = core.fetch_mods_metadata([mid]).get(mid, {})
                    # Keep fields returned by QueryFiles when the details endpoint
                    # omits optional fields (notably file_url and creator).
                    details.update({
                        key: value for key, value in fetched.items()
                        if value not in (None, "", [], {})
                    })
                except Exception as metadata_error:
                    # A temporary metadata outage must not prevent the SteamCMD
                    # fallback. QueryFiles already supplied a validated Workshop ID.
                    self.log(
                        f"[Workshop] Не удалось получить дополнительные метаданные "
                        f"({metadata_error}); использую доступные данные и SteamCMD при необходимости."
                    )

                if details.get("api_error") and not details.get("file_url"):
                    self.log(
                        f"[Workshop] Метаданные ограничены: {details['api_error']}; "
                        "пробую штатный загрузчик."
                    )
                failed = core.acquire_mods([mid], metadata={mid: details})
                if mid in failed:
                    raise RuntimeError(failed[mid])
            else:
                self.log(f"[Workshop] {mid}: уже присутствует в LocalMods; повторная загрузка не нужна.")

            if not (package / "filelist.xml").is_file():
                raise RuntimeError("После загрузки не найден LocalMods/<ID>/filelist.xml.")

            merged = {str(mod.get("id")): dict(mod) for mod in existing_mods}
            combined = dict(item)
            combined.update({key: value for key, value in details.items() if value not in (None, "")})
            combined["id"] = mid
            combined["update_status"] = "OK"
            merged[mid] = combined

            source_info = self.store.get_profile_source(profile_name)
            if source_info.get("format") == "Barotrauma ModList" and source_info.get("order_mode") in ("author", "manual"):
                # Do not silently rewrite an imported/hand-edited author order when a new
                # Workshop item is added; keep existing entries in place and append new IDs.
                ordered = [merged[existing_id] for existing_id in
                           [str(entry.get("id")) for entry in existing_mods]
                           if existing_id in merged]
                ordered_ids = {str(entry.get("id")) for entry in ordered}
                ordered.extend(entry for entry_id, entry in merged.items() if entry_id not in ordered_ids)
            else:
                ordered = core.compute_priority_order(list(merged.values()))
            self.store.set_profile_mods(profile_name, ordered)
            self.store.mark_mod_updated(mid, int(combined.get("time_updated", 0) or 0))
            self.store.save()

            installed = [
                mod for mod in ordered
                if (core.local_mods_dir / str(mod["id"]) / "filelist.xml").is_file()
            ]
            if self.store.data.get("active_profile") == profile_name:
                try:
                    core.apply_to_config_player(installed)
                except Exception as apply_error:
                    self.log(
                        "[Предупреждение] Мод скачан и профиль сохранён, "
                        f"но config_player.xml не обновлён: {apply_error}"
                    )

            self.log(
                f"[✔] «{combined.get('title', mid)}» находится в LocalMods "
                f"и добавлен в профиль «{profile_name}»."
            )
            if not self._closing:
                self.after(0, self._refresh_mod_listbox)
                self.after(0, lambda: self._set_workshop_card_downloaded(mid))
        except Exception as exc:
            error = str(exc)
            self.log(f"[Ошибка Workshop download] {mid}: {error}")
        finally:
            if not self._closing:
                def finish():
                    self._workshop_job_running = False
                    self._set_profile_job(False)
                    self.workshop_search_btn.config(state="normal")
                    self.workshop_more_btn.config(
                        state="normal" if self.workshop_next_cursor else "disabled"
                    )
                    self._refresh_workshop_card_states()
                    if error:
                        messagebox.showerror("Загрузка мода", error)
                self.after(0, finish)

    def download_workshop_collection(self, item: dict):
        if self._profile_job_running or self._workshop_job_running or self._steamcmd_job_running:
            return
        core = self._get_core()
        game_valid = core.game_dir.is_dir() and (
            (core.game_dir / "Barotrauma.dll").is_file()
            or (core.game_dir / "Barotrauma.exe").is_file()
        )
        if not game_valid:
            messagebox.showwarning("Папка игры", "Укажи существующую папку Barotrauma перед импортом коллекции.")
            return
        collection_id = str(item.get("id", ""))
        if not collection_id.isdigit():
            return

        profile_name = self.store.data["active_profile"]
        existing = self.store.get_profile_mods(profile_name)
        self._set_profile_job(True)
        self._workshop_job_running = True
        self.workshop_search_btn.config(state="disabled")
        self.workshop_more_btn.config(state="disabled")
        for card_button in self._workshop_buttons.values():
            card_button.config(state="disabled")
        selected_button = self._workshop_buttons.get(collection_id)
        if selected_button:
            selected_button.config(text="ЗАГРУЗКА КОЛЛЕКЦИИ…")

        threading.Thread(
            target=self._worker_download_workshop_collection,
            args=(core, dict(item), profile_name, existing),
            daemon=True,
        ).start()

    def _worker_download_workshop_collection(self, core, collection, profile_name, existing_mods):
        collection_id = str(collection.get("id", ""))
        error = None
        try:
            started = time.time()
            self.log(f"[Workshop] Получаю состав коллекции «{collection.get('title', collection_id)}»…")
            mod_ids = core.fetch_collection_items(collection_id)
            if not mod_ids:
                raise RuntimeError("Steam не вернул моды из этой коллекции.")
            self.log(f"[Workshop] В коллекции найдено {len(mod_ids)} уникальных элементов.")

            try:
                metadata = core.resolve_all_dependencies(mod_ids)
            except Exception as metadata_error:
                self.log(f"[Workshop] Не удалось получить часть метаданных ({metadata_error}); запускаю загрузчик по ID.")
                metadata = {
                    mid: {"id": mid, "title": f"Workshop_{mid}", "dependencies": []}
                    for mid in mod_ids
                }
            for mid in mod_ids:
                metadata.setdefault(mid, {"id": mid, "title": f"Workshop_{mid}", "dependencies": []})

            failed = core.acquire_mods(mod_ids, metadata=metadata)
            merged = {str(mod.get("id")): dict(mod) for mod in existing_mods}
            for mid in mod_ids:
                item = dict(metadata.get(mid, {"id": mid, "title": f"Workshop_{mid}"}))
                item["id"] = mid
                if mid in failed:
                    item["update_status"] = "MISSING"
                else:
                    item["update_status"] = "OK"
                    core_mark_time = int(item.get("time_updated", 0) or 0)
                    self.store.mark_mod_updated(mid, core_mark_time)
                merged[mid] = item

            source_info = self.store.get_profile_source(profile_name)
            if source_info.get("format") == "Barotrauma ModList" and source_info.get("order_mode") in ("author", "manual"):
                ordered = [merged[existing_id] for existing_id in
                           [str(entry.get("id")) for entry in existing_mods]
                           if existing_id in merged]
                ordered_ids = {str(entry.get("id")) for entry in ordered}
                ordered.extend(entry for entry_id, entry in merged.items() if entry_id not in ordered_ids)
            else:
                ordered = core.compute_priority_order(list(merged.values()))
            self.store.set_profile_mods(profile_name, ordered)
            self.store.save()
            if self.store.data.get("active_profile") == profile_name:
                installed = [
                    mod for mod in ordered
                    if find_filelist_root(core.local_mods_dir / str(mod.get("id", "")))
                ]
                try:
                    core.apply_to_config_player(installed)
                except Exception as apply_error:
                    self.log(f"[Предупреждение] Профиль сохранён, но игра не обновлена: {apply_error}")

            successes = len(mod_ids) - len(failed)
            self.log(
                f"[✔] Коллекция обработана: {successes}/{len(mod_ids)} модов за {time.time() - started:.0f} с."
            )
            self._report_failed(failed, metadata)
            if not self._closing:
                self.after(0, self._refresh_mod_listbox)
        except Exception as exc:
            error = str(exc)
            self.log(f"[Ошибка импорта коллекции] {collection_id}: {error}")
        finally:
            if not self._closing:
                def finish_collection():
                    self._workshop_job_running = False
                    self._set_profile_job(False)
                    self.workshop_search_btn.config(state="normal")
                    self.workshop_more_btn.config(
                        state="normal" if self.workshop_next_cursor else "disabled"
                    )
                    self._refresh_workshop_card_states()
                    if error:
                        messagebox.showerror("Импорт коллекции", error)
                self.after(0, finish_collection)

    def _set_workshop_card_downloaded(self, mod_id: str):
        button = self._workshop_buttons.get(str(mod_id))
        if button is not None:
            label = self._workshop_card_label(str(mod_id))
            button.config(
                text=label,
                state="disabled" if label in ("УЖЕ В ПРОФИЛЕ", "КОЛЛЕКЦИЯ В ПРОФИЛЕ") else "normal",
            )

    def open_pasted_workshop_item(self):
        try:
            target = self.workshop_var.get().strip()
            mid = BaroModManagerCore.extract_steam_id(target)
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mid}")
        except Exception as exc:
            messagebox.showwarning("Workshop", str(exc))

    def open_workshop(self):
        webbrowser.open("https://steamcommunity.com/workshop/browse/?appid=602960")

    def open_selected_workshop_item(self):
        index = self._selected_mod_index()
        if index is None:
            self.open_workshop()
            return
        mods = self.store.get_active_mods()
        if index >= len(mods):
            return
        mod_id = str(mods[index].get("id", ""))
        if mod_id.isdigit():
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}")

    def on_profile_switch(self, _event=None):
        if self._profile_job_running:
            return
        selected = self.profile_var.get()
        self.store.data["active_profile"] = selected
        self.store.save()
        self._refresh_profile_selector()
        self._refresh_mod_listbox()
        self._refresh_workshop_card_states()

    def create_profile(self):
        if self._profile_job_running:
            return
        name = simpledialog.askstring("Новый профиль", "Имя профиля:")
        if not name or not name.strip():
            return
        name = name.strip()
        if len(name) > 40:
            messagebox.showwarning("Профиль", "Имя профиля должно быть не длиннее 40 символов.")
            return
        if name in self.store.data["profiles"]:
            messagebox.showwarning("Профиль", "Профиль с таким именем уже существует.")
            return
        self.store.data["profiles"][name] = []
        self.store.data.setdefault("profile_sources", {}).pop(name, None)
        self.store.data["active_profile"] = name
        self.store.save()
        self._refresh_profile_selector()
        self._refresh_mod_listbox()
        self._refresh_workshop_card_states()
    def duplicate_profile(self):
        if self._profile_job_running:
            return
        current = self.store.data["active_profile"]
        name = simpledialog.askstring("Копировать профиль", f"Имя копии для «{current}»:")
        if not name or not name.strip():
            return
        name = name.strip()
        if len(name) > 40:
            messagebox.showwarning("Профиль", "Имя профиля должно быть не длиннее 40 символов.")
            return
        if name in self.store.data["profiles"]:
            messagebox.showwarning("Профиль", "Такой профиль уже существует — он не будет перезаписан.")
            return
        self.store.data["profiles"][name] = self.store.get_profile_mods(current)
        source_info = self.store.data.setdefault("profile_sources", {}).get(current)
        if isinstance(source_info, dict):
            self.store.data["profile_sources"][name] = dict(source_info)
        self.store.data["active_profile"] = name
        self.store.save()
        self._refresh_profile_selector()
        self._refresh_mod_listbox()
        self._refresh_workshop_card_states()
    def delete_profile(self):
        if self._profile_job_running:
            return
        cur = self.store.data["active_profile"]
        if len(self.store.data["profiles"]) <= 1:
            return
        if messagebox.askyesno("Удаление", f"Удалить профиль «{cur}»?"):
            del self.store.data["profiles"][cur]
            self.store.data.setdefault("profile_sources", {}).pop(cur, None)
            self.store.data["active_profile"] = next(iter(self.store.data["profiles"]))
            self.store.save()
            self._refresh_profile_selector()
            self._refresh_mod_listbox()
            self._refresh_workshop_card_states()

    def move_mod(self, direction: int):
        if self._profile_job_running:
            return
        idx = self._selected_mod_index()
        if idx is None:
            return
        mods = self.store.get_active_mods()
        new_idx = idx + direction
        if 0 <= new_idx < len(mods):
            mods[idx], mods[new_idx] = mods[new_idx], mods[idx]
            self.store.set_active_mods(mods)
            source = self.store.data.setdefault("profile_sources", {}).get(self.store.data["active_profile"])
            if isinstance(source, dict) and source.get("format") == "Barotrauma ModList":
                source["order_mode"] = "manual"
                source["ordered_ids"] = [str(item.get("id")) for item in mods]
                self.store.save()
            self._update_profile_order_indicator()
            self._refresh_mod_listbox()
            rows = self.mod_listbox.get_children()
            if 0 <= new_idx < len(rows):
                self.mod_listbox.selection_set(rows[new_idx])
                self.mod_listbox.focus(rows[new_idx])
                self.mod_listbox.see(rows[new_idx])

    def show_mod_context_menu(self, event=None):
        """Open secondary actions for the row under the pointer or current selection."""
        if self._profile_job_running or not hasattr(self, "mod_context_menu"):
            return "break"
        try:
            if event is not None and hasattr(event, "y"):
                row_id = self.mod_listbox.identify_row(event.y)
                if row_id:
                    self.mod_listbox.selection_set(row_id)
                    self.mod_listbox.focus(row_id)
            selected = self.mod_listbox.selection()
            if not selected:
                return "break"
            if event is not None and hasattr(event, "x_root"):
                self.mod_context_menu.tk_popup(event.x_root, event.y_root)
            else:
                bounds = self.mod_listbox.bbox(selected[0])
                if bounds:
                    x, y, _width, height = bounds
                    self.mod_context_menu.tk_popup(
                        self.mod_listbox.winfo_rootx() + x,
                        self.mod_listbox.winfo_rooty() + y + height,
                    )
        except tk.TclError:
            pass
        finally:
            try:
                self.mod_context_menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    def remove_selected_mod(self):
        if self._profile_job_running:
            return
        index = self._selected_mod_index()
        if index is None:
            messagebox.showinfo("Удаление мода", "Сначала выбери мод в списке.")
            return
        mods = self.store.get_active_mods()
        if index >= len(mods):
            return
        selected_mod = dict(mods[index])
        mid = str(selected_mod.get("id", ""))
        title = str(selected_mod.get("title", mid))

        full_delete = messagebox.askyesnocancel(
            "Удалить мод",
            f"{title}\nID: {mid}\n\n"
            "ДА — удалить файлы из LocalMods и кэша менеджера, а также убрать мод из ВСЕХ профилей.\n\n"
            "НЕТ — только убрать мод из активного профиля, файлы останутся на диске.\n\n"
            "ОТМЕНА — ничего не менять.",
            parent=self,
        )
        if full_delete is None:
            return

        core = self._get_core()
        try:
            if full_delete:
                removed_paths = core.remove_mod_files(mid)
                for profile_name, profile_mods in list(self.store.data["profiles"].items()):
                    self.store.data["profiles"][profile_name] = [
                        mod for mod in profile_mods if str(mod.get("id", "")) != mid
                    ]
                self.store.data.get("installed_versions", {}).pop(mid, None)
                self.log(f"[Удаление] {title}: очищено каталогов/ссылок — {removed_paths}; удалён из всех профилей.")
            else:
                self.store.data["profiles"][self.store.data["active_profile"]] = [
                    mod for mod in mods if str(mod.get("id", "")) != mid
                ]
                self.log(f"[Профиль] {title}: удалён только из «{self.store.data['active_profile']}».")

            source = self.store.get_profile_source(self.store.data["active_profile"])
            if source.get("format") == "Barotrauma ModList":
                source["order_mode"] = "manual"
                source["ordered_ids"] = [str(item.get("id")) for item in self.store.get_active_mods()]
                self.store.set_profile_source(self.store.data["active_profile"], source)
            self._update_profile_order_indicator()
            self.store.save()
            try:
                core.apply_to_config_player(self.store.get_active_mods())
            except Exception as apply_error:
                self.log(f"[Предупреждение] Профиль сохранён, но config_player.xml не обновлён: {apply_error}")
            self._refresh_mod_listbox()
            self._refresh_workshop_card_states()
        except Exception as exc:
            messagebox.showerror("Удаление мода", str(exc), parent=self)

    def _modlists_dialog_directory(self, create: bool = False) -> Path:
        """Prefer the game's native ModLists folder for preset import/export dialogs."""
        game_text = self.game_dir_var.get().strip() if hasattr(self, "game_dir_var") else ""
        if game_text:
            game_dir = Path(game_text).expanduser()
            modlists_dir = game_dir / "ModLists"
            if create:
                try:
                    modlists_dir.mkdir(parents=True, exist_ok=True)
                    return modlists_dir
                except OSError:
                    pass
            if modlists_dir.is_dir():
                return modlists_dir
            if game_dir.is_dir():
                return game_dir
        return Path.home()

    def import_modlist_xml(self):
        """Import the author's native <mods><Vanilla/><Workshop/></mods> preset."""
        if self._profile_job_running:
            return
        selected_path = filedialog.askopenfilename(
            parent=self,
            title="Импортировать авторский ModList",
            initialdir=str(self._modlists_dialog_directory()),
            filetypes=(("Barotrauma ModLists", "*.xml"), ("XML", "*.xml"), ("Все файлы", "*.*")),
        )
        if not selected_path:
            return
        try:
            preset_name, parsed_mods = parse_barotrauma_modlist(Path(selected_path))
        except Exception as exc:
            messagebox.showerror("Импорт XML", str(exc), parent=self)
            return

        replace_current = messagebox.askyesnocancel(
            "Импорт авторского порядка",
            f"Найден пресет «{preset_name}»: {len(parsed_mods)} модов.\n\n"
            "ДА — заменить текущий список и сохранить порядок автора.\n"
            "НЕТ — поставить моды из XML первыми, остальные текущие моды добавить в конец.\n"
            "ОТМЕНА — ничего не менять.",
            parent=self,
        )
        if replace_current is None:
            return

        profile_name = self.store.data["active_profile"]
        existing_mods = self.store.get_profile_mods(profile_name)
        core = self._get_core()
        self._set_profile_job(True)
        threading.Thread(
            target=self._worker_import_modlist,
            args=(core, Path(selected_path), preset_name, parsed_mods,
                  bool(replace_current), profile_name, existing_mods),
            daemon=True,
        ).start()

    def _worker_import_modlist(self, core, source_path, preset_name, parsed_mods,
                               replace_current, profile_name, existing_mods):
        error = None
        imported_count = 0
        missing_count = 0
        try:
            ids = [str(item["id"]) for item in parsed_mods]
            existing_by_id = {
                str(item.get("id")): dict(item)
                for item in existing_mods if str(item.get("id", "")).isdigit()
            }
            remote_meta = {}
            try:
                remote_meta = core.fetch_mods_metadata(ids)
            except Exception as metadata_error:
                self.log(
                    f"[ModList] Не удалось обновить метаданные Steam ({metadata_error}); "
                    "импортирую ID и названия из XML."
                )

            imported = []
            for index, parsed in enumerate(parsed_mods):
                mid = str(parsed["id"])
                merged = dict(remote_meta.get(mid, {}))
                merged.update(existing_by_id.get(mid, {}))
                # The author's title from the ModList is the best display name for this order.
                merged["id"] = mid
                merged["title"] = str(parsed.get("title") or merged.get("title") or f"Workshop_{mid}")
                merged.setdefault("dependencies", [])
                merged["author_order"] = index
                if find_filelist_root(core.local_mods_dir / mid) or core._find_source(mid):
                    merged["update_status"] = merged.get("update_status", "OK")
                else:
                    merged["update_status"] = "MISSING"
                    missing_count += 1
                imported.append(merged)

            imported_ids = {str(item["id"]) for item in imported}
            if replace_current:
                result = imported
            else:
                result = imported + [
                    dict(item) for item in existing_mods
                    if str(item.get("id", "")) not in imported_ids
                ]
            if not result:
                raise RuntimeError("В XML не найдено ни одного Workshop-мода.")

            self.store.set_profile_mods(profile_name, result)
            self.store.set_profile_source(profile_name, {
                "format": "Barotrauma ModList",
                "source_name": preset_name,
                "source_file": source_path.name,
                "order_mode": "author",
                "ordered_ids": ids,
                "imported_at": int(time.time()),
            })
            imported_count = len(imported)
            self.log(
                f"[ModList] Импортирован «{preset_name}»: {imported_count} авторских позиций; "
                f"отсутствующих локально модов: {missing_count}. Порядок сохранён без автосортировки."
            )
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА ModList] {error}")

        if not self._closing:
            def finish():
                if self.store.data.get("active_profile") == profile_name:
                    self._refresh_profile_selector()
                    self._refresh_mod_listbox()
                    self._refresh_workshop_card_states()
                self._set_profile_job(False)
                if error:
                    messagebox.showerror("Импорт ModList", error, parent=self)
                else:
                    messagebox.showinfo(
                        "Импорт завершён",
                        f"Импортировано модов: {imported_count}.\n"
                        f"Пока не найдено локальных файлов: {missing_count}.\n\n"
                        "Авторский порядок сохранён. Чтобы включить доступные моды, "
                        "нажми «Применить в игру». Для отсутствующих модов используй «Проверить».",
                        parent=self,
                    )
            self.after(0, finish)

    def export_modlist_xml(self):
        """Export active profile as a shareable Barotrauma ModLists XML file."""
        if self._profile_job_running:
            return
        profile_name = str(self.store.data.get("active_profile", "Barotrauma ModList"))
        safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", profile_name).strip(" ._") or "Barotrauma_ModList"
        selected_path = filedialog.asksaveasfilename(
            parent=self,
            title="Экспортировать ModList",
            initialfile=f"{safe_name}.xml",
            initialdir=str(self._modlists_dialog_directory(create=True)),
            defaultextension=".xml",
            filetypes=(("Barotrauma ModLists", "*.xml"), ("Все файлы", "*.*")),
        )
        if not selected_path:
            return
        try:
            payload = serialize_barotrauma_modlist(self.store.get_profile_mods(profile_name), profile_name)
            output = Path(selected_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            temp = output.with_name(output.name + ".tmp")
            temp.write_bytes(payload)
            os.replace(temp, output)
            self.log(f"[ModList] Экспортирован профиль «{profile_name}»: {output}")
            messagebox.showinfo("Экспорт завершён", f"Файл сохранён:\n{output}", parent=self)
        except Exception as exc:
            messagebox.showerror("Экспорт ModList", str(exc), parent=self)

    def _show_priority_preview(self, sorted_mods: list[dict], profile_name: str):
        """Show the proposed order before changing the active profile."""
        dialog = tk.Toplevel(self)
        dialog.title("Предпросмотр автоприоритета")
        dialog.geometry("860x620")
        dialog.minsize(700, 460)
        dialog.configure(bg="#0d131b")
        dialog.transient(self)
        dialog.grab_set()

        panel = tk.Frame(dialog, bg="#151e29", padx=16, pady=14,
                         highlightbackground="#29394a", highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            panel, text="ПРЕДЛАГАЕМЫЙ ПОРЯДОК", bg="#151e29", fg="#65c9f1",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        has_lua = any(str(item.get("id", "")) == "2559634234" for item in sorted_mods)
        order_description = (
            "Lua → CsForBarotrauma → LuaCsForBarotrauma → патчи → утилиты → "
            "крупные модификации → контент → субмарины. Порядок внутри групп сохранён."
        )
        if not has_lua:
            order_description = (
                "Lua For Barotrauma (2559634234) не найден в этом профиле — менеджер не добавляет "
                "его автоматически. Если он будет добавлен, сортировщик всегда поставит его первым.\n\n"
                + order_description
            )
        tk.Label(
            panel,
            text=order_description,
            bg="#151e29", fg="#91a4b7", font=("Segoe UI", 8),
            wraplength=790, justify="left",
        ).pack(anchor="w", pady=(5, 10))

        table_host = tk.Frame(panel, bg="#151e29")
        table_host.pack(fill="both", expand=True)
        table_host.columnconfigure(0, weight=1)
        table_host.rowconfigure(0, weight=1)
        preview = ttk.Treeview(
            table_host, columns=("number", "group", "title", "id"),
            show="headings", style="Baro.Treeview", selectmode="browse",
        )
        for column, caption, width, anchor, stretch in (
            ("number", "№", 48, "center", False),
            ("group", "ГРУППА", 150, "center", False),
            ("title", "МОД", 500, "w", True),
            ("id", "WORKSHOP ID", 120, "center", False),
        ):
            preview.heading(column, text=caption)
            preview.column(column, width=width, anchor=anchor, stretch=stretch)
        preview.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_host, orient="vertical", command=preview.yview,
                               style="Baro.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        preview.configure(yscrollcommand=scroll.set)
        for index, item in enumerate(sorted_mods, 1):
            preview.insert(
                "", "end", values=(index, item.get("tier_label", "CONTENT"),
                                   item.get("title", item.get("id", "")), item.get("id", "")),
            )

        footer = tk.Frame(panel, bg="#151e29")
        footer.pack(fill="x", pady=(12, 0))
        tk.Label(
            footer, text=f"{len(sorted_mods)} модов  ·  Профиль: {profile_name}",
            bg="#151e29", fg="#91a4b7", font=("Segoe UI", 8),
        ).pack(side="left")

        def close_dialog():
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            self._set_profile_job(False)

        def apply_order():
            try:
                if self.store.data.get("active_profile") != profile_name:
                    raise RuntimeError("Активный профиль изменился. Повтори сортировку.")
                self.store.set_profile_mods(profile_name, sorted_mods)
                source = self.store.get_profile_source(profile_name)
                if source.get("format") == "Barotrauma ModList":
                    source["order_mode"] = "automatic"
                    self.store.set_profile_source(profile_name, source)
                self._update_profile_order_indicator()
                self._refresh_mod_listbox()
                self.log("[Приоритеты] Применён новый порядок. Проверь рекомендации авторов совместимости, если они есть.")
                close_dialog()
            except Exception as exc:
                messagebox.showerror("Автоприоритет", str(exc), parent=dialog)

        tk.Button(
            footer, text="ОТМЕНА", command=close_dialog, bg="#273543", fg="#edf4fa",
            activebackground="#34475a", relief="flat", padx=16, pady=8,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side="right", padx=(7, 0))
        tk.Button(
            footer, text="ПРИМЕНИТЬ ПОРЯДОК", command=apply_order,
            bg="#17785d", fg="#ffffff", activebackground="#218e70",
            relief="flat", padx=16, pady=8, font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        ).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    def _worker_auto_sort(self, core, profile_name: str, mods: list[dict]):
        try:
            sorted_mods = core.compute_priority_order(mods)
            if not self._closing:
                self.after(0, lambda: self._show_priority_preview(sorted_mods, profile_name))
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА автоприоритета] {error}")
            if not self._closing:
                def finish_error():
                    self._set_profile_job(False)
                    messagebox.showerror("Автоприоритет", error, parent=self)
                self.after(0, finish_error)

    def auto_sort_current_profile(self):
        if self._profile_job_running:
            return
        profile_name = self.store.data["active_profile"]
        mods = self.store.get_profile_mods(profile_name)
        if not mods:
            return
        source = self.store.get_profile_source(profile_name)
        if source.get("format") == "Barotrauma ModList" and source.get("order_mode") == "author":
            proceed = messagebox.askyesno(
                "Авторский порядок",
                f"Профиль импортирован из авторского XML «{source.get('source_name', source.get('source_file', 'ModList'))}».\n\n"
                "Автоприоритет изменит исходную последовательность автора. Продолжить?",
                parent=self,
            )
            if not proceed:
                return
        core = self._get_core()
        self._set_profile_job(True)
        self.log("[Приоритеты] Анализирую типы пакетов и формирую предварительный порядок…")
        threading.Thread(
            target=self._worker_auto_sort,
            args=(core, profile_name, mods),
            daemon=True,
        ).start()

    def check_and_update_mods(self):
        if self._profile_job_running:
            return
        profile_name = self.store.data["active_profile"]
        mods = self.store.get_profile_mods(profile_name)
        if not mods:
            messagebox.showinfo("Проверка обновлений", "В текущем профиле пока нет модов.")
            return
        core = self._get_core()
        versions = dict(self.store.data.get("installed_versions", {}))
        self._set_profile_job(True)
        threading.Thread(
            target=self._worker_check_updates,
            args=(core, profile_name, mods, versions),
            daemon=True,
        ).start()
    def _worker_check_updates(self, core: BaroModManagerCore, profile_name: str,
                              mods: list[dict], installed_versions: dict):
        try:
            self.log("[Steam API] Сверка опубликованных версий с локальными модами...")
            updated_mods, outdated = core.check_updates_for_mods(mods, installed_versions)
            self.store.set_profile_mods(profile_name, updated_mods)
            if self.store.data.get("active_profile") == profile_name:
                self.after(0, self._refresh_mod_listbox)

            unknown_count = sum(1 for item in updated_mods if item.get("update_status") == "UNKNOWN")
            if not outdated:
                message = "Установленные моды актуальны."
                if unknown_count:
                    message += f"\nДля {unknown_count} модов Steam не отдал достаточно данных для сравнения."
                self.log("[✔] Проверка завершена: загружаемых обновлений не найдено.")
                self.after(0, lambda: messagebox.showinfo("Проверка завершена", message))
                return

            preview = "\n".join(
                f"• {item.get('title', item['id'])} ({item['id']})" for item in outdated[:12]
            )
            if len(outdated) > 12:
                preview += f"\n...и ещё {len(outdated) - 12}"
            should_download = self._ask(lambda: messagebox.askyesno(
                "Обновления Workshop",
                f"Нужно обновить или загрузить {len(outdated)} модов:\n\n{preview}\n\nПродолжить?",
            ))
            if not should_download:
                return

            ids = [str(item["id"]) for item in outdated]
            metadata = {str(item["id"]): item for item in outdated}
            failed = core.acquire_mods(ids, metadata=metadata, force_refresh_ids=ids)
            for item in updated_mods:
                mid = str(item["id"])
                if mid in ids and mid not in failed:
                    item["update_status"] = "OK"
                    self.store.mark_mod_updated(mid, int(item.get("time_updated", 0) or 0))
                elif mid in failed:
                    still_installed = (core.local_mods_dir / mid / "filelist.xml").is_file()
                    item["update_status"] = "OUTDATED" if still_installed else "MISSING"
            self.store.set_profile_mods(profile_name, updated_mods)
            self.store.save()
            if self.store.data.get("active_profile") == profile_name:
                self.after(0, self._refresh_mod_listbox)
            self.log(f"[✔] Успешно обработано: {len(ids) - len(failed)} из {len(ids)}.")
            self._report_failed(failed, {str(item["id"]): item for item in updated_mods})
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА обновления] {error}")
            if not self._closing:
                self.after(0, lambda error=error: messagebox.showerror("Обновление модов", error))
        finally:
            if not self._closing:
                self.after(0, lambda: self._set_profile_job(False))
    def run_conflict_detector(self):
        if self._profile_job_running:
            return
        try:
            core = self._get_core()
            mods = self.store.get_active_mods()
            if not mods:
                return
            conflicts = core.detect_conflicts(mods)

            win = tk.Toplevel(self)
            win.title("Детектор конфликтов XML")
            win.geometry("750x500")
            win.configure(bg="#18181b")

            tk.Label(
                win,
                text=f"Конфликтующих идентификаторов: {len(conflicts)}",
                bg="#18181b",
                fg="#f4f4f5",
                font=("Segoe UI", 11, "bold"),
            ).pack(anchor="w", padx=12, pady=10)
            text_area = scrolledtext.ScrolledText(win, bg="#09090b", fg="#e4e4e7", font=("Consolas", 9), relief="flat")
            text_area.pack(fill="both", expand=True, padx=12, pady=(0, 12))

            if not conflicts:
                text_area.insert(tk.END, "Конфликтов не обнаружено.\n")
            else:
                for ident, owners in conflicts.items():
                    text_area.insert(tk.END, f"• ID: [ {ident} ]\n  Перезаписывают моды:\n")
                    for o_title, o_id in owners:
                        text_area.insert(tk.END, f"    - {o_title} ({o_id})\n")
                    text_area.insert(tk.END, "-" * 50 + "\n")
            text_area.config(state="disabled")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def apply_active_profile_to_game(self):
        if self._profile_job_running:
            return
        try:
            core = self._get_core()
            mods = self.store.get_active_mods()
            core.apply_to_config_player(mods)
            messagebox.showinfo("Готово", "Профиль применен в config_player.xml!")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def download_and_add_to_profile(self):
        if self._profile_job_running:
            return
        target = self.workshop_var.get().strip()
        if not target or target.startswith(("Ссылка на мод", "Paste a Workshop link")):
            messagebox.showinfo("Steam Workshop", "Вставьте ссылку на мод, коллекцию или числовой ID.")
            return
        core = self._get_core()
        if not core.game_dir.is_dir():
            messagebox.showwarning("Папка игры", "Укажите существующую папку Barotrauma.")
            return
        profile_name = self.store.data["active_profile"]
        existing_ids = [str(item.get("id", "")) for item in self.store.get_profile_mods(profile_name)]
        self._set_profile_job(True)
        threading.Thread(
            target=self._worker_add_workshop,
            args=(target, core, profile_name, existing_ids),
            daemon=True,
        ).start()
    def _report_failed(self, failed: dict, meta: dict):
        if not failed:
            return
        self.log(f"[!] Не удалось скачать {len(failed)} шт.:")
        for mid, reason in failed.items():
            title = meta.get(mid, {}).get("title", mid)
            self.log(f"    - {title} ({mid}): {reason}")
        self.log(
            "[i] Если у мода «Access Denied» — подпишитесь на него в клиенте Steam (Steam должен быть запущен, "
            "аккаунт с Barotrauma) и нажмите кнопку ещё раз: менеджер возьмёт мод из папки Steam."
        )

    def _worker_add_workshop(self, target: str, core: BaroModManagerCore,
                             profile_name: str, existing_ids: list[str]):
        try:
            started = time.time()
            target_id = core.extract_steam_id(target)
            self.log(f"[Steam API] Получаю состав элемента {target_id}...")
            imported_ids = core.fetch_collection_items(target_id)
            combined_ids = list(dict.fromkeys(existing_ids + imported_ids))
            if not combined_ids:
                raise RuntimeError("Steam API не вернул ни одного Workshop ID.")

            metadata = core.resolve_all_dependencies(combined_ids)
            unavailable = {
                mid: item.get("api_error", "Элемент недоступен через Steam API.")
                for mid, item in metadata.items() if not item.get("available", True)
            }
            for mid, reason in unavailable.items():
                self.log(f"[Workshop] {mid}: {reason}")

            to_acquire = list(metadata)
            self.log(f"[Steam API] Получены метаданные: {len(metadata)}; проверяю/загружаю все элементы.")
            # Даже если API не отдал метаданные, SteamCMD или установленный клиент
            # могут иметь доступ к самому файлу. Источник проверяется отдельно.
            failed = core.acquire_mods(to_acquire, metadata=metadata) if to_acquire else {}

            for mid, item in metadata.items():
                if mid in failed:
                    still_installed = (core.local_mods_dir / mid / "filelist.xml").is_file()
                    item["update_status"] = "OUTDATED" if still_installed else "MISSING"
                elif core._find_source(mid):
                    item["update_status"] = "OK"
                    self.store.mark_mod_updated(mid, int(item.get("time_updated", 0) or 0))
                else:
                    item["update_status"] = "MISSING"
                    failed[mid] = "После загрузки не найден filelist.xml."

            source_info = self.store.get_profile_source(profile_name)
            if source_info.get("format") == "Barotrauma ModList" and source_info.get("order_mode") in ("author", "manual"):
                metadata_by_id = {str(item.get("id")): item for item in metadata.values()}
                sorted_mods = [metadata_by_id[mid] for mid in existing_ids if mid in metadata_by_id]
                existing_order_ids = {str(item.get("id")) for item in sorted_mods}
                sorted_mods.extend(item for mid, item in metadata_by_id.items() if mid not in existing_order_ids)
            else:
                sorted_mods = core.compute_priority_order(list(metadata.values()))
            self.store.set_profile_mods(profile_name, sorted_mods)
            self.store.save()

            installed = [
                item for item in sorted_mods
                if (core.local_mods_dir / str(item["id"]) / "filelist.xml").is_file()
            ]
            core.apply_to_config_player(installed)
            elapsed = time.time() - started
            self.log(f"[✔] Профиль «{profile_name}»: установлено и включено {len(installed)} из {len(sorted_mods)} модов за {elapsed:.0f} с.")
            self._report_failed(failed, metadata)
            if self.store.data.get("active_profile") == profile_name and not self._closing:
                self.after(0, self._refresh_mod_listbox)
        except Exception as exc:
            error = str(exc)
            self.log(f"[ОШИБКА импорта] {error}")
            if not self._closing:
                self.after(0, lambda error=error: messagebox.showerror("Импорт Workshop", error))
        finally:
            if not self._closing:
                self.after(0, lambda: self._set_profile_job(False))

if __name__ == "__main__":
    app = ModManagerGUI()
    app.mainloop()