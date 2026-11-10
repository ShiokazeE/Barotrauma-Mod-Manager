# Contributing

Thanks for helping improve Barotrauma Mod Manager.

## Before opening an issue

- Search existing issues for the same problem.
- Use the latest available version when possible.
- Include exact reproduction steps and relevant log lines.
- Remove Steam API keys, account identifiers and personal paths from logs.

## Pull requests

1. Keep changes focused and explain the user-visible behavior.
2. Do not commit personal profile JSON, DPAPI files, SteamCMD caches, downloaded Workshop content, game files or executables.
3. Run the core regression script: `py -3 tests/test_barotrauma_mod_manager.py`.
4. For Workshop UI changes, run `py -3 tests/test_workshop_gui_hotfix.py` in a desktop session.
5. Update documentation and `CHANGELOG.md` when behavior changes.
6. Do not change the load-order semantics without tests covering Lua-first ordering, author order and manual order.

## Coding notes

- Keep network operations out of the Tkinter main thread.
- Route worker-thread messages through the existing thread-safe UI queue.
- Validate external URLs, Workshop IDs, archives and filesystem paths.
- Keep API keys out of logs, JSON state and source code.