"""PikPak integration regressions without account credentials or real transfers."""
import ast
import hashlib
import json
import os
import platform
import queue
import re
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

SOURCE = Path(__file__).with_name('ultimate_downloader.py')


def load_subject():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'DownloadTask', '_TASK_FIELDS', 'task_from_dict', '_queue_task_key',
             '_url_host', 'url_matches_host', '_classify_url', 'resolve_all_links',
             'download_worker', 'check_and_load_secrets', 'save_session'}
    nodes = [node for node in tree.body if
             isinstance(node, (ast.FunctionDef, ast.ClassDef)) and
             (node.name in names or node.name.startswith('_pikpak') or node.name == 'resolve_pikpak')
             or isinstance(node, ast.Assign) and any(
                 isinstance(t, ast.Name) and (t.id.startswith('PIKPAK_') or t.id in
                 {'_TASK_FIELDS', '_pikpak_install_lock', '_pikpak_command_lock',
                  'STREAMING_HOSTS', 'DEBRID_SUPPORTED_HOSTS'})
                 for t in node.targets)]
    ns = dict(os=os, re=re, json=json, hashlib=hashlib, platform=platform,
              subprocess=subprocess, time=time, requests=SimpleNamespace(get=Mock(), Session=object),
              dataclass=dataclass, field=field, fields=fields, asdict=asdict, uuid4=uuid4,
              Any=Any, Callable=Callable, Dict=Dict, List=List, Optional=Optional, Tuple=Tuple,
              Lock=threading.Lock, urlparse=urlparse, parse_qs=parse_qs, unquote=unquote,
              token_pikpak=SimpleNamespace(value='private-test-token'),
              _cancel_requested=False, cancel_requested=lambda: False, print=Mock(),
              COLAB_ROOT='/content/', REQUEST_TIMEOUT=30,
              _tb_contextual_filename=lambda path: os.path.basename(path),
              _tb_file_in_extras_folder=lambda path: '/Extras/' in '/' + path,
              _strip_size_suffix=lambda name: re.sub(r' \([\d.]+ MB\)$', '', name),
              _register_proc=Mock(), _unregister_proc=Mock(),
              _tb_url_refresher=lambda *a: None, print_lock=threading.Lock(),
              check_duplicate_in_drive=Mock(return_value=False),
              DUPLICATE_SKIP=object(), handle_file_processing=Mock(),
              download_stats={}, queue=queue,
              ThreadPoolExecutor=__import__('concurrent.futures', fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def file_record(file_id='original', name='Show.S01E01.mkv', size=1300000000):
    return dict(id=file_id, name=name, size=str(size), kind='drive#file')


class PikPakTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject()
        self.cli = Mock()
        self.ns['_pikpak_cli'] = self.cli

    def task(self, **overrides):
        args = dict(url='https://mypikpak.com/s/share', original_url='https://mypikpak.com/s/share',
                    filename='Show.S01E01.mkv (1239.8 MB)', source='pikpak', link_type='pikpak',
                    pikpak_share_id='share', pikpak_file_id='original',
                    pikpak_original_name='Show.S01E01.mkv', pikpak_size=1300000000)
        args.update(overrides)
        return self.ns['DownloadTask'](**args)

    def test_classification_precedes_both_debrid_services(self):
        for debrid in (False, True):
            for url in ('https://mypikpak.com/s/share', 'pikpak://folder/abc', 'pikpak://file/abc'):
                self.assertEqual(self.ns['_classify_url'](url, debrid), 'pikpak')
        self.assertEqual(self.ns['_classify_url']('https://evil.test/?x=mypikpak.com', False), 'direct')

    def test_nested_share_route_uses_real_breadcrumb_id(self):
        # Nuxt stores objects in a flat reference array. The final URL token
        # deliberately differs from the real selected folder ID.
        values = [dict(share_status=1, files=2), 'OK', [3, 6],
                  dict(id=4, name=5), 'outer', 'Collection',
                  dict(id=7, name=8), 'actual-subfolder', 'Show Season 1']
        page = '<script type="application/json" id="__NUXT_DATA__">' + json.dumps(values) + '</script>'
        response = Mock(text=page)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        self.ns['requests'].get.return_value = response
        location = self.ns['_pikpak_location']('https://mypikpak.com/s/share/outer/opaque-token')
        self.assertEqual(location, ('share', 'actual-subfolder', '', ''))
        response.raise_for_status.assert_called_once()

    def test_unreadable_nested_route_does_not_expand_parent_share(self):
        response = Mock(text='<html>No breadcrumbs</html>')
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        self.ns['requests'].get.return_value = response
        with self.assertRaisesRegex(ValueError, 'folder location'):
            self.ns['resolve_pikpak']('https://mypikpak.com/s/share/outer/opaque')
        self.cli.assert_not_called()

    def test_own_folder_and_file_locations(self):
        self.assertEqual(self.ns['_pikpak_location']('pikpak://file/id'), ('', '', 'id', ''))
        self.assertEqual(self.ns['_pikpak_location']('pikpak://folder/id'), ('', 'id', '', ''))
        self.assertEqual(self.ns['_pikpak_location']('https://mypikpak.com/drive/all?parent_id=id'),
                         ('', 'id', '', ''))
        for url in ('pikpak://file/--help', 'pikpak://folder/a/b', 'https://evil.test/s/id'):
            # IDs may start with '-', but are always placed after positional
            # command names. Reject option-like IDs to prevent flag injection.
            with self.assertRaises(ValueError):
                self.ns['_pikpak_location'](url)

    def test_paginated_recursive_listing_keeps_subtitles_and_does_not_restore(self):
        self.cli.side_effect = [
            dict(file_list=[dict(id='season', name='Season 1', kind='folder')], next_page_token='page2'),
            dict(file_list=[file_record('subtitle', 'Show.S01E01.en.srt', 1000)]),
            dict(file_list=[file_record(), dict(id='extra', name='Extras', kind='folder')]),
            dict(file_list=[file_record('trailer', 'Trailer.mp4', 500)]),
        ]
        tasks = self.ns['resolve_pikpak']('https://mypikpak.com/s/share')
        self.assertEqual([t.pikpak_file_id for t in tasks], ['original', 'trailer', 'subtitle'])
        self.assertFalse(tasks[1].selected_by_default)
        self.assertTrue(tasks[2].selected_by_default)
        self.assertTrue(all(t.pikpak_saved_file_id is None for t in tasks))
        self.assertTrue(all(call.args[0][:2] == ['share', 'get'] for call in self.cli.call_args_list))
        self.assertIn('--token', self.cli.call_args_list[1].args[0])

    def test_repeating_pagination_token_stops_resolution(self):
        self.cli.return_value = dict(file_list=[], next_page_token='repeat')
        with self.assertRaisesRegex(RuntimeError, 'repeated a page token'):
            self.ns['_pikpak_files']('', 'share')
        self.assertEqual(self.cli.call_count, 2)

    def test_live_cli_subfolder_shape_and_formatted_sizes(self):
        self.ns['_pikpak_location'] = lambda url: ('share', 'parent', '', '')
        self.cli.return_value = dict(files=[file_record(size='1.33 GB')])
        tasks = self.ns['resolve_pikpak']('https://mypikpak.com/s/share/opaque')
        self.assertEqual(len(tasks), 1)
        self.assertIn(' MB)', tasks[0].filename)
        # Rounded display sizes must never be used for strict restored-file matching.
        self.assertEqual(tasks[0].pikpak_size, 0)

    def test_size_formats_used_by_official_client(self):
        for value, expected in [('1024', 1024), ('1.5 GB', int(1.5 * 1024 ** 3)),
                                ('12 KiB', 12288), ('-', 0), ('', 0)]:
            self.assertEqual(self.ns['_pikpak_size_bytes'](value), expected)

    def test_root_cli_shape_uses_file_list_instead_of_file_count(self):
        self.cli.return_value = dict(files='1', file_list=[file_record()], status='OK')
        self.assertEqual(len(self.ns['resolve_pikpak']('https://mypikpak.com/s/share')), 1)

    def test_queue_routes_pikpak_and_debrid_toggle_does_not_change_provider(self):
        task = self.task()
        self.ns['resolve_pikpak'] = Mock(return_value=[task])
        result = self.ns['resolve_all_links']([task.url], None, {}, 'rd-key', 'tb-key', 'tb')
        self.assertEqual(result, ([task], [], [], []))
        self.ns['resolve_pikpak'].assert_called_once_with(task.url)

    def test_own_file_is_not_restored(self):
        self.cli.return_value = file_record()
        tasks = self.ns['resolve_pikpak']('pikpak://file/original')
        self.assertEqual(tasks[0].pikpak_saved_file_id, 'original')
        self.cli.assert_called_once_with(['get', 'original', '--raw'])

    def test_selected_file_is_saved_once_and_refreshed_by_own_id(self):
        task = self.task()
        self.cli.side_effect = [dict(file_id='saved', restore_status='COMPLETE'),
                                file_record('saved'), 'https://cdn.test/first', 'https://cdn.test/fresh']
        self.assertEqual(self.ns['_pikpak_download_url'](task), 'https://cdn.test/first')
        self.assertEqual(self.ns['_pikpak_download_url'](task), 'https://cdn.test/fresh')
        self.assertEqual(self.cli.call_args_list[0].args[0],
                         ['share', 'restore', 'share', '--file-ids', 'original'])
        self.assertEqual(task.pikpak_saved_file_id, 'saved')
        self.assertEqual(task.url, 'https://mypikpak.com/s/share')
        restores = [c for c in self.cli.call_args_list if c.args[0][:2] == ['share', 'restore']]
        self.assertEqual(len(restores), 1)

    def test_own_share_reuses_verified_original_file_and_refreshes_without_restore(self):
        task = self.task()
        failure = self.ns['_pikpak_failure']('', json.dumps({'error': {
            'code': 'FILE_RESTORE_OWN', 'message': 'Failed to restore share',
            'reason': "You don't need to restore your own files or folders"}}), ['share', 'restore'])
        self.cli.side_effect = [failure, file_record(),
                                'https://cdn.test/original', 'https://cdn.test/refreshed']

        self.assertEqual(self.ns['_pikpak_download_url'](task), 'https://cdn.test/original')
        self.assertEqual(task.pikpak_saved_file_id, 'original')
        self.assertEqual(self.ns['_pikpak_download_url'](task), 'https://cdn.test/refreshed')
        self.assertEqual(self.cli.call_args_list[1].args[0], ['get', 'original', '--raw'])
        self.assertEqual(self.cli.call_args_list[2].args[0], ['download', 'original', '--url-only'])
        self.assertEqual(self.cli.call_args_list[3].args[0], ['download', 'original', '--url-only'])
        self.assertIsNone(task.pikpak_restore_root)

    def test_unrelated_restore_errors_do_not_try_to_download_source_account_files(self):
        failure = self.ns['_pikpak_failure']('', json.dumps({'error': {'code': 'ACCESS_DENIED'}}),
                                              ['share', 'restore'])
        self.cli.side_effect = failure
        task = self.task()
        with self.assertRaisesRegex(RuntimeError, 'ACCESS_DENIED'):
            self.ns['_pikpak_download_url'](task)
        self.cli.assert_called_once()
        self.assertIsNone(task.pikpak_saved_file_id)

    def test_own_share_fallback_checks_file_identity_before_downloading(self):
        failure = self.ns['_pikpak_failure']('', json.dumps({'error': {'code': 'FILE_RESTORE_OWN'}}),
                                              ['share', 'restore'])
        self.cli.side_effect = [failure, file_record(name='Different.mkv')]
        task = self.task()
        with self.assertRaisesRegex(RuntimeError, 'uniquely identify'):
            self.ns['_pikpak_download_url'](task)
        self.assertIsNone(task.pikpak_saved_file_id)
        self.assertEqual(self.cli.call_count, 2)

    def test_resume_reuses_pending_restore_before_generating_url(self):
        task = self.task(pikpak_restore_root='saved', pikpak_restore_task_id='restore-job')
        self.cli.side_effect = [dict(status='Complete'), file_record('saved'), 'https://cdn.test/file']
        self.assertEqual(self.ns['_pikpak_download_url'](task), 'https://cdn.test/file')
        self.assertEqual(self.cli.call_args_list[0].args[0], ['task', 'get', 'restore-job'])
        self.assertTrue(all(c.args[0][:2] != ['share', 'restore'] for c in self.cli.call_args_list))

    def test_container_restore_matches_original_name_and_size(self):
        task = self.task(pikpak_restore_root='container')
        self.cli.side_effect = [dict(kind='drive#folder'), dict(files=[file_record('other', 'Other.mkv'),
                                                                    file_record('saved')]),
                                file_record('saved'), file_record('other', 'Other.mkv'),
                                'https://cdn.test/file']
        self.assertEqual(self.ns['_pikpak_download_url'](task), 'https://cdn.test/file')
        self.assertEqual(task.pikpak_saved_file_id, 'saved')

    def test_ambiguous_container_does_not_download_wrong_file(self):
        task = self.task(pikpak_restore_root='container')
        self.cli.side_effect = [dict(kind='drive#folder'), dict(files=[file_record('one'), file_record('two')]),
                                file_record('two'), file_record('one')]
        with self.assertRaisesRegex(RuntimeError, 'uniquely identify'):
            self.ns['_pikpak_download_url'](task)
        self.assertIsNone(task.pikpak_saved_file_id)

    def test_pending_restore_is_retryable_and_does_not_save_second_copy(self):
        task = self.task()
        self.cli.side_effect = [dict(file_id='saved', restore_task_id='job'), dict(status='Running')]
        self.ns['time'] = SimpleNamespace(monotonic=Mock(side_effect=[0, 121]))
        with self.assertRaisesRegex(RuntimeError, 'still saving'):
            self.ns['_pikpak_download_url'](task)
        self.assertEqual(task.pikpak_restore_root, 'saved')
        self.assertEqual(task.pikpak_restore_task_id, 'job')

    def test_no_transcoded_playback_fallback(self):
        task = self.task(pikpak_saved_file_id='saved')
        self.cli.return_value = ''
        with self.assertRaisesRegex(RuntimeError, 'original-file download URL'):
            self.ns['_pikpak_download_url'](task)
        self.cli.assert_called_once_with(['download', 'saved', '--url-only'], json_output=False)

    def test_official_http_download_link_is_accepted_without_rewriting_it(self):
        task = self.task(pikpak_saved_file_id='saved')
        link = 'http://dl-z01a-0016.mypikpak.com/original.mkv?signature=example'
        self.cli.return_value = link
        self.assertEqual(self.ns['_pikpak_download_url'](task), link)

    def test_non_download_schemes_and_multiple_links_are_rejected(self):
        task = self.task(pikpak_saved_file_id='saved')
        for link in ('file:///etc/passwd', 'ftp://server/file', 'http:///file',
                     'http://server/one\nhttp://server/two'):
            self.cli.return_value = link
            with self.subTest(link=link), self.assertRaisesRegex(RuntimeError, 'original-file download URL'):
                self.ns['_pikpak_download_url'](task)

    def test_session_roundtrip_preserves_file_ids_and_contains_no_account_token(self):
        task = self.task(pikpak_saved_file_id='saved', pikpak_restore_root='saved',
                         pikpak_restore_task_id='job', season_override=2)
        data = asdict(task)
        restored = self.ns['task_from_dict'](data)
        self.assertEqual(asdict(restored), data)
        self.assertNotIn('private-test-token', json.dumps(data))
        with tempfile.TemporaryDirectory() as directory:
            self.ns.update(SESSION_FILE=str(Path(directory, 'session.json')),
                           _session_save_lock=threading.Lock(), _last_session_save=0,
                           datetime=__import__('datetime').datetime)
            self.ns['save_session']([task])
            saved = Path(directory, 'session.json').read_text()
        self.assertNotIn('private-test-token', saved)
        self.assertIn('saved', saved)

    def test_queue_identity_does_not_change_on_refresh_or_contextual_filename_edits(self):
        task = self.task()
        key = self.ns['_queue_task_key'](task)
        task.pikpak_saved_file_id = 'saved'
        task.filename = 'Renamed.S02E01.mkv'
        self.assertEqual(self.ns['_queue_task_key'](task), key)
        self.assertNotEqual(self.ns['_queue_task_key'](self.task(pikpak_file_id='other')), key)

    def test_worker_uses_existing_transfer_and_drive_processing(self):
        task = self.task(pikpak_saved_file_id='saved')
        self.cli.side_effect = ['https://cdn.test/first', 'https://cdn.test/fresh']
        refreshes = []
        def download(url, name, destination, cookie, **kwargs):
            self.assertEqual(url, 'https://cdn.test/first')
            self.assertEqual(name, 'Show.S01E01.mkv')
            self.assertIsNone(cookie)
            refreshes.append(kwargs['url_refresh']())
            return '/content/video.mkv'
        self.ns['download_with_aria2'] = download
        result = self.ns['download_worker'](task, '')
        self.assertEqual(result.status, 'done')
        self.assertEqual(refreshes, ['https://cdn.test/fresh'])
        self.ns['handle_file_processing'].assert_called_once_with('/content/video.mkv', source='pikpak')

    def test_worker_failure_remains_retryable(self):
        task = self.task()
        self.cli.side_effect = RuntimeError('PikPak storage or transfer limit reached')
        self.ns['download_with_aria2'] = Mock()
        result = self.ns['download_worker'](task, '')
        self.assertEqual(result.status, 'failed')
        self.assertIn('limit', result.error)
        self.ns['download_with_aria2'].assert_not_called()
        self.assertTrue(any(result.error in str(call) for call in self.ns['print'].call_args_list))

    def test_existing_drive_file_is_skipped_before_any_cloud_save(self):
        self.ns['check_duplicate_in_drive'].return_value = True
        result = self.ns['download_worker'](self.task(), '')
        self.assertEqual(result.status, 'skipped')
        self.cli.assert_not_called()

    def test_worker_keeps_transfer_http_error_and_saved_file_id_for_retry(self):
        task = self.task(pikpak_saved_file_id='saved')
        self.cli.return_value = 'http://dl-z01.mypikpak.com/file'

        def failed_transfer(*args, **kwargs):
            self.ns['download_stats'][kwargs['task_id']] = {
                'error': 'Download failed after 4 attempt(s): HTTP 503: server unavailable'}
            return None

        self.ns['download_with_aria2'] = failed_transfer
        result = self.ns['download_worker'](task, '')
        self.assertEqual(result.status, 'failed')
        self.assertIn('HTTP 503', result.error)
        self.assertEqual(result.pikpak_saved_file_id, 'saved')
        self.cli.assert_called_once_with(['download', 'saved', '--url-only'], json_output=False)
        self.ns['handle_file_processing'].assert_not_called()

    def test_cancellation_stops_before_saving(self):
        self.ns['_cancel_requested'] = True
        result = self.ns['download_worker'](self.task(), '')
        self.assertEqual(result.status, 'pending')
        self.cli.assert_not_called()


class PikPakClientTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject()
        self.ns['_pikpak_cli_path'] = Mock(return_value='/content/pikpak')
        self.process = Mock(returncode=0)
        self.process.communicate.return_value = ('{"files": []}', '')
        self.process.poll.return_value = 0
        self.ns['subprocess'] = SimpleNamespace(Popen=Mock(return_value=self.process),
                                               PIPE=-1, DEVNULL=-3, TimeoutExpired=subprocess.TimeoutExpired)

    def test_token_is_only_in_environment_and_logging_is_disabled(self):
        self.ns['_pikpak_cli'](['ls', '--raw'])
        args, kwargs = self.ns['subprocess'].Popen.call_args
        self.assertNotIn('private-test-token', ' '.join(args[0]))
        self.assertEqual(kwargs['env']['PIKPAK_TOKEN'], 'private-test-token')
        self.assertEqual(kwargs['env']['PIKPAK_NO_LOG'], '1')
        self.assertTrue(kwargs['env']['PIKPAK_DIR'].startswith('/content/'))
        self.ns['_unregister_proc'].assert_called_once()

    def test_errors_hide_tokens_urls_and_account_diagnostics(self):
        self.process.returncode = 1
        self.process.communicate.return_value = ('', 'expired token private-test-token https://secret.test?key=token')
        with self.assertRaisesRegex(RuntimeError, 'invalid or expired') as error:
            self.ns['_pikpak_cli'](['ls', '--raw'])
        self.assertNotIn('private-test-token', str(error.exception))
        self.assertNotIn('secret.test', str(error.exception))

    def test_malformed_response_fails_without_echoing_content(self):
        self.process.communicate.return_value = ('not JSON private-test-token', '')
        with self.assertRaisesRegex(RuntimeError, 'unreadable response'):
            self.ns['_pikpak_cli'](['ls', '--raw'])

    def test_structured_service_error_preserves_code_and_sanitized_reason(self):
        self.process.returncode = 1
        self.process.communicate.return_value = ('', json.dumps({'error': {
            'code': 'FILE_NOT_FOUND', 'message': 'File cannot be restored',
            'reason': 'private-test-token https://secret.test?key=abc missing file',
            'hint': 'Verify the selected file ID'}}))
        with self.assertRaisesRegex(RuntimeError, 'FILE_NOT_FOUND') as error:
            self.ns['_pikpak_cli'](['share', 'restore', 'share', '--file-ids', 'file'])
        text = str(error.exception)
        self.assertIn('saving shared file', text)
        self.assertIn('missing file', text)
        self.assertIn('Verify the selected file ID', text)
        self.assertNotIn('private-test-token', text)
        self.assertNotIn('secret.test', text)

    def test_service_error_in_success_exit_is_still_actionable(self):
        self.process.communicate.return_value = (json.dumps({'error': {
            'code': 'ACCESS_DENIED', 'reason': 'Missing files:manage scope'}}), '')
        with self.assertRaisesRegex(RuntimeError, 'ACCESS_DENIED') as error:
            self.ns['_pikpak_cli'](['share', 'restore', 'share', '--file-ids', 'file'])
        self.assertIn('Read files and Manage files', str(error.exception))

    def test_text_error_schema_preserves_code_and_hides_credentials(self):
        self.process.returncode = 1
        self.process.communicate.return_value = ('',
            'Error: request failed\n  Code: REQUEST_FAILED\n  Reason: Bearer other-secret '
            'https://private.test?key=value\n  Hint: Try again later\n')
        with self.assertRaisesRegex(RuntimeError, 'REQUEST_FAILED') as error:
            self.ns['_pikpak_cli'](['download', 'file', '--url-only'], json_output=False)
        self.assertIn('getting download link', str(error.exception))
        self.assertIn('Try again later', str(error.exception))
        self.assertNotIn('other-secret', str(error.exception))
        self.assertNotIn('private.test', str(error.exception))

    def test_missing_token_does_not_install_or_start_client(self):
        self.ns['token_pikpak'].value = ''
        with self.assertRaisesRegex(RuntimeError, 'PIKPAK_TOKEN'):
            self.ns['_pikpak_cli'](['ls'])
        self.ns['_pikpak_cli_path'].assert_not_called()
        self.ns['subprocess'].Popen.assert_not_called()

    def test_cancelled_client_is_killed_and_unregistered(self):
        self.process.poll.return_value = None
        checks = iter([False, True])
        self.ns['cancel_requested'] = lambda: next(checks)
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
            self.ns['_pikpak_cli'](['ls'])
        self.process.kill.assert_called_once()
        self.ns['_unregister_proc'].assert_called_once()


class PikPakInstallerTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ns['COLAB_ROOT'] = self.temp.name
        self.ns['platform'] = SimpleNamespace(system=lambda: 'Windows', machine=lambda: 'AMD64')
        self.payload = b'official client test fixture'
        self.ns['PIKPAK_CLI_SHA256'] = {'windows_amd64': hashlib.sha256(self.payload).hexdigest()}
        self.response = Mock()
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.response.iter_content.return_value = [self.payload]
        self.ns['requests'].get.return_value = self.response

    def test_verified_download_is_cached_and_reused(self):
        path = self.ns['_pikpak_cli_path']()
        self.assertEqual(Path(path).read_bytes(), self.payload)
        self.assertEqual(self.ns['_pikpak_cli_path'](), path)
        self.ns['requests'].get.assert_called_once()
        self.assertFalse(Path(path + '.tmp').exists())

    def test_checksum_mismatch_does_not_leave_executable_or_partial(self):
        self.response.iter_content.return_value = [b'corrupt binary']
        with self.assertRaisesRegex(RuntimeError, 'checksum verification failed'):
            self.ns['_pikpak_cli_path']()
        self.assertEqual(list(Path(self.temp.name).rglob('*.exe')), [])
        self.assertEqual(list(Path(self.temp.name).rglob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
