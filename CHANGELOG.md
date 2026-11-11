# Changelog

## 2.7.1 — Windows packaging update

- Replaced the self-extracting PyInstaller --onefile executable with an explicit --onedir application bundle.
- The Windows ZIP now contains the executable and its complete runtime directory; preserve the folder structure when extracting.
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
