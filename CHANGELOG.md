# Changelog

## 2.7.4 — Read-only ZIP package check

- Added a ZIP check button for local archives.
- Validates archive paths, file count, size and compression ratio, checks CRCs and validates the Barotrauma filelist.xml manifest.
- Performs all checks without extracting, installing or activating archive contents.

## 2.7.3 — Direct launch and Windows reliability

- Launch Barotrauma.exe directly from the configured game directory without relying on the Steam URI handler.
- Block game launch while profile, LuaCs, SteamCMD, or Workshop operations are active.
- Report missing game directories and missing Barotrauma.exe paths clearly.
- Restart the manager with subprocess.Popen after a language change; argument lists and cwd are handled safely on Windows paths containing spaces.
- Align both HTTP User-Agent headers with the application version.
- Treat the Russian and English quick-add prompt text as a placeholder instead of a submitted value.
- Exclude packaging and test-only modules from the PyInstaller bundle to reduce unnecessary files.

## 2.7.2 — Resizable UI and Steam launch

- Added Discord-style draggable splitters for the profile sections and Steam Workshop search/results.
- Persisted splitter proportions between launches and restored them after switching tabs.
- Added a one-click Play button that launches Barotrauma through Steam using AppID 602960.
- Added safe Steam Workshop request/response diagnostics, result validation counters, and clearer network error messages.
- Kept Steam API keys out of request diagnostics and error logs.

## 2.7.1 — Windows packaging update

- Replaced the self-extracting PyInstaller --onefile executable with an explicit --onedir application bundle.
- The Windows ZIP contains the executable and its complete runtime directory; preserve the folder structure when extracting.
- Kept the Russian/English first-run language selector and saved per-PC preference.
- Kept the optional Boosty donation link; all manager features remain free.

## 2.7.1 — Workshop scrolling and resize hotfix

- Added a Russian/English first-run language selector and a saved per-PC preference, with a header control to change languages.
- Added a voluntary Boosty donation link; all manager features remain free.
- Fixed mouse-wheel handling over Workshop card children and filtered scrolling to the Workshop results area.
- Added support for high-resolution wheel deltas and X11 wheel buttons.
- Debounced Canvas width and scroll-region recalculation during resize and card rendering.

## 2.7.0 — UI and load-order update

- Compact dark interface with clearer profile and Workshop sections.
- Added author ModList XML import/export.
- Preserved author order and manual order when importing additional content.
- Added conservative load-order preview and explicit order-source indicator.
- Kept active game configuration (config_player.xml) separate from shareable ModLists presets.
