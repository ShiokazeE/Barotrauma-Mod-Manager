import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "barotrauma_mod_manager.py"

if not SOURCE.is_file():
    raise FileNotFoundError(f"Application source not found: {SOURCE}")

with tempfile.TemporaryDirectory() as config_dir, tempfile.TemporaryDirectory() as local_dir:
    os.environ["XDG_CONFIG_HOME"] = config_dir
    os.environ["LOCALAPPDATA"] = local_dir

    spec = importlib.util.spec_from_file_location("baro_gui_hotfix", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load application module from {SOURCE}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    # Avoid network-dependent SteamCMD setup in this isolated GUI smoke test.
    mod.ModManagerGUI._bootstrap_steamcmd_background = lambda self: None

    app = mod.ModManagerGUI()
    try:
        app.geometry("1260x860")
        app.notebook.select(app.workshop_tab)
        app.update_idletasks()

        dummy = [
            {
                "id": str(1_000_000_000 + index),
                "title": f"Test Workshop Item {index}",
                "description": (
                    "Description used to create enough content to scroll and test "
                    "responsive geometry. " * 2
                ),
                "time_updated": 1_800_000_000,
                "subscriptions": 1_000 + index,
                "creator": "76561198000000000",
                "tags": ["test", "content"],
                "preview_url": "",
                "is_collection": False,
                "children": [],
            }
            for index in range(36)
        ]

        app._display_workshop_results(dummy, None, False)
        app.update_idletasks()
        app.after(150, app.quit)
        app.mainloop()  # Let the debounced scroll-region update run.

        y_before = app.workshop_canvas.yview()
        card = app.workshop_results_frame.winfo_children()[0]
        preview = card.grid_slaves(row=0, column=0)[0]

        # A wheel event over a child widget must scroll the Workshop canvas.
        preview.event_generate("<MouseWheel>", delta=-120)
        app.update_idletasks()
        y_after = app.workshop_canvas.yview()
        assert y_after[0] > y_before[0], (y_before, y_after)

        # A burst of resize notifications should result in one geometry update.
        calls = []
        original_itemconfigure = app.workshop_canvas.itemconfigure

        def counted_itemconfigure(*args, **kwargs):
            calls.append((args, kwargs))
            return original_itemconfigure(*args, **kwargs)

        app.workshop_canvas.itemconfigure = counted_itemconfigure
        for width in (980, 990, 1000, 1010, 1020, 1030, 1040):
            app._schedule_workshop_canvas_resize(SimpleNamespace(width=width))

        app.after(160, app.quit)
        app.mainloop()

        assert len(calls) == 1, f"Expected one debounced resize, got {len(calls)}"
        assert app._workshop_last_canvas_width == 1040
    finally:
        app._closing = True
        app.destroy()

print("PASS: Tkinter Workshop wheel + resize debounce smoke test")
