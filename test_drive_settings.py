"""Drive preferences must survive mounting and later edits to other settings."""

import ast
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


class Widget:
    def __init__(self, value):
        self._value = value
        self.min, self.max = 1, 8
        self.changed = None

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        old, self._value = self._value, value
        if self.changed and old != value:
            self.changed(dict(type='change', name='value', owner=self))


class DriveSettingsTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name('ultimate_downloader.py')
        tree = ast.parse(source.read_text(encoding='utf-8'))
        names = {'save_dir_settings', 'load_dir_settings', 'on_dir_change', 'early_mount_drive'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = root / 'drive' / 'My Drive' / 'Ultimate Downloader' / 'settings.json'
        self.settings.parent.mkdir(parents=True)
        self.mounted = False
        # Drive files exist remotely but are unreadable until the mount succeeds.
        fake_os = SimpleNamespace(
            path=SimpleNamespace(exists=lambda p: self.mounted and os.path.exists(p)),
            makedirs=os.makedirs)
        self.ns = dict(os=fake_os, json=json, COLAB_ROOT=str(root) + '/',
                       DRIVE_BASE=str(self.settings.parent.parent),
                       UD_CONFIG_PATH=str(self.settings.parent), SETTINGS_FILE=str(self.settings),
                       _loading_settings=False, _user_touched_settings=set(),
                       update_main_ui_visibility=Mock(), check_resume_available=Mock(), print=Mock(),
                       drive=SimpleNamespace(mount=Mock(side_effect=self.mount)))
        # Supply only the notebook controls used by the real settings functions.
        widgets = {n.value.id for f in nodes for n in ast.walk(f)
                   if isinstance(n, ast.Attribute) and n.attr == 'value'
                   and isinstance(n.value, ast.Name)}
        for name in widgets:
            value = False if name.endswith('checkbox') else ''
            if name in {'quick_dl_subtitle_langs', 'extract_langs'}:
                value = ('en',)
            if name == 'drive_movers_slider':
                value = 3
            self.ns[name] = Widget(value)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.ns)
        for name in widgets:
            self.ns[name].changed = self.ns['on_dir_change']

    def mount(self, _path):
        self.mounted = True

    def start_with_saved_preference(self, enabled):
        self.settings.write_text(json.dumps({'drive_api': enabled, 'tv_path': 'Saved TV'}))
        self.ns['load_dir_settings']()  # Startup read before mounting sees no file.
        self.ns['early_mount_drive']()

    def test_edit_after_mount_preserves_saved_api_preference(self):
        self.start_with_saved_preference(True)
        self.ns['dir_movie_input'].value = 'New Movies'
        saved = json.loads(self.settings.read_text())
        self.assertTrue(self.ns['drive_api_checkbox'].value)
        self.assertTrue(saved['drive_api'])
        self.assertEqual(saved['tv_path'], 'Saved TV')
        self.assertEqual(saved['movie_path'], 'New Movies')

    def test_explicit_saved_mount_preference_is_respected(self):
        self.start_with_saved_preference(False)
        self.ns['dir_movie_input'].value = 'New Movies'
        self.assertFalse(json.loads(self.settings.read_text())['drive_api'])

    def test_mount_reload_keeps_in_session_user_choice(self):
        self.ns['drive_api_checkbox']._value = True
        self.ns['_user_touched_settings'].add(self.ns['drive_api_checkbox'])
        self.start_with_saved_preference(False)
        self.assertTrue(self.ns['drive_api_checkbox'].value)
        self.assertEqual(self.ns['dir_tv_input'].value, 'Saved TV')

    def test_already_mounted_drive_restores_settings_too(self):
        self.settings.write_text(json.dumps({'drive_api': True}))
        self.mounted = True
        self.ns['early_mount_drive']()
        self.ns['drive'].mount.assert_not_called()
        self.assertTrue(self.ns['drive_api_checkbox'].value)

    def test_background_setup_does_not_retry_an_access_prompt(self):
        source = Path(__file__).with_name('ultimate_downloader.py')
        node = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'setup_environment')
        ns = dict(self.ns)
        ns.update(check_and_load_secrets=Mock(), load_dir_settings=Mock(), save_dir_settings=Mock(),
                  drive_api_checkbox=SimpleNamespace(value=True), _init_drive_api=Mock(),
                  _ensure_dest_dir=Mock(), get_tv_path=lambda: 'TV', get_movie_path=lambda: 'Movies',
                  get_youtube_path=lambda: 'YouTube', shutil=SimpleNamespace(which=lambda _: True),
                  subprocess=SimpleNamespace(run=Mock()), current_thread=threading.current_thread,
                  main_thread=threading.main_thread)
        self.mounted = True
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)
        errors = []

        def setup():
            try:
                ns['setup_environment'](False, False, True)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=setup)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        ns['_init_drive_api'].assert_not_called()
        ns['setup_environment'](False, False, True)
        ns['_init_drive_api'].assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
