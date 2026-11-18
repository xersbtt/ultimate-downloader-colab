"""Swift queue and transfer regressions without live account credentials."""
import ast
import base64
import hashlib
import html
import json
import os
import posixpath
import queue
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

import requests


TORRENT_HASH = '0123456789abcdef0123456789abcdef01234567'
MAGNET = f'magnet:?xt=urn:btih:{TORRENT_HASH}&tr=https%3A%2F%2Ftracker.invalid%2Fpasskey'
FINGERPRINT = 'SHA256:' + 'A' * 43


def load_subject(tmp_path):
    source = Path(__file__).with_name('ultimate_downloader.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    names = {'DownloadTask', '_RapidSeedboxError', '_RapidSeedboxReadError', '_queue_task_key', '_classify_url',
             '_url_host', 'url_matches_host', 'resolve_rapidseedbox', 'resolve_all_links',
             '_download_rapidseedbox', 'download_worker', 'task_from_dict', '_strip_size_suffix',
             '_redact_transfer_error'}
    constants = {'_TASK_FIELDS', '_rapidseedbox_install_lock', 'DEBRID_SUPPORTED_HOSTS', 'STREAMING_HOSTS'}
    nodes = [node for node in tree.body if
             isinstance(node, (ast.ClassDef, ast.FunctionDef)) and
             (node.name in names or node.name.startswith('_rapidseedbox')) or
             isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                                                 (target.id in constants or target.id.startswith('_RAPIDSEEDBOX_'))
                                                 for target in node.targets)]
    namespace = dict(globals(), COLAB_ROOT=str(tmp_path), _rapidseedbox_secrets={},
                     rapidseedbox_connections=SimpleNamespace(value=2), rapidseedbox_fields={
        key: SimpleNamespace(value=value) for key, value in {
            'HOST': 'swift.example', 'USERNAME': 'user', 'PASSWORD': 'private-test-password',
            'HOST_KEY': FINGERPRINT, 'QBIT_URL': 'https://qb.swift.example/webui/',
            'QBIT_USERNAME': '', 'QBIT_PASSWORD': '',
        }.items()
    }, get_colab_secret=lambda key: '', _tb_contextual_filename=posixpath.basename,
        _tb_file_in_extras_folder=lambda path: '/Extras/' in '/' + path,
        cancel_requested=Mock(return_value=False), _cancel_requested=False,
        _disk_free_gb=Mock(return_value=100), _wait_for_disk_space=Mock(return_value=True),
        _mark_disk_gave_up=Mock(), DISK_START_GB=5, DISK_FLOOR_GB=2,
        progress_lock=Lock(), download_stats={}, print_lock=Lock(), print=Mock(),
        _tb_url_refresher=Mock(return_value=None), check_duplicate_in_drive=Mock(return_value=False),
        handle_file_processing=Mock(), DUPLICATE_SKIP=object())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


def attributes(mode=stat.S_IFREG, size=10, mtime=1234, name=''):
    return SimpleNamespace(st_mode=mode, st_size=size, st_mtime=mtime, filename=name)


class RapidSeedboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp_path = Path(temp.name)
        self.subject = load_subject(self.tmp_path)

    def test_fingerprint_accepts_copied_period_and_preserves_digest_case(self):
        field = self.subject['rapidseedbox_fields']['HOST_KEY']
        digest = 'aB' * 21 + 'c'
        for value in ('SHA256:' + digest, 'SHA256:' + digest + '.', ' sha256:' + digest + '=. '):
            with self.subTest(value=value):
                field.value = value
                self.assertEqual(self.subject['_rapidseedbox_sftp_settings']()[4], 'SHA256:' + digest)
        field.value = 'SHA256:…'
        with self.assertRaisesRegex(self.subject['_RapidSeedboxError'], 'full SHA256: code'):
            self.subject['_rapidseedbox_sftp_settings']()

    def test_folder_resolves_distinct_paths_and_skips_symlinks(self):
        subject = self.subject
        server = Mock()
        server.lstat.return_value = attributes(stat.S_IFDIR)
        server.listdir_attr.side_effect = [
            [attributes(name='x #1.mkv'), attributes(stat.S_IFDIR, name='Extras'),
             attributes(stat.S_IFLNK, name='loop')],
            [attributes(name='x #1.mkv')],
        ]
        subject['_rapidseedbox_sftp'] = Mock(return_value=nullcontext(server))
        tasks = subject['resolve_rapidseedbox']('rapidseedbox:///Downloads/')
        assert [t.rapidseedbox_path for t in tasks] == ['/Downloads/Extras/x #1.mkv', '/Downloads/x #1.mkv']
        assert [t.selected_by_default for t in tasks] == [False, True]
        assert tasks[0].url.endswith('/Extras/x%20%231.mkv')
        assert len({subject['_queue_task_key'](t) for t in tasks}) == 2
        assert all(t.rapidseedbox_mtime == 1234 for t in tasks)
        with self.assertRaisesRegex(subject['_RapidSeedboxError'], 'invalid file path'):
            subject['resolve_rapidseedbox']('rapidseedbox:///Downloads/%2e%2e/another')

    def test_browser_folder_link_resolves_into_sftp_home_and_deduplicates(self):
        subject = self.subject
        browser = 'https://rapidseedbox12345-files.swift-012.seedbox.vip/files/'
        folder = '[Release] Show 2007 [1080p]'
        sftp_path = f'/home/user/{folder}'
        server = Mock()
        server.normalize.return_value = '/home/user'
        server.lstat.return_value = attributes(stat.S_IFDIR)
        server.listdir_attr.return_value = [attributes(name='Show #1.mkv'), attributes(name='Show #2.mkv')]
        subject['_rapidseedbox_sftp'] = Mock(return_value=nullcontext(server))

        browser_url = browser + quote(folder) + '/'
        absolute_url = 'rapidseedbox://' + quote(sftp_path) + '/'
        tasks, youtube, mega, debrid = subject['resolve_all_links'](
            [browser_url, absolute_url], None, {}, 'rd-key', debrid_service='rd')
        self.assertEqual([task.rapidseedbox_path for task in tasks],
                         [f'{sftp_path}/Show #1.mkv', f'{sftp_path}/Show #2.mkv'])
        self.assertTrue(all(task.link_type == 'rapidseedbox' for task in tasks))
        self.assertFalse(youtube or mega or debrid)
        server.normalize.assert_called_once_with('.')
        server.lstat.assert_called_with(sftp_path)
        self.assertEqual(tasks[0].url, 'rapidseedbox://' + quote(f'{sftp_path}/Show #1.mkv'))

    def test_browser_file_link_in_confined_home_and_link_validation(self):
        subject = self.subject
        host = 'rapidseedbox12345-files.swift-012.seedbox.vip'
        link = f'https://{host}/files/My%20Show/ep.mkv'
        server = Mock()
        server.normalize.return_value = '/'
        server.lstat.return_value = attributes()
        subject['_rapidseedbox_sftp'] = Mock(return_value=nullcontext(server))
        task, = subject['resolve_rapidseedbox'](link)
        self.assertEqual(task.rapidseedbox_path, '/My Show/ep.mkv')
        for unrelated in (f'https://{host}.example/files/a/', f'https://{host}/s/share'):
            self.assertEqual(subject['_classify_url'](unrelated, False), 'direct')
        with self.assertRaisesRegex(subject['_RapidSeedboxError'], 'invalid file path'):
            subject['resolve_rapidseedbox'](f'https://{host}/files/%2e%2e/a/')
        server.lstat.assert_called_once_with('/My Show/ep.mkv')

    def test_magnet_uses_qbit_only_when_selected_and_saves_no_tracker_or_credentials(self):
        subject = self.subject
        api = Mock(side_effect=[[], 'Ok.', [{'hash': TORRENT_HASH}], [{'name': 'Movie.mkv', 'size': 10}]])
        subject['_rapidseedbox_qbit'] = Mock(return_value=nullcontext(api))
        subject['time'] = SimpleNamespace(monotonic=time.monotonic, sleep=Mock())
        subject['rapidseedbox_magnets'] = SimpleNamespace(value=True)
        subject['resolve_tb_magnet_files'] = Mock(return_value=[])
        tasks, *_ = subject['resolve_all_links']([MAGNET], None, {}, '', 'tb-key', 'tb')
        assert len(tasks) == 1
        subject['resolve_tb_magnet_files'].assert_not_called()
        calls = api.call_args_list
        assert calls[1].args == ('torrents/add',)
        assert calls[1].kwargs == {'multipart': {'urls': (None, MAGNET)}}
        saved = json.dumps(asdict(tasks[0]))
        assert 'passkey' not in saved and 'private-test-password' not in saved
        restored = subject['task_from_dict'](json.loads(saved))
        assert restored.url == f'rapidseedbox://torrent/{TORRENT_HASH}'
        assert restored.rapidseedbox_torrent == TORRENT_HASH
        subject['rapidseedbox_magnets'].value = False
        subject['resolve_all_links']([MAGNET], None, {}, '', 'tb-key', 'tb')
        subject['resolve_tb_magnet_files'].assert_called_once_with(MAGNET, 'tb-key')

    def test_qbit_hash_reuses_torrent_and_waits_until_move_is_complete(self):
        subject = self.subject
        torrent = dict(hash=TORRENT_HASH, state='moving', progress=1, save_path='/home/user/Downloads')
        file = dict(name='Season 01/E01.mkv', size=10, progress=1)
        api = Mock(side_effect=[[torrent], [file], [torrent], [dict(torrent, state='uploading')], [file]])
        subject['_rapidseedbox_qbit'] = Mock(return_value=nullcontext(api))
        subject['time'] = SimpleNamespace(monotonic=time.monotonic, sleep=Mock())
        task, = subject['resolve_rapidseedbox'](f'rapidseedbox://torrent/{TORRENT_HASH}')
        settings = subject['_rapidseedbox_qbit_settings'](subject['_rapidseedbox_sftp_settings']())
        server_path = subject['_rapidseedbox_torrent_path'](task, settings)
        assert server_path == '/home/user/Downloads/Season 01/E01.mkv'
        assert all(call.args[0] != 'torrents/add' for call in api.call_args_list)
        server = Mock()
        server.lstat.side_effect = [FileNotFoundError(), attributes()]
        mapped, _ = subject['_rapidseedbox_remote_file'](server, server_path, task, 'user')
        assert mapped == '/Downloads/Season 01/E01.mkv'

    def test_hybrid_magnet_uses_qbit_returned_id(self):
        subject = self.subject
        v1_base32 = base64.b32encode(bytes.fromhex(TORRENT_HASH)).decode()
        v2 = 'ab' * 32
        magnet = f'magnet:?xt=urn:btih:{v1_base32}&xt=urn:btmh:1220{v2}'
        api = Mock(side_effect=[[{'hash': v2[:40]}], [{'name': 'Movie.mkv', 'size': 10}]])
        subject['_rapidseedbox_qbit'] = Mock(return_value=nullcontext(api))
        task, = subject['resolve_rapidseedbox'](magnet)
        assert task.rapidseedbox_torrent == v2[:40]
        assert api.call_args_list[0].kwargs['params']['hashes'] == f'{TORRENT_HASH}|{v2[:40]}'
        assert api.call_args_list[1].args == ('torrents/files',)

    def test_qbit_login_allows_normal_and_empty_success(self):
        for login_status, login_body in [(200, 'Ok.'), (204, '')]:
            with self.subTest(login_status=login_status):
                self.check_qbit_login(login_status, login_body)

    def check_qbit_login(self, login_status, login_body):
        subject = self.subject
        session = Mock(headers={}, auth=None)
        response = SimpleNamespace(status_code=login_status, text=login_body, headers={})
        session.request.side_effect = [nullcontext(response), nullcontext(SimpleNamespace(status_code=200, text='v5.2.3'))]
        subject['requests'] = SimpleNamespace(Session=lambda: nullcontext(session), RequestException=requests.RequestException)
        settings = subject['_rapidseedbox_qbit_settings'](subject['_rapidseedbox_sftp_settings']())
        with subject['_rapidseedbox_qbit'](settings) as api:
            assert api('app/version') == 'v5.2.3'
        assert session.headers['Origin'] == 'https://qb.swift.example'
        assert session.request.call_args_list[0].args == ('POST', 'https://qb.swift.example/webui/api/v2/auth/login')
        assert not any(call.kwargs['allow_redirects'] for call in session.request.call_args_list)

    def test_worker_interrupted_partial_is_resumed_and_only_verified_file_goes_to_drive(self):
        subject = self.subject
        tmp_path = self.tmp_path
        data = bytes(range(251)) * 50000
        server = Mock()
        server.lstat.return_value = attributes(size=len(data))
        subject['_rapidseedbox_sftp'] = Mock(return_value=nullcontext(server))
        task, = subject['resolve_rapidseedbox']('rapidseedbox:///Downloads/Example.mkv')
        remote = Mock()
        server.open.return_value = nullcontext(remote)
        requests_made = []

        def read(chunks):
            requests_made.append(chunks[0][0])
            if len(requests_made) in (2, 3):
                raise TimeoutError('interrupted')
            return [data[offset:offset + length] for offset, length in chunks]

        remote.readv.side_effect = read
        subject['download_worker'](task, '')
        assert task.status == 'failed'
        subject['handle_file_processing'].assert_not_called()
        partial, = tmp_path.glob('.rapidseedbox/*/.partial-*')
        partial_size = partial.stat().st_size
        self.assertEqual(partial_size, subject['_RAPIDSEEDBOX_READ_AHEAD'])
        retried = subject['download_worker'](subject['task_from_dict'](asdict(task)), '')
        final_path = Path(subject['handle_file_processing'].call_args.args[0])
        assert final_path.name == 'Example.mkv'
        assert final_path.read_bytes() == data
        self.assertEqual(requests_made[1:4], [partial_size] * 3)
        assert retried.status == 'done'

    def test_fast_read_failure_resumes_with_a_smaller_window(self):
        subject = self.subject
        fast_window = subject['_RAPIDSEEDBOX_READ_AHEAD']
        smaller_window = subject['_RAPIDSEEDBOX_READ_AHEAD_FALLBACK']
        data = b'x' * fast_window + b'y' * (smaller_window + 1)
        server = Mock()
        server.lstat.return_value = attributes(size=len(data))
        subject['_rapidseedbox_sftp'] = Mock(return_value=nullcontext(server))
        task, = subject['resolve_rapidseedbox']('rapidseedbox:///Downloads/Example.mkv')
        remote = Mock()
        server.open.return_value = nullcontext(remote)
        windows = []

        def read(chunks):
            window = chunks[0][0], sum(length for _, length in chunks)
            windows.append(window)
            if len(windows) == 2:
                raise TimeoutError('read interrupted')
            return [data[offset:offset + length] for offset, length in chunks]

        remote.readv.side_effect = read
        subject['download_worker'](task, '')
        self.assertEqual(task.status, 'done')
        self.assertEqual(windows, [(0, fast_window), (fast_window, smaller_window + 1),
                                   (fast_window, smaller_window), (fast_window + smaller_window, 1)])
        final_path = Path(subject['handle_file_processing'].call_args.args[0])
        self.assertEqual(final_path.read_bytes(), data)

    def test_two_connections_write_in_order_and_keep_a_resumable_partial(self):
        subject = self.subject
        window = subject['_RAPIDSEEDBOX_READ_AHEAD'] = 128 * 1024
        data = bytes(range(251)) * 3000

        def payload(chunks):
            return [data[offset:offset + length] for offset, length in chunks]

        for interrupt_second in (False, True):
            with self.subTest(interrupt_second=interrupt_second):
                second_started = Event()

                def first_read(chunks):
                    self.assertTrue(second_started.wait(3), 'The second connection did not start in parallel')
                    return payload(chunks)

                def second_read(chunks):
                    second_started.set()
                    if interrupt_second:
                        raise TimeoutError('second connection interrupted')
                    return payload(chunks)

                sessions = []
                for read in (first_read, second_read, payload):
                    source = Mock()
                    source.readv.side_effect = read
                    sftp = Mock()
                    sftp.lstat.return_value = attributes(size=len(data))
                    sftp.open.return_value = nullcontext(source)
                    sessions.append((sftp, source))
                first, second, fallback = [sftp for sftp, _ in sessions]
                subject['_rapidseedbox_sftp'] = Mock(side_effect=[nullcontext(sftp) for sftp in
                                                                (first, first, second, fallback)])
                task, = subject['resolve_rapidseedbox']('rapidseedbox:///Downloads/Example.mkv')
                subject['download_worker'](task, '')
                self.assertEqual(task.status, 'done')
                final_path = Path(subject['handle_file_processing'].call_args.args[0])
                self.assertEqual(final_path.read_bytes(), data)
                fallback_source = sessions[2][1]
                if interrupt_second:
                    self.assertEqual(fallback_source.readv.call_args_list[0].args[0][0][0], window)
                else:
                    fallback_source.readv.assert_not_called()

    def test_changed_file_is_rejected_before_staging(self):
        subject = self.subject
        server = Mock()
        server.lstat.side_effect = [attributes(), attributes(mtime=1235)]
        subject['_rapidseedbox_sftp'] = Mock(return_value=nullcontext(server))
        task, = subject['resolve_rapidseedbox']('rapidseedbox:///Downloads/Example.mkv')
        subject['download_worker'](task, '')
        assert task.status == 'failed' and 'changed' in task.error
        server.open.assert_not_called()
        subject['handle_file_processing'].assert_not_called()
