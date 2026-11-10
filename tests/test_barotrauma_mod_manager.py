import io
import json
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path

# Keep application state out of the user's real profile during tests.
with tempfile.TemporaryDirectory() as config_tmp:
    os.environ['XDG_CONFIG_HOME'] = config_tmp
    os.environ['LOCALAPPDATA'] = config_tmp
    import importlib.util
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    spec = importlib.util.spec_from_file_location('baro_v2', project_root / 'barotrauma_mod_manager.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    # Static/class method binding and Steam library scanning.
    assert isinstance(mod.BaroModManagerCore.find_steam_libraries(), list)
    assert mod.BaroModManagerCore._is_link(Path(config_tmp) / 'does-not-exist') is False

    # Workshop ID parsing.
    core = mod.BaroModManagerCore('', '', log_callback=lambda _msg: None)
    assert core.extract_steam_id('1234567890') == '1234567890'
    assert core.extract_steam_id('https://steamcommunity.com/sharedfiles/filedetails/?id=1234567890') == '1234567890'
    try:
        core.extract_steam_id('https://example.com/sharedfiles/filedetails/?id=1234567890')
    except ValueError:
        pass
    else:
        raise AssertionError('Non-Steam URL was accepted')

    # Profile persistence and safe duplicate-free copy.
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / 'profiles.json'
        store = mod.ProfileStore(state)
        store.set_active_mods([{'id': '1234567890', 'title': 'Test'}])
        store.data['profiles']['Second'] = store.get_profile_mods('Основной')
        store.data['active_profile'] = 'Second'
        store.save()
        loaded = mod.ProfileStore(state)
        assert loaded.data['active_profile'] == 'Second'
        assert loaded.get_active_mods()[0]['id'] == '1234567890'

    # ZIP extraction supports normal files and rejects Zip Slip.
    with tempfile.TemporaryDirectory() as tmp:
        valid_zip = io.BytesIO()
        with zipfile.ZipFile(valid_zip, 'w') as zf:
            zf.writestr('mod/filelist.xml', '<contentpackage name="Test" />')
        valid_zip.seek(0)
        with zipfile.ZipFile(valid_zip) as zf:
            mod.safe_extract_zip(zf, Path(tmp) / 'ok')
        assert (Path(tmp) / 'ok' / 'mod' / 'filelist.xml').is_file()

        bad_zip = io.BytesIO()
        with zipfile.ZipFile(bad_zip, 'w') as zf:
            zf.writestr('../escaped.txt', 'bad')
        bad_zip.seek(0)
        try:
            with zipfile.ZipFile(bad_zip) as zf:
                mod.safe_extract_zip(zf, Path(tmp) / 'bad')
        except ValueError:
            pass
        else:
            raise AssertionError('Zip Slip path was accepted')

    # Author-oriented automatic order: Lua first, then frameworks, patches,
    # utilities, overhauls/content, submarine-only packages. Preserve order in groups.
    with tempfile.TemporaryDirectory() as tmp:
        core = mod.BaroModManagerCore(tmp, '', log_callback=lambda _msg: None)
        core.inspect_local_mod_xml = lambda mid: {
            'name': f'Mod_{mid}',
            'is_submarine_only': mid == '5000000005',
            'has_lua_or_cs': False,
            'has_overrides': False,
        }
        ordered = core.compute_priority_order([
            {'id': '4000000004', 'title': 'Ordinary B'},
            {'id': '2000000002', 'title': 'Compatibility patch B'},
            {'id': '3000000003', 'title': 'Performance Fix'},
            {'id': '2000000001', 'title': 'Compatibility patch A'},
            {'id': '2959574986', 'title': 'LuaCsForBarotrauma'},
            {'id': '2795927223', 'title': 'CsForBarotrauma'},
            {'id': '2559634234', 'title': 'Lua For Barotrauma'},
            {'id': '4000000006', 'title': 'Ordinary A'},
            {'id': '5000000005', 'title': 'Submarine package'},
            {'id': '4000000004', 'title': 'Duplicate ordinary B'},
        ])
        ordered_ids = [item['id'] for item in ordered]
        assert ordered_ids == [
            '2559634234', '2795927223', '2959574986',
            '2000000002', '2000000001', '3000000003',
            '4000000004', '4000000006', '5000000005',
        ], ordered_ids
        assert len(ordered_ids) == len(set(ordered_ids))
        assert ordered[0]['tier_label'] == 'FRAMEWORK'
        assert ordered[-1]['tier_label'] == 'SUBMARINE'

    # Native ModLists XML round-trip keeps IDs and exact author order.
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'author.xml'
        source.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<mods name="Author Pack"><Vanilla/>'
            '<!-- author note -->'
            '<Workshop name="Lua For Barotrauma" id="2559634234"/>'
            '<Workshop name="Patch Example" id="1234567890"/>'
            '<Workshop name="Content Example" id="1234567891"/>'
            '</mods>', encoding='utf-8'
        )
        preset_name, parsed = mod.parse_barotrauma_modlist(source)
        assert preset_name == 'Author Pack'
        assert [item['id'] for item in parsed] == ['2559634234', '1234567890', '1234567891']
        exported = mod.serialize_barotrauma_modlist(parsed, preset_name)
        exported_path = Path(tmp) / 'exported.xml'
        exported_path.write_bytes(exported)
        roundtrip_name, roundtrip = mod.parse_barotrauma_modlist(exported_path)
        assert roundtrip_name == preset_name
        assert [item['id'] for item in roundtrip] == [item['id'] for item in parsed]
        assert roundtrip[1]['title'] == 'Patch Example'

        malicious = Path(tmp) / 'malicious.xml'
        malicious.write_text(
            '<!DOCTYPE mods [<!ENTITY x "boom">]><mods name="&x;">'
            '<Workshop name="Bad" id="1234567890"/></mods>', encoding='utf-8'
        )
        try:
            mod.parse_barotrauma_modlist(malicious)
        except ValueError:
            pass
        else:
            raise AssertionError('DTD/entity XML was accepted')

    # Update checker must not compare filesystem mtime against Steam time_updated.
    with tempfile.TemporaryDirectory() as tmp:
        core = mod.BaroModManagerCore(tmp, '', log_callback=lambda _msg: None)
        local = core.local_mods_dir / '1234567890'
        local.mkdir(parents=True)
        (local / 'filelist.xml').write_text('<contentpackage name="Test" />', encoding='utf-8')
        core._find_source = lambda _mid: local
        core.fetch_mods_metadata = lambda _ids: {
            '1234567890': {'id': '1234567890', 'title': 'Test', 'time_updated': 1900000000, 'available': True}
        }
        mods, outdated = core.check_updates_for_mods([{'id': '1234567890', 'title': 'Test'}], {})
        assert mods[0]['update_status'] == 'UNKNOWN'
        assert outdated == []
        mods, outdated = core.check_updates_for_mods([{'id': '1234567890', 'title': 'Test'}], {'1234567890': 1800000000})
        assert mods[0]['update_status'] == 'OUTDATED'
        assert len(outdated) == 1

    # XML profile application is atomic and preserves comments/non-package elements.
    with tempfile.TemporaryDirectory() as tmp:
        game_dir = Path(tmp)
        local = game_dir / 'LocalMods' / '1234567890'
        local.mkdir(parents=True)
        (local / 'filelist.xml').write_text('<contentpackage name="Test" />', encoding='utf-8')
        config = game_dir / 'config_player.xml'
        config.write_text(
            '<?xml version="1.0"?><config><contentpackages><regularpackages>'
            '<!-- keep --><package path="old.xml"/><custom value="1"/>'
            '</regularpackages></contentpackages></config>', encoding='utf-8'
        )
        core = mod.BaroModManagerCore(str(game_dir), '', log_callback=lambda _msg: None)
        core.config_path = config
        core.apply_to_config_player([{'id': '1234567890'}])
        written = config.read_text(encoding='utf-8')
        assert 'keep' in written and 'custom' in written and 'LocalMods/1234567890/filelist.xml' in written
        assert config.with_suffix('.xml.bak').is_file()

print('PASS: core smoke tests')

# SteamCMD integration logic: one process handles the whole batch and files are verified.
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    steamcmd = tmp_path / 'steamcmd.exe'
    steamcmd.write_bytes(b'fake executable for test')
    core = mod.BaroModManagerCore(str(tmp_path / 'game'), str(steamcmd), log_callback=lambda _msg: None)
    core.workers = 1  # This test specifically verifies the stable serial SteamCMD path.
    calls = []
    def fake_run(args, on_line=None, timeout=1800):
        calls.append(list(args))
        for index, value in enumerate(args[:-2]):
            if value == '+workshop_download_item':
                mid = args[index + 2]
                out = core.workshop_content_dir / mid
                out.mkdir(parents=True, exist_ok=True)
                (out / 'filelist.xml').write_text('<contentpackage name="Test" />', encoding='utf-8')
                if on_line:
                    on_line(f'Success. Downloaded item {mid}')
        return 0
    core._run_steamcmd = fake_run
    core.ensure_steamcmd_installed = lambda: steamcmd
    errors = core.download_mods_steamcmd(['1234567890', '1234567891'])
    assert errors == {}, errors
    assert len(calls) == 1, len(calls)
    assert (core.workshop_content_dir / '1234567890' / 'filelist.xml').is_file()
    assert (core.workshop_content_dir / '1234567891' / 'filelist.xml').is_file()

# Steam content is linked only after a valid package root exists.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    source = base / 'source' / '1234567890'
    source.mkdir(parents=True)
    (source / 'filelist.xml').write_text('<contentpackage name="Test" />', encoding='utf-8')
    core = mod.BaroModManagerCore(str(base / 'game'), '', log_callback=lambda _msg: None)
    core._find_source = lambda _mid: source
    core.link_to_local_mods(['1234567890'])
    target = core.local_mods_dir / '1234567890'
    assert (target / 'filelist.xml').is_file()
    if os.name != 'nt':
        assert target.is_symlink()

print('PASS: SteamCMD batch + LocalMods linking')

# Forced update must bypass an existing Steam-client cache and link the freshly
# downloaded SteamCMD copy instead of the stale client copy.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    steamcmd = base / 'steamcmd.exe'
    steamcmd.write_bytes(b'fake executable for test')
    core = mod.BaroModManagerCore(str(base / 'game'), str(steamcmd), log_callback=lambda _msg: None)
    mid = '1234567899'
    old_client = base / 'client_cache' / mid
    old_client.mkdir(parents=True)
    (old_client / 'filelist.xml').write_text('<contentpackage name="Old" />', encoding='utf-8')
    core._steam_client_source = lambda _mid: old_client if _mid == mid else None

    calls = []
    def fake_update(args, on_line=None, timeout=1800):
        calls.append(list(args))
        new_cache = core.workshop_content_dir / mid
        new_cache.mkdir(parents=True, exist_ok=True)
        (new_cache / 'filelist.xml').write_text('<contentpackage name="Fresh" />', encoding='utf-8')
        if on_line:
            on_line(f'Success. Downloaded item {mid}')
        return 0
    core._run_steamcmd = fake_update
    core.ensure_steamcmd_installed = lambda: steamcmd
    errors = core.acquire_mods([mid], force_refresh_ids=[mid])
    assert errors == {}, errors
    assert len(calls) == 1
    linked = core.local_mods_dir / mid
    assert linked.is_dir() and (linked / 'filelist.xml').is_file()
    assert 'Fresh' in (linked / 'filelist.xml').read_text(encoding='utf-8')

print('PASS: forced refresh prefers the freshly downloaded package')

# Steam Workshop catalog search: key handling, exact Barotrauma AppID, results and paging.
with tempfile.TemporaryDirectory() as tmp:
    core = mod.BaroModManagerCore(str(Path(tmp) / 'game'), '', log_callback=lambda _msg: None)
    class FakeResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {
                'response': {
                    'publishedfiledetails': [{
                        'publishedfileid': '1234567890',
                        'consumer_app_id': 602960,
                        'title': 'Workshop test item',
                        'short_description': 'Small test description',
                        'time_updated': 1800000000,
                        'subscriptions': 123,
                        'creator': '76561198000000000',
                        'tags': [{'tag': 'content'}, {'tag': 'submarine'}],
                        'previews': [{'url': 'https://steamuserimages-a.akamaihd.net/preview.jpg'}],
                    }],
                    'next_cursor': 'cursor-next',
                }
            }
    class FakeSession:
        def __init__(self):
            self.last = None
        def get(self, url, params, timeout):
            self.last = (url, params, timeout)
            return FakeResponse()
    fake_session = FakeSession()
    core.session = fake_session
    items, next_cursor = core.query_workshop('submarine', 'a' * 32, 'relevance')
    assert len(items) == 1
    assert items[0]['id'] == '1234567890'
    assert items[0]['title'] == 'Workshop test item'
    assert items[0]['preview_url'].endswith('preview.jpg')
    assert next_cursor == 'cursor-next'
    request_json = json.loads(fake_session.last[1]['input_json'])
    assert request_json['appid'] == 602960 and request_json['creator_appid'] == 602960
    assert request_json['query_type'] == 12 and request_json['search_text'] == 'submarine'
    try:
        core.query_workshop('test', 'not-a-valid-key')
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid Steam API key was accepted')

print('PASS: Steam Workshop API search + key validation')

# SteamCMD setup is tested below with a broken/missing prior path and a mocked official ZIP.

# An item already in LocalMods must still be addable to the active profile.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    game = base / 'game'
    local_mod = game / 'LocalMods' / '1234567898'
    local_mod.mkdir(parents=True)
    (local_mod / 'filelist.xml').write_text('<contentpackage name="Already installed" />', encoding='utf-8')
    store = mod.ProfileStore(base / 'profiles.json')
    core = mod.BaroModManagerCore(str(game), '', log_callback=lambda _msg: None)
    core.acquire_mods = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('should not download an installed mod'))
    core.apply_to_config_player = lambda _mods: None

    app = mod.ModManagerGUI.__new__(mod.ModManagerGUI)
    app._closing = True  # Avoid scheduling Tk callbacks; this tests the worker's domain logic.
    app.store = store
    app.log = lambda _msg: None
    item = {'id': '1234567898', 'title': 'Already installed', 'time_updated': 1800000000}
    app._worker_download_workshop_item(core, item, 'Основной', [])
    profile = store.get_profile_mods('Основной')
    assert len(profile) == 1 and profile[0]['id'] == '1234567898', profile
    assert store.data['installed_versions']['1234567898'] == 1800000000

print('PASS: existing LocalMods item is safely added to a profile')

# A typed search can be ordered by the selected sort type, not forced to text relevance.
with tempfile.TemporaryDirectory() as tmp:
    core = mod.BaroModManagerCore(str(Path(tmp) / 'game'), '', log_callback=lambda _msg: None)
    class SortResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {'response': {'publishedfiledetails': [], 'next_cursor': ''}}
    class SortSession:
        def __init__(self):
            self.params = None
        def get(self, _url, params, timeout):
            self.params = params
            return SortResponse()
    session = SortSession()
    core.session = session
    expected_sort_types = {
        'popular': 9,
        'relevance': 12,
        'rated': 0,
        'newest': 1,
        'updated': 21,
    }
    for sort_mode, expected_query_type in expected_sort_types.items():
        core.query_workshop('submarine', 'b' * 32, sort_mode)
        assert json.loads(session.params['input_json'])['query_type'] == expected_query_type

    # The collection catalog uses Steam's documented matching file type 1.
    core.query_workshop('modpack', 'b' * 32, 'popular', content_type='collections')
    collection_payload = json.loads(session.params['input_json'])
    assert collection_payload['filetype'] == 1
    assert collection_payload['return_children'] is True

    # Steam can repeat the last cursor. Treat it as the end to avoid endless paging.
    class RepeatingCursorResponse(SortResponse):
        def json(self):
            return {'response': {'publishedfiledetails': [], 'next_cursor': 'same-cursor'}}
    class RepeatingCursorSession(SortSession):
        def get(self, _url, params, timeout):
            self.params = params
            return RepeatingCursorResponse()
    core.session = RepeatingCursorSession()
    _items, next_cursor = core.query_workshop('submarine', 'b' * 32, 'popular', cursor='same-cursor')
    assert next_cursor is None
    class StarCursorResponse(SortResponse):
        def json(self):
            return {'response': {'publishedfiledetails': [], 'next_cursor': '*'}}
    class StarCursorSession(SortSession):
        def get(self, _url, params, timeout):
            self.params = params
            return StarCursorResponse()
    core.session = StarCursorSession()
    _items, next_cursor = core.query_workshop('submarine', 'b' * 32, 'popular', cursor='cursor-page-2')
    assert next_cursor is None

print('PASS: Workshop search sorting + cursor termination')

# SteamCMD installation code is intentionally retained from the previous working build.

# Direct CDN downloads accept only trusted Steam hosts and do not follow redirects to arbitrary domains.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    core = mod.BaroModManagerCore(str(base / 'game'), str(base / 'steamcmd.exe'), log_callback=lambda _msg: None)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('filelist.xml', '<contentpackage name="CDN Mod" />')
    archive_bytes = archive_buffer.getvalue()

    class FakeCDNResponse:
        status_code = 200
        headers = {'Content-Length': str(len(archive_bytes))}
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def raise_for_status(self):
            return None
        def iter_content(self, chunk_size):
            for offset in range(0, len(archive_bytes), chunk_size):
                yield archive_bytes[offset:offset + chunk_size]

    class FakeCDNSession:
        def __init__(self):
            self.calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeCDNResponse()

    session = FakeCDNSession()
    core.session = session
    assert core._download_mod_from_cdn('1234567897', {
        'file_url': 'https://steamusercontent-a.akamaihd.net/workshop/item.zip'
    }) is True
    installed_filelist = core.workshop_content_dir / '1234567897' / 'filelist.xml'
    assert installed_filelist.is_file()
    assert 'CDN Mod' in installed_filelist.read_text(encoding='utf-8')
    assert session.calls[0][1]['allow_redirects'] is False
    assert mod.is_trusted_steam_cdn_url('https://steamusercontent.com/file.zip') is True
    assert mod.is_trusted_steam_cdn_url('https://notsteamusercontent.com/file.zip') is False

    class RedirectResponse:
        status_code = 302
        headers = {'Location': 'https://example.com/redirected.zip'}
        def close(self):
            self.closed = True

    class RedirectSession:
        def __init__(self):
            self.calls = []
        def get(self, url, **kwargs):
            self.calls.append(url)
            return RedirectResponse()

    redirect_session = RedirectSession()
    core.session = redirect_session
    try:
        core._download_mod_from_cdn('1234567896', {
            'file_url': 'https://steamusercontent-a.akamaihd.net/redirect.zip'
        })
    except ValueError:
        pass
    else:
        raise AssertionError('Untrusted CDN redirect was accepted')
    assert len(redirect_session.calls) == 1, redirect_session.calls

print('PASS: direct Steam CDN download + redirect protection')


# Schema migration preserves the conservative two-worker default and an explicit single-worker setting.
with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp) / 'profiles.json'
    state.write_text(json.dumps({
        'schema_version': 2,
        'parallel_downloads': 2,
        'profiles': {'Основной': []},
        'active_profile': 'Основной',
    }), encoding='utf-8')
    migrated = mod.ProfileStore(state)
    assert migrated.data['schema_version'] == 3
    assert migrated.data['parallel_downloads'] == 2

    state.write_text(json.dumps({
        'schema_version': 2,
        'parallel_downloads': 1,
        'profiles': {'Основной': []},
        'active_profile': 'Основной',
    }), encoding='utf-8')
    preserved = mod.ProfileStore(state)
    assert preserved.data['parallel_downloads'] == 1

# Isolated SteamCMD workers use private roots and balance known file sizes.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    steamcmd = base / 'steamcmd.exe'
    steamcmd.write_bytes(b'fake executable')
    core = mod.BaroModManagerCore(str(base / 'game'), str(steamcmd), log_callback=lambda _msg: None)
    core.workers = 2
    core.ensure_steamcmd_installed = lambda: steamcmd
    worker_roots = []
    assignments = {}

    def fake_prepare(index):
        root = base / f'worker_{index + 1}'
        root.mkdir(parents=True, exist_ok=True)
        exe = root / 'steamcmd.exe'
        exe.write_bytes(b'fake')
        worker_roots.append(root)
        return exe, root

    core._prepare_isolated_steamcmd_worker = fake_prepare

    def fake_isolated_run(executable, args, on_line=None, timeout=1800):
        worker_root = executable.parent
        worker_index = worker_roots.index(worker_root)
        ids = []
        for i, value in enumerate(args[:-2]):
            if value == '+workshop_download_item':
                mid = args[i + 2]
                ids.append(mid)
                output = worker_root / 'steamapps' / 'workshop' / 'content' / '602960' / mid
                output.mkdir(parents=True, exist_ok=True)
                (output / 'filelist.xml').write_text('<contentpackage name="Parallel" />', encoding='utf-8')
                if on_line:
                    on_line(f'Success. Downloaded item {mid}')
        assignments[worker_index] = ids
        return 0

    core._run_isolated_steamcmd = fake_isolated_run
    size_hints = {
        '1000000001': 900,
        '1000000002': 800,
        '1000000003': 10,
        '1000000004': 10,
    }
    errors = core.download_mods_steamcmd(list(size_hints), size_hints=size_hints)
    assert errors == {}, errors
    assert len(assignments) == 2, assignments
    assert sum(len(ids) for ids in assignments.values()) == 4
    assert set(assignments[0]).isdisjoint(assignments[1])
    for mid in size_hints:
        assert (core.workshop_content_dir / mid / 'filelist.xml').is_file(), mid

# Deleting a LocalMods junction/symlink must not follow it and erase the source folder.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    game = base / 'game'
    source = base / 'steam_cache' / '1234567896'
    source.mkdir(parents=True)
    (source / 'filelist.xml').write_text('<contentpackage name="Keep source" />', encoding='utf-8')
    core = mod.BaroModManagerCore(str(game), '', log_callback=lambda _msg: None)
    core.local_mods_dir.mkdir(parents=True)
    local_link = core.local_mods_dir / '1234567896'
    if os.name == 'nt':
        # A directory symlink may require permissions on Windows; test a real copy there.
        import shutil
        shutil.copytree(source, local_link)
    else:
        os.symlink(source, local_link, target_is_directory=True)
    removed = core.remove_mod_files('1234567896')
    assert removed >= 1
    assert not local_link.exists()
    assert (source / 'filelist.xml').is_file()

print('PASS: migration + isolated workers + safe mod removal')

# A failed/exceptional public CDN attempt must fall back to SteamCMD, not abort a pack.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    core = mod.BaroModManagerCore(str(base / 'game'), str(base / 'steamcmd.exe'), log_callback=lambda _msg: None)
    mid = '1234567895'
    core._steam_client_source = lambda _mid: None
    def broken_cdn(*_args, **_kwargs):
        raise OSError('temporary CDN network failure')
    core._download_mod_from_cdn = broken_cdn
    def fake_fallback(ids, **_kwargs):
        for value in ids:
            package = core.workshop_content_dir / value
            package.mkdir(parents=True, exist_ok=True)
            (package / 'filelist.xml').write_text('<contentpackage name="SteamCMD fallback" />', encoding='utf-8')
        return {}
    core.download_mods_steamcmd = fake_fallback
    failed = core.acquire_mods([mid], metadata={mid: {
        'id': mid,
        'file_url': 'https://steamusercontent-a.akamaihd.net/workshop/test.zip',
        'file_size': 2048,
    }})
    assert failed == {}, failed
    assert (core.local_mods_dir / mid / 'filelist.xml').is_file()

print('PASS: CDN failure falls back to SteamCMD')