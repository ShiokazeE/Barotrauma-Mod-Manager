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

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.data = {
            "schema_version": 3,
            "game_dir": "",
            "steamcmd_path": str(DEFAULT_STEAMCMD_EXE) if DEFAULT_STEAMCMD_EXE.exists() else "",
            "active_profile": "Основной",
            "installed_versions": {},
            "profiles": {"Основной": []},
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
                profiles = {"Основной": []}
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
                normalized_profiles = {"Основной": []}
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