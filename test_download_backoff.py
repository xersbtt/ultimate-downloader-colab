"""Exercise transfer recovery without starting the notebook UI or aria2."""
import ast
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional
from unittest.mock import Mock
from urllib.parse import urlparse


class DownloadBackoffTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name('ultimate_downloader.py')
        tree = ast.parse(source.read_text(encoding='utf-8'))
        names = {'THROTTLE_BACKOFF_SECS', 'THROTTLE_MAX_BACKOFF_SECS',
                 'MAX_THROTTLE_WAITS', 'SERVER_BACKOFF_SECS',
                 'SERVER_MAX_BACKOFF_SECS', 'MAX_SERVER_WAITS'}
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.FunctionDef) and n.name in
                  {'download_with_aria2', '_speed_to_mbs', '_redact_transfer_error',
                   '_url_host', 'url_matches_host'}) or
                 (isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id in names for t in n.targets))]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, 'video.mkv')
        self.calls = []
        self.waits = []
        self.sleep_count = 0
        self.outputs = []
        self.ns = dict(
            os=os, re=re, Optional=Optional, Callable=Callable,
            sanitize_filename=lambda name: name, check_duplicate_in_drive=lambda _: False,
            print_lock=threading.Lock(), progress_lock=threading.Lock(),
            download_stats={}, progress_bar=SimpleNamespace(),
            _wait_for_disk_space=lambda *a, **k: True, DISK_START_GB=1,
            RATE_LIMITED_HOSTS=('torbox.app', 'tb-cdn.io'), urlparse=urlparse,
            _cancel_requested=False, _register_proc=Mock(), _unregister_proc=Mock(),
            DISK_CHECK_SECS=10, print=Mock(),
            time=SimpleNamespace(time=lambda: 100, sleep=self.sleep),
            subprocess=SimpleNamespace(Popen=self.popen, PIPE=-1, STDOUT=-2))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.ns)

    def sleep(self, seconds):
        self.sleep_count += seconds

    def popen(self, cmd, **kwargs):
        self.waits.append(self.sleep_count)
        self.sleep_count = 0
        self.calls.append(list(cmd))
        lines, code = self.outputs.pop(0)
        if code == 0:
            self.path.write_bytes(b'complete')
        return SimpleNamespace(stdout=lines, returncode=code, wait=lambda: None)

    def run_download(self, refresh=None, url='https://test.tb-cdn.io/video'):
        return self.ns['download_with_aria2'](
            url, self.path.name, self.temp.name,
            task_id='task', url_refresh=refresh)

    def test_stalled_retries_are_capped_and_use_one_connection(self):
        self.outputs = [(['status=429'], 1)] * 4 + [([], 0)]
        self.assertEqual(self.run_download(), str(self.path))
        self.assertEqual(self.waits, [0, 5, 10, 20, 30])
        for cmd in self.calls[1:]:
            self.assertEqual(cmd[cmd.index('-x') + 1], '1')
            self.assertEqual(cmd[cmd.index('-s') + 1], '1')
            self.assertIn('-c', cmd)

    def test_progress_at_99_percent_resets_throttle_budget(self):
        self.outputs = [(['[#abcdef 990MiB/1GiB(99%)]', 'status=429'], 1)]
        for size in range(991, 999):
            self.outputs.append(([f'[#abcdef {size}MiB/1GiB(99%)]', 'status=429'], 1))
        self.outputs.append(([], 0))
        self.assertEqual(self.run_download(), str(self.path))
        self.assertEqual(self.waits, [0] + [5] * 9)

    def test_later_error_line_does_not_hide_429_or_refresh_link(self):
        refresh = Mock()
        self.outputs = [(['status=429', 'errorCode=22 download aborted'], 1), ([], 0)]
        self.assertEqual(self.run_download(refresh), str(self.path))
        refresh.assert_not_called()
        self.assertEqual(self.waits, [0, 5])

    def test_permanent_throttle_still_terminates(self):
        self.outputs = [(['[#abcdef 990MiB/1GiB(99%)]', 'status=429'], 1)] * 7
        self.assertIsNone(self.run_download())
        self.assertEqual(len(self.calls), 7)

    def test_cancel_during_backoff_preserves_partial(self):
        self.path.write_bytes(b'partial')
        self.outputs = [(['status=429'], 1)]
        def cancel(_seconds):
            self.ns['_cancel_requested'] = True
        self.ns['time'].sleep = cancel
        self.assertIsNone(self.run_download())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.path.read_bytes(), b'partial')

    def test_pikpak_503_waits_before_refresh_and_reduces_connections(self):
        self.outputs = [(['status=503', 'errorCode=22 download aborted'], 1)] * 2 + [([], 0)]
        refresh_times = []

        def refresh():
            refresh_times.append(self.sleep_count)
            self.assertNotIn('retry_reason', self.ns['download_stats']['task'])
            return 'http://dl-z01.mypikpak.com/fresh'

        self.assertEqual(self.run_download(refresh, 'http://dl-z01.mypikpak.com/file'), str(self.path))
        self.assertEqual(self.waits, [0, 15, 30])
        self.assertEqual(refresh_times, [15, 30])
        self.assertEqual([cmd[cmd.index('-x') + 1] for cmd in self.calls], ['16', '4', '1'])
        self.assertEqual([cmd[cmd.index('-s') + 1] for cmd in self.calls], ['16', '4', '1'])
        self.assertTrue(all('-c' in cmd for cmd in self.calls))
        self.assertEqual(self.calls[-1][1], 'http://dl-z01.mypikpak.com/fresh')
        self.assertNotIn('error', self.ns['download_stats']['task'])

    def test_permanent_503_terminates_with_status_and_redacted_details(self):
        self.outputs = [(['status=503', 'errorCode=29 server unavailable https://cdn.test/file?secret=hidden'], 1)] * 4
        self.assertIsNone(self.run_download(url='http://dl-z01.mypikpak.com/file'))
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.waits, [0, 15, 30, 60])
        error = self.ns['download_stats']['task']['error']
        self.assertIn('HTTP 503', error)
        self.assertIn('4 attempt(s)', error)
        self.assertNotIn('secret=', error)
        self.assertNotIn('secret=', str(self.ns['print'].call_args_list))

    def test_503_cancellation_keeps_partial_and_control_file_without_refresh(self):
        self.path.write_bytes(b'partial')
        control = Path(str(self.path) + '.aria2')
        control.write_bytes(b'resume metadata')
        self.outputs = [(['status=503'], 1)]
        refresh = Mock()

        def cancel(_seconds):
            stats = self.ns['download_stats']['task']
            self.assertIn('HTTP 503', stats['retry_reason'])
            self.assertEqual(stats['retry_until'], 115)
            self.ns['_cancel_requested'] = True

        self.ns['time'].sleep = cancel
        self.assertIsNone(self.run_download(refresh, 'http://dl-z01.mypikpak.com/file'))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.path.read_bytes(), b'partial')
        self.assertEqual(control.read_bytes(), b'resume metadata')
        refresh.assert_not_called()
        self.assertNotIn('error', self.ns['download_stats']['task'])

    def test_other_host_503_does_not_change_connection_count(self):
        self.outputs = [(['status=503'], 1), ([], 0)]
        self.assertEqual(self.run_download(url='https://cdn.example.test/file'), str(self.path))
        self.assertEqual(self.waits, [0, 15])
        self.assertEqual([cmd[cmd.index('-x') + 1] for cmd in self.calls], ['16', '16'])

    def test_403_consumes_normal_attempts_and_refreshes_links(self):
        self.outputs = [(['errorCode=29 The response status is not successful. status=403'], 1)] * 3
        refresh = Mock(return_value='https://test.tb-cdn.io/fresh')
        self.assertIsNone(self.run_download(refresh))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.waits, [0, 2, 4])
        self.assertEqual(refresh.call_count, 2)
        self.assertIn('HTTP 403', self.ns['download_stats']['task']['error'])

    def test_launch_failure_redacts_url_and_authentication(self):
        self.ns['subprocess'].Popen = Mock(side_effect=OSError(
            'cannot launch https://cdn.test/file?secret=hidden Bearer private-token accountToken=cookie-secret'))
        self.assertIsNone(self.run_download())
        error = self.ns['download_stats']['task']['error']
        self.assertIn('cannot launch', error)
        self.assertNotIn('secret=', error)
        self.assertNotIn('private-token', error)
        self.assertNotIn('cookie-secret', error)
        self.assertNotIn('private-token', str(self.ns['print'].call_args_list))


if __name__ == '__main__':
    unittest.main()
