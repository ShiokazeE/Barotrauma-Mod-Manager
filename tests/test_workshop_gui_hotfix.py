import os, tempfile, importlib.util, sys
from pathlib import Path
from types import SimpleNamespace

with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as local:
    os.environ['XDG_CONFIG_HOME'] = cfg
    os.environ['LOCALAPPDATA'] = local
    source = Path('/mnt/data/barotrauma_mod_manager.py')
    spec = importlib.util.spec_from_file_location('baro_gui_hotfix', source)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    # Do not run the network-dependent background SteamCMD bootstrap in a GUI smoke test.
    mod.ModManagerGUI._bootstrap_steamcmd_background = lambda self: None
    app = mod.ModManagerGUI()
    app.geometry('1260x860')
    app.notebook.select(app.workshop_tab)
    app.update_idletasks()

    dummy = []
    for i in range(36):
        dummy.append({
            'id': str(1000000000 + i),
            'title': f'Test Workshop Item {i}',
            'description': 'Description used to create enough content to scroll and test responsive geometry. ' * 2,
            'time_updated': 1800000000,
            'subscriptions': 1000 + i,
            'creator': '76561198000000000',
            'tags': ['test', 'content'],
            'preview_url': '',
            'is_collection': False,
            'children': [],
        })
    app._display_workshop_results(dummy, None, False)
    app.update_idletasks()
    app.after(150, app.quit)
    app.mainloop()  # allow the debounced scrollregion update to run

    y_before = app.workshop_canvas.yview()
    card = app.workshop_results_frame.winfo_children()[0]
    preview = card.grid_slaves(row=0, column=0)[0]
    # Generate an actual Tk event on a card Label; bind_all must route it to the Canvas.
    preview.event_generate('<MouseWheel>', delta=-120)
    app.update_idletasks()
    y_after = app.workshop_canvas.yview()
    assert y_after[0] > y_before[0], (y_before, y_after)

    # Simulate a burst of configure events (as produced by dragging the window edge).
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
    assert len(calls) == 1, f'expected one debounced resize, got {len(calls)}'
    assert app._workshop_last_canvas_width == 1040

    app._closing = True
    app.destroy()
    print('PASS: real Tkinter Workshop wheel + resize debounce smoke test')