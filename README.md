<div align="center">
  <img src="assets/logo.svg" alt="Barotrauma Mod Manager" width="96">
<h1>Barotrauma Mod Manager</h1>
  <p><strong>A desktop Workshop and load-order manager for Barotrauma.</strong></p>
  <p>
    <a href="README.ru.md">Русский</a> · <a href="README.zh-CN.md">简体中文</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/platform-Windows-2678c8?logo=windows&logoColor=white" alt="Windows">
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/version-2.7.1-17a673" alt="Version 2.7.1">
    <img src="https://img.shields.io/badge/game-Barotrauma-0d6578" alt="Barotrauma">
    <a href="https://github.com/ShiokazeE/Barotrauma-Mod-Manager/actions/workflows/ci.yml"><img src="https://github.com/ShiokazeE/Barotrauma-Mod-Manager/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  </p>
</div>

Barotrauma Mod Manager helps you browse Steam Workshop, install mods and collections, maintain separate load-order profiles, and apply a chosen profile to the game.

> **Project status:** community project in active development. The current source version is 2.7.1. A verified Windows executable will be attached to GitHub Releases after a Windows build has been tested.

Project website: [Barotrauma Mod Manager](https://shiokazee.github.io/Barotrauma-Mod-Manager/) · Source code: [GitHub repository](https://github.com/ShiokazeE/Barotrauma-Mod-Manager)

## Features

- **Workshop browser** — search Barotrauma Workshop items and collections using your own Steam Web API key.
- **One-click install** — download a Workshop item into `LocalMods`, then add it to the selected profile.
- **Collection import** — expand nested collections and resolve available Workshop metadata.
- **Multiple profiles** — create, copy, switch and remove mod profiles.
- **Load-order tools** — manual moves, conservative automatic priority, author ModList XML import/export.
- **Author order preservation** — import Barotrauma's `ModLists/*.xml` without silently reordering it.
- **Update checks** — compare installed versions with Workshop metadata and download updates.
- **Conflict inspection** — scan XML identifiers for possible overlaps.
- **LuaCs helper** — install the client patch with a backup and rollback option.
- **Safer file handling** — archive path validation, staged installs, profile persistence and backups before applying the game configuration.

## Screenshots

Screenshots of the actual Windows application will be added after the current build has been smoke-tested. The repository will not use mock screenshots that could misrepresent the interface.

## Download

Go to **[GitHub Releases](https://github.com/ShiokazeE/Barotrauma-Mod-Manager/releases)** and download the latest Windows package when a verified release is available.

The release package is intended to be portable: extract it and run `BarotraumaModManager.exe`. The manager stores profiles, SteamCMD data and the encrypted API key in the current user's application-data directory, not beside the executable.

## Run from source

Requirements: Windows 10/11 and Python 3.10 or newer.

```powershell
py -3 -m pip install -r requirements.txt
py -3 barotrauma_mod_manager.py
```

Pillow is used for Workshop thumbnail rendering. If it is unavailable, browsing and downloading can still work without thumbnails.

## Build the Windows executable

1. Install Python 3.10+ and enable the Python Launcher (`py`).
2. Keep `barotrauma_mod_manager.py` and `build_exe.bat` in the same folder.
3. Run `build_exe.bat`.
4. The script creates `BarotraumaModManager.exe` next to the BAT file.

The project also contains a GitHub Actions workflow that builds a Windows executable when a `v*` tag is pushed. See [Release workflow](.github/workflows/build-windows.yml).

## Steam Web API key

The Workshop browser uses Steam's `IPublishedFileService/QueryFiles` endpoint. Add your own Steam Web API key in the manager's Workshop settings. The key is stored locally using Windows DPAPI and is not part of the source repository or profile JSON.

- [Steam Web API: IPublishedFileService](https://partner.steamgames.com/doc/webapi/IPublishedFileService)
- [Steam Web API: ISteamRemoteStorage](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage)

**Never commit your API key, profile file, SteamCMD cache, or game folder to Git.** The `.gitignore` excludes common local state and credential files.

## Downloads and Steam access

The manager reuses an existing Steam client cache when possible, tries an allowed Steam CDN URL when the API supplies one, and falls back to SteamCMD. Steam does not provide a direct file URL for every Workshop item. The manager does not bypass ownership, subscription, privacy or access restrictions.

For large collections, the application supports up to three isolated SteamCMD workers with independent caches. More workers do not guarantee faster downloads; use the setting that behaves best on your connection.

## Load order and Barotrauma XML

The manager handles two different XML concepts:

- **`Barotrauma/ModLists/<preset>.xml`** — a shareable author preset with `<mods>`, `<Vanilla />` and ordered `<Workshop ... />` entries. Import preserves the author's sequence; export writes the selected profile in this format.
- **`config_player.xml`** — the local active package configuration. Applying a profile writes ordered `<package path="LocalMods/<ID>/filelist.xml" />` entries under `contentpackages/regularpackages` after creating a backup.

Author-provided order is more authoritative than generic heuristics. The automatic sorter is deliberately conservative, keeps the original relative order within its groups, and always places Lua For Barotrauma (`2559634234`) first when present. Review the preview and keep manual changes where a mod author documents a special requirement.

## Tests

Run the core regression script:

```powershell
py -3 tests/test_barotrauma_mod_manager.py
```

Run the Tkinter Workshop scroll/resize smoke test on a desktop Windows session:

```powershell
py -3 tests/test_workshop_gui_hotfix.py
```

The tests use temporary application-data directories and mocked Steam responses; they do not make live Workshop downloads.

## Troubleshooting

- **Workshop search is unavailable:** verify the API key in Workshop settings and try again when Steam Web API is reachable.
- **A mod fails to download:** make sure the item is public and available to your Steam account, start the Steam client if required, and retry. SteamCMD is used as a fallback.
- **The game does not show the expected order:** review the selected profile, press **Apply to Game**, and check the active `config_player.xml`.
- **A profile looks wrong after an import:** import the original author ModList again; author order is preserved separately from the game's active configuration.

For bug reports, include the manager version, Windows version, a short reproduction sequence and the relevant log lines. **Remove API keys, account details and other private data before attaching logs.**

## Contributing

Bug reports and focused pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

No open-source license has been selected yet. Until a license file is added, all rights remain with the copyright holder; public visibility does not by itself grant permission to redistribute or publish modified versions. A license will be added after the maintainer confirms the desired terms.

## Disclaimer

This is an unofficial community tool. It is not affiliated with, endorsed by, or sponsored by FakeFish, Daedalic Entertainment or Valve. Barotrauma and Steam are trademarks of their respective owners.