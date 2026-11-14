"""Donor-link validation, bounded HTTP probes and shared connection limits."""
import ast
import hashlib
import html
import os
import queue
import re
import tempfile
import threading
import unittest
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from uuid import uuid4

import requests


SOURCE = Path(__file__).with_name('ultimate_downloader.py')
NAME = 'Example.Show.EP01.mkv'
LINK = 'https://chauthanh.info/drama/download/eri2/example-token/' + NAME
CDN = 'https://eri2.chauthanh.info/signed-file/' + NAME
PREFIX = b'\x1aE\xdf\xa3' + bytes(28)


def load_subject(with_login=False, stub_ip_status=True):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'DownloadTask', 'task_from_dict', 'resolve_chauthanh', '_download_chauthanh',
             '_classify_url', '_url_host', 'url_matches_host', 'resolve_all_links',
             'download_worker', '_redact_transfer_error', '_get_chauthanh_session'}
    constants = {'_TASK_FIELDS', '_chauthanh_transfer_slots', 'STREAMING_HOSTS',
                 'DEBRID_SUPPORTED_HOSTS', '_chauthanh_session_lock', '_chauthanh_cached_session',
                 '_chauthanh_session_key', '_chauthanh_session_time', '_chauthanh_profile_url',
                 '_chauthanh_browser', '_chauthanh_browser_panel'}
    nodes = [node for node in tree.body if
             isinstance(node, (ast.FunctionDef, ast.ClassDef)) and
             (node.name in names or node.name.startswith(('_chauthanh_', 'ChauThanh', '_ChauThanh'))) or
             isinstance(node, ast.Assign) and any(
                 isinstance(t, ast.Name) and t.id in constants for t in node.targets)]
    ns = dict(os=os, re=re, queue=queue, hashlib=hashlib, html=html,
        HTMLParser=HTMLParser, nullcontext=nullcontext, requests=SimpleNamespace(
        Session=Mock(), RequestException=requests.RequestException),
        dataclass=dataclass, field=field, fields=fields, Any=Any, Callable=Callable,
        Dict=Dict, List=List, Optional=Optional, Tuple=Tuple, uuid4=uuid4,
        urlparse=urlparse, urljoin=urljoin, unquote=unquote, quote=quote, parse_qs=parse_qs,
        BoundedSemaphore=threading.BoundedSemaphore, RLock=threading.RLock, ThreadPoolExecutor=ThreadPoolExecutor,
        time=SimpleNamespace(monotonic=lambda: 100, sleep=Mock()),
        token_chauthanh_username=SimpleNamespace(value=''), token_chauthanh_password=SimpleNamespace(value=''),
        token_chauthanh_session=SimpleNamespace(value=''),
        _cancel_requested=False, REQUEST_TIMEOUT=30, print=Mock(),
        _tb_url_refresher=Mock(return_value=None), _strip_size_suffix=lambda name: name,
        progress_lock=threading.Lock(), print_lock=threading.Lock(),
        download_stats={}, download_with_aria2=Mock(return_value=None),
        check_duplicate_in_drive=Mock(return_value=False), handle_file_processing=Mock(),
        COLAB_ROOT='/content/', DUPLICATE_SKIP=object(), cancel_requested=lambda: False)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    if not with_login:
        ns['_get_chauthanh_session'] = Mock(return_value=None)
    if stub_ip_status:
        ns['_chauthanh_ip_status_refresh'] = Mock(return_value=True)
    return ns


def response(status=206, *, headers=None, prefix=PREFIX):
    reply = Mock(status_code=status, headers=(headers if headers is not None else {
        'Content-Range': 'bytes 0-31/1000', 'Content-Type': 'video/webm; charset=UTF-8'}))
    reply.raw.read.return_value = prefix
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    return reply


class ChauThanhTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject()
        self.session = Mock()
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.ns['requests'].Session.return_value = self.session
        self.task = self.ns['resolve_chauthanh'](LINK)[0]

    def probe(self, *replies):
        self.session.get.side_effect = replies
        return self.ns['_chauthanh_download_url'](self.task)

    def test_native_route_precedes_debrid_without_substring_matches(self):
        for debrid in (False, True):
            for link in (LINK, CDN, 'https://chauthanh.info/drama/view/example.html'):
                self.assertEqual(self.ns['_classify_url'](link, debrid), 'chauthanh')
        for link in ('https://evil.test/?u=chauthanh.info',
                     'https://chauthanh.info.evil.test/file'):
            self.assertEqual(self.ns['_classify_url'](link, False), 'direct')

    def test_resolution_does_not_contact_server_and_preserves_input_for_resume(self):
        tasks, youtube, mega, debrid = self.ns['resolve_all_links'](
            [LINK, CDN], Mock(), {}, 'rd-key', 'tb-key', 'tb')
        self.assertEqual(len(tasks), 2)
        self.assertEqual((tasks[0].filename, tasks[0].source, tasks[0].link_type),
                         (NAME, 'chauthanh', 'chauthanh'))
        self.assertEqual(tasks[0].original_url, LINK)
        self.assertEqual((youtube, mega, debrid), ([], [], []))
        self.ns['requests'].Session.assert_not_called()
        saved = asdict(tasks[0])
        self.assertEqual(self.ns['task_from_dict'](saved).original_url, LINK)
        self.assertIsNone(saved['cookie'])

    def test_non_donor_and_unsafe_urls_are_not_queued(self):
        for link in ('https://chauthanh.info/drama/view/show.html',
                     LINK.replace('/eri2/', '/eri7/'), CDN.replace('eri2.', 'eri7.'),
                     LINK.replace('https:', 'http:'),
                     LINK.replace('chauthanh.info', 'chauthanh.info.evil.test'),
                     LINK.replace('chauthanh.info', 'user:secret@chauthanh.info'),
                     LINK.replace('chauthanh.info', 'chauthanh.info:444'),
                     LINK.replace(NAME, 'bad%2Fpath.mkv'),
                     LINK.replace(NAME, 'bad%0D%0Aheader.mkv'),
                     LINK + '.html'):
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.ns['resolve_chauthanh'](link)
        self.ns['requests'].Session.assert_not_called()

    def test_show_page_failure_is_actionable_without_debrid_fallback(self):
        tasks, youtube, mega, debrid = self.ns['resolve_all_links'](
            ['https://chauthanh.info/drama/view/show.html'], Mock(), {}, 'rd-key')
        self.assertEqual((tasks, youtube, mega, debrid), ([], [], [], []))
        self.assertIn('copy the donor download link', str(self.ns['print'].call_args))

    def test_redirect_probe_reads_32_bytes_and_retains_verified_size(self):
        redirect = response(302, headers={'Location': CDN})
        file = response()
        self.assertEqual(self.probe(redirect, file), CDN)
        self.assertEqual(self.task.chauthanh_size, 1000)
        self.assertEqual(self.task.url, LINK)
        file.raw.read.assert_called_once_with(32)
        redirect.raw.read.assert_not_called()
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs['headers']['Range'], 'bytes=0-31')
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertTrue(call.kwargs['stream'])
        self.assertEqual(file.__exit__.call_count, 1)
        self.session.__exit__.assert_called_once()

    def test_direct_cdn_link_is_supported(self):
        self.task.original_url = CDN
        self.assertEqual(self.probe(response()), CDN)

    def test_observed_php_donor_link_uses_name_parameter(self):
        link = ('https://eri2.chauthanh.info/download.php?id=1&file=private-token&name='
                + quote('Example Show EP01.mkv'))
        task = self.ns['resolve_chauthanh'](link)[0]
        self.assertEqual(task.filename, 'Example Show EP01.mkv')
        self.assertEqual(task.original_url, link)
        self.ns['requests'].Session.assert_not_called()

    def test_php_name_parameter_cannot_be_missing_duplicated_or_a_path(self):
        root = 'https://eri2.chauthanh.info/download.php?file=private-token'
        for query in ('', '&name=a.mkv&name=b.mkv', '&name=..%2Fa.mkv', '&name=a%0Amkv'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.ns['resolve_chauthanh'](root + query)

    def test_redirects_never_contact_unverified_hosts_or_downgrade_to_free(self):
        for destination in ('https://evil.test/file', CDN.replace('eri2.', 'eri7.'),
                            'http://eri2.chauthanh.info/file', '/forum/login.html'):
            self.session.get.reset_mock()
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.probe(response(302, headers={'Location': destination}))
            self.assertEqual(self.session.get.call_count, 1)

    def test_redirect_loop_is_bounded(self):
        with self.assertRaisesRegex(ValueError, 'too many'):
            self.probe(*[response(302, headers={'Location': CDN}) for _ in range(5)])
        self.assertEqual(self.session.get.call_count, 5)

    def test_refusal_has_login_guidance_and_no_signed_path(self):
        for status in (401, 403):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'donor server refused'):
                self.probe(response(302, headers={'Location': CDN}), response(status))

    def test_challenge_at_original_link_is_reported_without_contacting_cdn(self):
        challenge = response(403, headers={'cf-mitigated': 'challenge'})
        with self.assertRaisesRegex(ValueError, 'Cloudflare'):
            self.probe(challenge)
        self.assertEqual(self.session.get.call_count, 1)
        challenge.raw.read.assert_not_called()

    def test_challenge_body_is_bounded_and_never_printed_in_diagnostics(self):
        page = response(403, headers={'Server': 'cloudflare'},
                        prefix=b'<title>Just a moment...</title> private-token')
        self.session.get.side_effect = [page]
        diagnostics = []
        with self.assertRaisesRegex(ValueError, 'entry website'):
            self.ns['_chauthanh_download_url'](self.task, diagnostics.append)
        page.raw.read.assert_called_once_with(2048, decode_content=True)
        self.assertEqual(diagnostics, [{'host': 'chauthanh.info', 'status': 403, 'challenge': True}])
        self.assertNotIn('private-token', str(diagnostics))

    def test_plain_403_does_not_claim_cloudflare_or_login_failure(self):
        page = response(403, headers={'Server': 'cloudflare'}, prefix=b'Forbidden')
        with self.assertRaisesRegex(ValueError, 'entry website refused') as caught:
            self.probe(page)
        self.assertNotIn('Cloudflare challenge', str(caught.exception))
        self.assertIn('no challenge marker observed', str(caught.exception))

    def test_ip_allowlist_message_is_recognised_even_with_http_200(self):
        body = b'Your IP (203.0.113.1/203.0.113.1) is not in the allowed list, please contact admin!'
        self.task.original_url = CDN
        diagnostics = []
        self.session.get.return_value = response(200, headers={'Content-Type': 'text/html'}, prefix=body)
        with self.assertRaisesRegex(self.ns['ChauThanhAccessError'], 'not in its allowed list') as caught:
            self.ns['_chauthanh_download_url'](self.task, diagnostic=diagnostics.append)
        self.assertTrue(caught.exception.info['ip_not_allowed'])
        self.assertNotIn('203.0.113.1', str(caught.exception))
        self.assertNotIn('203.0.113.1', str(diagnostics))

    def test_donor_challenge_is_distinct_from_entry_website_challenge(self):
        with self.assertRaisesRegex(ValueError, 'Cloudflare challenge at the donor server'):
            self.probe(response(302, headers={'Location': CDN}),
                       response(403, headers={'cf-mitigated': 'challenge'}))

    def test_success_diagnostics_report_each_host_without_reading_extra_bytes(self):
        self.session.get.side_effect = [response(302, headers={'Location': CDN}), response()]
        diagnostics = []
        self.assertEqual(self.ns['_chauthanh_download_url'](self.task, diagnostics.append), CDN)
        self.assertEqual([(d['host'], d['status']) for d in diagnostics],
                         [('chauthanh.info', 302), ('eri2.chauthanh.info', 206)])

    def test_http_success_html_and_bad_ranges_are_rejected(self):
        replies = [response(200), response(headers={'Content-Type': 'text/html',
                    'Content-Range': 'bytes 0-31/1000'}),
                   response(prefix=b'<!DOCTYPE html>' + bytes(17)),
                   response(prefix=b'not video' + bytes(23)),
                   response(headers={'Content-Range': 'bytes 32-63/1000'}),
                   response(headers={'Content-Range': 'bytes 0-31/*'}),
                   response(headers={'Content-Range': 'bytes 0-31/10'}),
                   response(prefix=PREFIX[:10]),
                   response(headers={'Content-Range': 'bytes 0-0/1000'})]
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                self.probe(response(302, headers={'Location': CDN}), reply)

    def test_ass_subtitles_are_not_confused_with_json(self):
        self.task.filename = 'English.ass'
        file = response(prefix=b'[Script Info]\r\n' + bytes(17))
        self.assertEqual(self.probe(response(302, headers={'Location': CDN}), file), CDN)

    def test_changed_file_size_blocks_resume(self):
        self.task.chauthanh_size = 2000
        with self.assertRaisesRegex(ValueError, 'size changed'):
            self.probe(response(302, headers={'Location': CDN}), response())

    def test_network_errors_do_not_expose_signed_urls(self):
        self.session.get.side_effect = requests.ConnectionError('failed: ' + LINK)
        result = self.ns['download_worker'](self.task, '')
        self.assertEqual(result.status, 'failed')
        self.assertIn('network request failed', result.error)
        self.assertNotIn('example-token', result.error)
        self.assertNotIn('example-token', str(self.ns['print'].call_args_list))

    def test_worker_skips_drive_duplicates_before_probe(self):
        self.ns['check_duplicate_in_drive'].return_value = True
        self.assertEqual(self.ns['download_worker'](self.task, '').status, 'skipped')
        self.ns['requests'].Session.assert_not_called()

    def test_selected_worker_uses_refresh_and_normal_drive_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, NAME)
            path.write_bytes(bytes(1000))
            self.ns['_chauthanh_download_url'] = Mock(return_value=CDN)
            self.task.chauthanh_size = 1000
            self.ns['download_with_aria2'].return_value = str(path)
            result = self.ns['download_worker'](self.task, '')
        self.assertEqual(result.status, 'done')
        self.ns['handle_file_processing'].assert_called_once_with(str(path), source='chauthanh')
        arguments = self.ns['download_with_aria2'].call_args
        self.assertEqual(arguments.kwargs['source'], 'chauthanh')
        self.assertEqual(arguments.kwargs['url_refresh'](), CDN)

    def test_incomplete_file_is_kept_and_not_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, NAME)
            path.write_bytes(b'<html>access denied</html>')
            self.ns['_chauthanh_download_url'] = Mock(return_value=CDN)
            self.task.chauthanh_size = 1000
            self.ns['download_with_aria2'].return_value = str(path)
            result = self.ns['download_worker'](self.task, '')
            self.assertTrue(path.exists())
        self.assertEqual(result.status, 'failed')
        self.assertIn('size differs', result.error)
        self.ns['handle_file_processing'].assert_not_called()

    def test_shared_slots_allow_three_files_and_cancellation_releases_them(self):
        slots = self.ns['_chauthanh_transfer_slots']
        for _ in range(3):
            self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        self.ns['_cancel_requested'] = True
        self.assertIsNone(self.ns['_download_chauthanh'](self.task))
        self.ns['requests'].Session.assert_not_called()
        for _ in range(3):
            slots.release()
        self.assertIsNone(self.ns['_download_chauthanh'](self.task))
        for _ in range(3):
            self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        for _ in range(3):
            slots.release()

    def test_probe_failure_releases_slot(self):
        self.session.get.side_effect = requests.Timeout()
        with self.assertRaises(RuntimeError):
            self.ns['_download_chauthanh'](self.task)
        slots = self.ns['_chauthanh_transfer_slots']
        for _ in range(3):
            self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        for _ in range(3):
            slots.release()

    def test_parallel_workers_allow_three_but_never_transfer_four_donor_files(self):
        lock = threading.Lock()
        three_entered = threading.Event()
        release = threading.Event()
        active = maximum = 0

        def transfer(*args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 3:
                    three_entered.set()
            try:
                self.assertTrue(release.wait(5))
                return None
            finally:
                with lock:
                    active -= 1

        self.ns['_chauthanh_download_url'] = Mock(return_value=CDN)
        self.ns['download_with_aria2'] = transfer
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(self.ns['_download_chauthanh'],
                                   self.ns['resolve_chauthanh'](LINK)[0]) for _ in range(5)]
            try:
                self.assertTrue(three_entered.wait(5))
            finally:
                release.set()
            for future in futures:
                self.assertIsNone(future.result(timeout=5))
        self.assertEqual(maximum, 3)


LOGIN_PAGE = '''<form id="login" method="POST"><input name="username">
<input name="password" type="password"><input name="ref" value="safe-ref">
<input name="passhash"></form><script>
var pass=hex_md5(hex_md5($("#password").val()));
pass+="0123456789abcdef0123456789abcdef";pass=hex_md5(pass);
</script>'''
PROFILE = '<h3>Profile</h3><span>Permission</span><p>Supporter</p>'


class ChauThanhLoginTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject(with_login=True)
        self.ns['token_chauthanh_username'].value = 'example-user'
        self.ns['token_chauthanh_password'].value = 'private-password'
        self.session = Mock(headers={})
        self.ns['requests'].Session.return_value = self.session

    def page(self, text, status=200, headers=None):
        return Mock(text=text, status_code=status, headers=headers or {})

    def successful_login(self):
        self.session.get.side_effect = [self.page(LOGIN_PAGE), self.page(PROFILE)]
        self.session.post.return_value = self.page('', 302, {'Location': '/forum/profile.html'})
        return self.ns['_get_chauthanh_session']()

    def test_login_uses_page_nonce_clears_password_and_verifies_profile(self):
        self.assertIs(self.successful_login(), self.session)
        call = self.session.post.call_args
        self.assertEqual(call.args[0], 'https://chauthanh.info/forum/login.html')
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(call.kwargs['headers'], {'Referer': 'https://chauthanh.info/forum/login.html',
                                                  'Origin': 'https://chauthanh.info'})
        data = call.kwargs['data']
        md5 = lambda s: hashlib.md5(s.encode('ascii')).hexdigest()
        self.assertEqual(data['passhash'], md5(md5(md5('private-password')) + '0123456789abcdef0123456789abcdef'))
        self.assertEqual(data['password'], '')
        self.assertEqual(data['username'], 'example-user')
        self.assertEqual(data['ref'], 'safe-ref')
        self.assertEqual(self.ns['_chauthanh_profile_url'], 'https://chauthanh.info/forum/profile.html')
        self.assertNotIn('private-password', str(self.session.post.call_args))
        self.assertNotIn('private-password', str(self.ns['print'].call_args_list))

    def test_one_login_is_shared_by_concurrent_workers(self):
        self.successful_login()
        with ThreadPoolExecutor(max_workers=5) as pool:
            self.assertTrue(all(session is self.session for session in pool.map(
                lambda _: self.ns['_get_chauthanh_session'](), range(20))))
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.ns['requests'].Session.call_count, 1)
        self.session.close.assert_not_called()

    def test_expired_cached_session_is_reverified(self):
        self.successful_login()
        self.ns['_chauthanh_session_time'] = -1000
        self.session.get.side_effect = [self.page(PROFILE)]
        self.assertIs(self.ns['_get_chauthanh_session'](), self.session)
        self.assertEqual(self.session.post.call_count, 1)

    def test_turnstile_markup_does_not_prevent_one_normal_login_submission(self):
        page = LOGIN_PAGE.replace('</form>', '<div class="cf-turnstile"></div>'
            '<input name="cf-turnstile-response" value=""></form>')
        self.session.get.side_effect = [self.page(page), self.page(PROFILE)]
        self.session.post.return_value = self.page('', 302, {'Location': '/forum/profile.html'})
        trace = []
        self.assertIs(self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append), self.session)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs['data']['cf-turnstile-response'], '')
        self.assertIn({'stage': 'login form', 'turnstile_present': True, 'ref_present': True}, trace)
        self.assertEqual([(event['method'], event['status']) for event in trace if 'method' in event],
                         [('GET', 200), ('POST', 302), ('GET', 200)])
        for private in ('private-password', 'example-user', '0123456789abcdef', 'passhash'):
            self.assertNotIn(private, str(trace))

    def test_actual_turnstile_failure_after_post_is_reported_once(self):
        page = LOGIN_PAGE.replace('</form>', '<div class="cf-turnstile"></div></form>')
        failure = page.replace('</form>', '<p>Turnstile verification failed.</p></form>')
        self.session.get.return_value = self.page(page)
        self.session.post.return_value = self.page(failure)
        with self.assertRaisesRegex(self.ns['ChauThanhChallengeError'], 'login submission'):
            self.ns['_get_chauthanh_session'](http_only=True)
        self.session.post.assert_called_once()
        self.assertIsNone(self.ns['_chauthanh_cached_session'])

    def test_observed_confirmation_code_error_stops_before_profile_or_ip_requests(self):
        self.session.get.return_value = self.page(LOGIN_PAGE.replace(
            '</form>', '<div class="cf-turnstile"></div></form>'))
        self.session.post.return_value = self.page(
            '<div id="content-full"><p>Confirmation code is not correct! Please try again.</p>'
            + LOGIN_PAGE + '</div>')
        trace = []
        with self.assertRaisesRegex(self.ns['ChauThanhChallengeError'], 'Open browser login') as caught:
            self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append)
        self.assertIn('Use this login', str(caught.exception))
        self.assertTrue(next(e for e in trace if e['stage'] == 'login result')['verification_error'])
        self.assertFalse(next(e for e in trace if e['stage'] == 'login result')['credentials_error'])
        self.assertTrue(next(e for e in trace if e.get('method') == 'POST')['challenge'])
        self.session.post.assert_called_once()
        self.assertEqual(self.session.get.call_count, 1)
        self.ns['_chauthanh_ip_status_refresh'].assert_not_called()
        self.assertIsNone(self.ns['_chauthanh_cached_session'])

    def test_unrelated_chat_or_script_mentions_do_not_become_a_challenge(self):
        page = LOGIN_PAGE + '<div id="chatinline"><p>Someone wrote: CAPTCHA failed.</p></div>' + \
               '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
        self.session.get.return_value = self.page(page)
        self.session.post.return_value = self.page(PROFILE + '<div id="chatbox"><p>CAPTCHA failed</p></div>')
        self.assertIs(self.ns['_get_chauthanh_session'](http_only=True), self.session)
        self.session.post.assert_called_once()

    def test_post_200_without_profile_link_checks_confirmed_profile_url(self):
        self.session.get.side_effect = [self.page(LOGIN_PAGE), self.page(PROFILE)]
        self.session.post.return_value = self.page('<p>Login successful</p><script>location="/forum/";</script>')
        trace = []
        self.assertIs(self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append), self.session)
        self.assertEqual(self.session.get.call_args.args[0], 'https://chauthanh.info/forum/profile.html')
        self.assertTrue(next(event for event in trace if event['stage'] == 'login result')['success_notice'])
        self.assertTrue(any(event.get('stage') == 'donor profile check' for event in trace))
        self.session.post.assert_called_once()

    def test_verification_error_outside_form_is_detected_after_submission(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page('<div class="box"><p>Please confirm that you are not a robot!</p></div>')
        trace = []
        with self.assertRaisesRegex(self.ns['ChauThanhChallengeError'], 'login submission'):
            self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append)
        self.assertTrue(next(event for event in trace if event['stage'] == 'login result')['verification_error'])
        self.assertEqual(self.session.get.call_count, 1)

    def test_explicit_credential_error_is_reported_without_echoing_account_text(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page('<div class="error">Invalid username or password for example-user.</div>')
        trace = []
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'username/password error'):
            self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append)
        self.assertNotIn('example-user', str(trace))
        self.assertTrue(next(event for event in trace if event['stage'] == 'login result')['credentials_error'])

    def test_profile_returning_login_form_does_not_approve_session(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page('<p>Continue...</p>')
        trace = []
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'protected profile'):
            self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append)
        self.assertTrue(next(event for event in trace if event['stage'] == 'profile result')['login_form_returned'])
        self.assertIsNone(self.ns['_chauthanh_cached_session'])

    def test_actual_public_profile_error_is_recognised_inside_http_200(self):
        self.session.get.side_effect = [self.page(LOGIN_PAGE), self.page(
            '<div id="content-full"><h3>Error</h3><p>You are not authorized to access this page, please re-check!</p></div>')]
        self.session.post.return_value = self.page(LOGIN_PAGE)
        trace = []
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'not authorized'):
            self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append)
        self.assertTrue(next(e for e in trace if e['stage'] == 'profile result')['not_authorized'])
        self.ns['_chauthanh_ip_status_refresh'].assert_not_called()

    def test_initial_login_get_has_home_referrer_and_preserves_return_field(self):
        self.successful_login()
        self.assertEqual(self.session.get.call_args_list[0].kwargs['headers'], {'Referer': 'https://chauthanh.info/'})
        self.assertEqual(self.session.post.call_args.kwargs['data']['ref'], 'safe-ref')
        self.ns['_chauthanh_ip_status_refresh'].assert_called_once()

    def test_feedback_preview_redacts_credentials_cookie_ip_url_and_token(self):
        self.session.cookies.values.return_value = ['private-cookie']
        text = '<nav>Do not include this navigation</nav><div id="content-full">' \
               'Error for example-user: private-password private-cookie 203.0.113.1 ' \
               'https://chauthanh.info/private?token=secret abcdef0123456789abcdef0123456789abcdef</div>'
        preview = self.ns['_chauthanh_safe_preview'](text, session=self.session)
        for private in ('example-user', 'private-password', 'private-cookie', '203.0.113.1', 'token=secret',
                        'abcdef0123456789abcdef0123456789abcdef', 'navigation'):
            self.assertNotIn(private, preview)
        self.assertIn('Error', preview)
        self.assertLessEqual(len(preview), 360)

    def test_feedback_preview_omits_profile_details_and_input_values(self):
        self.assertEqual(self.ns['_chauthanh_safe_preview'](PROFILE), '[account profile details omitted]')
        text = '<div id="content"><form id="login"><input name="password" value="never-output-this"></form><p>Login failed.</p></div>'
        preview = self.ns['_chauthanh_safe_preview'](text)
        self.assertNotIn('never-output-this', preview)
        self.assertIn('Login failed', preview)

    def test_ip_status_refresh_uses_provider_hook_but_does_not_claim_file_access(self):
        ns = load_subject(with_login=True, stub_ip_status=False)
        session = Mock(headers={})
        session.get.return_value = self.page('Your ip is 203.0.113.1')
        trace = []
        self.assertTrue(ns['_chauthanh_ip_status_refresh'](
            session, 'https://chauthanh.info/forum/profile.html', diagnostic=trace.append))
        self.assertEqual(session.get.call_args.args[0], 'https://chauthanh.info/ip')
        self.assertNotIn('203.0.113.1', str(trace))
        self.assertEqual(trace[-1], {'stage': 'IP status result', 'request_completed': True})

    def test_failed_ip_status_request_does_not_discard_verified_supporter_login(self):
        ns = load_subject(with_login=True, stub_ip_status=False)
        ns['token_chauthanh_username'].value = 'example-user'
        ns['token_chauthanh_password'].value = 'private-password'
        session = Mock(headers={})
        session.get.side_effect = [self.page(LOGIN_PAGE), self.page(PROFILE),
                                   requests.RequestException('private request context')]
        session.post.return_value = self.page('', 302, {'Location': '/forum/profile.html'})
        ns['requests'].Session.return_value = session
        trace = []
        self.assertIs(ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append), session)
        self.assertEqual(trace[-1], {'stage': 'IP status result', 'request_completed': False})
        session.close.assert_not_called()
        self.assertNotIn('private request context', str(trace))

    def test_sidebar_feedback_is_excluded_but_main_page_feedback_remains(self):
        text = '<aside class="sidebar"><div>Wrong password. CAPTCHA failed.</div></aside><p>Login successful.</p>'
        result = self.ns['_chauthanh_login_result'](text)
        self.assertFalse(result['credentials_error'])
        self.assertFalse(result['verification_error'])
        self.assertTrue(result['success_notice'])

    def test_captcha_labels_do_not_turn_a_password_error_into_verification_failure(self):
        text = '<form id="login"><p>Username Password CAPTCHA</p><p>Invalid username or password.</p></form>'
        result = self.ns['_chauthanh_login_result'](text)
        self.assertTrue(result['credentials_error'])
        self.assertFalse(result['verification_error'])

    def test_confirmation_code_labels_and_chat_are_not_verification_errors(self):
        result = self.ns['_chauthanh_login_result'](
            '<form id="login"><p>Confirmation code</p><p>Invalid username or password.</p></form>'
            '<div id="chatinline">Confirmation code is not correct!</div>')
        self.assertFalse(result['verification_error'])
        self.assertTrue(result['credentials_error'])

    def test_invalid_csrf_is_not_reported_as_a_password_error(self):
        result = self.ns['_chauthanh_login_result']('<form id="login">Username Password<p>Invalid CSRF token.</p></form>')
        self.assertFalse(result['credentials_error'])
        self.assertFalse(result['verification_error'])

    def test_http_403_is_not_reported_as_wrong_password(self):
        self.session.get.return_value = self.page('Forbidden', 403)
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'no verification challenge') as caught:
            self.ns['_get_chauthanh_session']()
        self.assertNotIsInstance(caught.exception, self.ns['ChauThanhChallengeError'])
        self.session.post.assert_not_called()

    def test_actual_interstitial_before_login_does_not_submit_credentials(self):
        self.session.get.return_value = self.page('<title>Just a moment...</title>', 403)
        trace = []
        with self.assertRaisesRegex(self.ns['ChauThanhChallengeError'], 'login page'):
            self.ns['_get_chauthanh_session'](http_only=True, diagnostic=trace.append)
        self.session.post.assert_not_called()
        self.assertEqual(trace, [{'stage': 'login page', 'method': 'GET', 'host': 'chauthanh.info',
                                  'status': 403, 'challenge': True}])

    def test_plain_post_refusal_is_distinct_from_verification_failure(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page('Forbidden', 403)
        with self.assertRaises(self.ns['ChauThanhLoginError']) as caught:
            self.ns['_get_chauthanh_session'](http_only=True)
        self.assertNotIsInstance(caught.exception, self.ns['ChauThanhChallengeError'])
        self.session.post.assert_called_once()

    def test_http_only_attempt_does_not_adopt_an_existing_browser_session(self):
        previous = Mock()
        self.ns['_chauthanh_cached_session'] = previous
        self.ns['_chauthanh_session_key'] = 'browser'
        browser = Mock()
        self.ns['_chauthanh_browser'] = browser
        self.session.get.side_effect = [self.page(LOGIN_PAGE), self.page(PROFILE)]
        self.session.post.return_value = self.page('', 302, {'Location': '/forum/profile.html'})
        self.assertIs(self.ns['_get_chauthanh_session'](force=True, http_only=True), self.session)
        browser.submit.assert_not_called()
        previous.close.assert_called_once()
        self.session.post.assert_called_once()

    def test_account_button_explicitly_tries_http_login(self):
        self.ns['_get_chauthanh_session'] = Mock()
        self.ns['chauthanh_login_status'] = SimpleNamespace(value='')
        self.ns['_chauthanh_login_clicked']()
        self.ns['_get_chauthanh_session'].assert_called_once_with(force=True, http_only=True)

    def test_login_error_does_not_cache_an_unverified_account(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page(LOGIN_PAGE)
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'not verified'):
            self.ns['_get_chauthanh_session']()
        self.assertIsNone(self.ns['_chauthanh_cached_session'])
        self.session.close.assert_called_once()

    def test_member_permission_is_rejected_as_non_donor(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page(PROFILE.replace('Supporter', 'Member'))
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'not marked as a donor'):
            self.ns['_get_chauthanh_session']()

    def test_offsite_form_action_is_rejected_before_post(self):
        self.session.get.return_value = self.page(LOGIN_PAGE.replace(
            'method="POST"', 'method="POST" action="https://evil.test/login"'))
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'unexpected destination'):
            self.ns['_get_chauthanh_session']()
        self.session.post.assert_not_called()

    def test_post_redirect_never_replays_hash_to_another_host(self):
        self.session.get.return_value = self.page(LOGIN_PAGE)
        self.session.post.return_value = self.page('', 307, {'Location': 'https://evil.test/'})
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'unexpected destination'):
            self.ns['_get_chauthanh_session']()
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.get.call_count, 1)

    def test_changed_hash_handler_or_non_ascii_password_is_not_guessed(self):
        for page, password in ((LOGIN_PAGE.replace('pass+=', 'changed+='), 'test'),
                               (LOGIN_PAGE, 'mật khẩu')):
            self.session.get.return_value = self.page(page)
            self.ns['token_chauthanh_password'].value = password
            with self.assertRaises(self.ns['ChauThanhLoginError']):
                self.ns['_get_chauthanh_session']()
        self.session.post.assert_not_called()

    def test_runtime_browser_cookies_stay_in_memory_and_are_provider_scoped(self):
        future = Future()
        future.set_result(([{'name': 'session', 'value': 'private-cookie', 'domain': '.chauthanh.info'},
                            {'name': 'other', 'value': 'do-not-import', 'domain': 'evil.test'}],
                           'Runtime browser UA', 'https://chauthanh.info/forum/profile.html'))
        self.ns['_chauthanh_browser'] = Mock(submit=Mock(return_value=future))
        self.assertIs(self.ns['_chauthanh_browser_session'](), self.session)
        self.session.cookies.set.assert_called_once_with('session', 'private-cookie',
                                                         domain='.chauthanh.info', path='/', secure=True)
        self.assertEqual(self.ns['_chauthanh_session_key'], 'browser')
        self.assertEqual(self.session.headers['User-Agent'], 'Runtime browser UA')
        self.assertNotIn('private-cookie', str(self.ns['print'].call_args_list))

    def test_donor_permission_refusal_refreshes_login_once(self):
        first, second = Mock(), Mock()
        self.ns['_get_chauthanh_session'] = Mock(side_effect=[first, second])
        error = self.ns['ChauThanhAccessError']({'host': 'eri2.chauthanh.info', 'status': 403, 'challenge': False})
        self.ns['_chauthanh_download_url'] = Mock(side_effect=[error, CDN])
        self.assertEqual(self.ns['_chauthanh_runtime_link'](Mock()), CDN)
        self.assertEqual(self.ns['_get_chauthanh_session'].call_args_list[1].kwargs, {'force': True})

    def test_challenge_does_not_repeat_login(self):
        self.ns['_get_chauthanh_session'] = Mock(return_value=self.session)
        error = self.ns['ChauThanhAccessError']({'host': 'chauthanh.info', 'status': 403, 'challenge': True})
        self.ns['_chauthanh_download_url'] = Mock(side_effect=error)
        with self.assertRaises(self.ns['ChauThanhAccessError']):
            self.ns['_chauthanh_runtime_link'](Mock())
        self.assertEqual(self.ns['_get_chauthanh_session'].call_count, 1)

    def test_missing_credentials_does_not_attempt_anonymous_login(self):
        self.ns['token_chauthanh_password'].value = ''
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'Settings'):
            self.ns['_get_chauthanh_session']()
        self.ns['requests'].Session.assert_not_called()

    def test_authenticated_entry_and_cookie_free_file_check_are_separate(self):
        file_client = Mock(headers={})
        file_client.__enter__ = Mock(return_value=file_client)
        file_client.__exit__ = Mock(return_value=False)
        file_client.get.return_value = response()
        self.session.headers['User-Agent'] = 'Runtime browser UA'
        self.session.get.return_value = response(302, headers={'Location': CDN})
        self.ns['requests'].Session.return_value = file_client
        task = self.ns['resolve_chauthanh'](LINK)[0]
        self.assertEqual(self.ns['_chauthanh_download_url'](task, session=self.session), CDN)
        self.assertEqual(self.session.get.call_args.args[0], LINK)
        self.assertEqual(file_client.get.call_args.args[0], CDN)
        self.assertEqual(file_client.get.call_args.kwargs['headers']['User-Agent'], 'Mozilla/5.0')
        self.assertNotIn('Cookie', file_client.get.call_args.kwargs['headers'])
        self.session.close.assert_not_called()
        file_client.__exit__.assert_called_once()

    def test_user_operated_browser_verifies_profile_with_fresh_navigation(self):
        browser = self.ns['_ChauThanhRuntimeBrowser']()
        self.addCleanup(lambda: browser.pool.shutdown(wait=False))
        browser.page = Mock(url='https://chauthanh.info/forum/profile.html')
        browser.page.content.return_value = PROFILE
        browser.page.evaluate.return_value = 'Browser UA'
        browser.context = Mock()
        browser.context.cookies.return_value = []
        cookies, agent, profile_url = browser._action('verify', {})
        self.assertEqual((cookies, agent, profile_url), ([], 'Browser UA', browser.page.url))
        browser.page.goto.assert_called_once()

    def test_browser_profile_verification_does_not_require_a_landing_page_link(self):
        browser = self.ns['_ChauThanhRuntimeBrowser']()
        self.addCleanup(lambda: browser.pool.shutdown(wait=False))
        browser.page = Mock(url='https://chauthanh.info/forum/')
        browser.page.content.return_value = '<p>Login successful.</p>'
        def navigate(url, **_kwargs):
            browser.page.url = url
            browser.page.content.return_value = PROFILE
        browser.page.goto.side_effect = navigate
        browser.context = Mock()
        browser.context.cookies.return_value = []
        browser.page.evaluate.return_value = 'Browser UA'
        _, _, profile_url = browser._action('verify', {})
        self.assertEqual(profile_url, 'https://chauthanh.info/forum/profile.html')
        browser.page.goto.assert_called_once_with(profile_url, wait_until='domcontentloaded', timeout=60000)

    def test_browser_does_not_adopt_cookies_when_protected_profile_is_unauthorised(self):
        browser = self.ns['_ChauThanhRuntimeBrowser']()
        self.addCleanup(lambda: browser.pool.shutdown(wait=False))
        browser.page = Mock(url='https://chauthanh.info/forum/profile.html')
        browser.page.content.return_value = '<p>You are not authorized to access this page.</p>'
        browser.context = Mock()
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'Complete login and verification'):
            browser._action('verify', {})
        browser.context.cookies.assert_not_called()

    def test_user_browser_queues_text_without_screenshot_for_every_character(self):
        browser = self.ns['_ChauThanhRuntimeBrowser']()
        self.addCleanup(lambda: browser.pool.shutdown(wait=False))
        browser.page = Mock()
        self.assertIsNone(browser._action('key', {'key': 'a'}))
        browser.page.keyboard.insert_text.assert_called_once_with('a')
        browser.page.screenshot.assert_not_called()
        browser._action('key', {'key': 'Enter'})
        browser.page.keyboard.press.assert_called_once_with('Enter')


class ChauThanhPcSessionTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject(with_login=True)
        self.value = 'private-pc-session'
        self.ns['token_chauthanh_session'].value = self.value
        self.ns['token_chauthanh_username'].value = 'example-user'
        self.ns['token_chauthanh_password'].value = 'private-password'
        self.session = Mock(headers={})
        self.session.get.return_value = Mock(status_code=200, headers={}, text=PROFILE)
        self.ns['requests'].Session.return_value = self.session

    def test_pc_session_is_preferred_and_shared_without_password_submission(self):
        session = self.ns['_get_chauthanh_session']()
        self.assertIs(session, self.session)
        self.session.cookies.set.assert_called_once_with(
            'PHPSESSID', self.value, domain='chauthanh.info', path='/', secure=True)
        self.assertEqual(self.session.get.call_args.args[0], 'https://chauthanh.info/forum/profile.html')
        with ThreadPoolExecutor(max_workers=5) as pool:
            self.assertTrue(all(result is session for result in pool.map(
                lambda _: self.ns['_get_chauthanh_session'](), range(15))))
        self.session.get.assert_called_once()
        self.session.post.assert_not_called()
        self.assertNotIn(self.value, str(self.ns['_chauthanh_session_key']))

    def test_expired_and_forced_checks_keep_the_current_cookie_jar(self):
        self.ns['_get_chauthanh_session']()
        self.ns['_chauthanh_session_time'] = -1000
        self.assertIs(self.ns['_get_chauthanh_session'](), self.session)
        self.assertIs(self.ns['_get_chauthanh_session'](force=True), self.session)
        self.session.cookies.set.assert_called_once()
        self.assertEqual(self.session.get.call_count, 3)
        self.session.post.assert_not_called()
        self.session.close.assert_not_called()

    def test_rejected_expired_session_closes_cache_without_password_fallback(self):
        self.ns['_get_chauthanh_session']()
        self.ns['_chauthanh_session_time'] = -1000
        self.session.get.return_value = Mock(status_code=200, headers={}, text='<p>You are not authorized to access this page.</p>')
        with self.assertRaisesRegex(self.ns['ChauThanhLoginError'], 'current PHPSESSID'):
            self.ns['_get_chauthanh_session']()
        self.assertIsNone(self.ns['_chauthanh_cached_session'])
        self.session.close.assert_called_once()
        self.session.post.assert_not_called()

    def test_changed_session_value_replaces_and_closes_previous_session(self):
        self.ns['_get_chauthanh_session']()
        self.ns['token_chauthanh_session'].value = 'new-private-session'
        second = Mock(headers={})
        second.get.return_value = Mock(status_code=200, headers={}, text=PROFILE)
        self.ns['requests'].Session.return_value = second
        self.assertIs(self.ns['_get_chauthanh_session'](), second)
        self.session.close.assert_called_once()
        second.cookies.set.assert_called_once_with(
            'PHPSESSID', 'new-private-session', domain='chauthanh.info', path='/', secure=True)
        second.post.assert_not_called()

    def test_invalid_or_missing_cookie_never_submits_password(self):
        for value in ('PHPSESSID=private-value; other=value', '', 'invalid\r\nvalue'):
            self.ns['token_chauthanh_session'].value = value
            with self.assertRaises(self.ns['ChauThanhLoginError']):
                self.ns['_get_chauthanh_session'](pc_session_only=True)
        self.ns['requests'].Session.assert_not_called()

    def test_pc_session_button_shows_profile_only_acceptance(self):
        self.ns['chauthanh_login_status'] = SimpleNamespace(value='')
        self.ns['_chauthanh_pc_session_clicked']()
        self.assertIn('PC supporter session accepted', self.ns['chauthanh_login_status'].value)
        self.assertIn('File access will be checked', self.ns['chauthanh_login_status'].value)
        self.assertNotIn(self.value, self.ns['chauthanh_login_status'].value)
        self.session.post.assert_not_called()

    def test_donor_refusal_rechecks_pc_session_then_downloads_without_cookies(self):
        self.session.get.side_effect = [
            Mock(status_code=200, headers={}, text=PROFILE),
            response(302, headers={'Location': CDN}),
            Mock(status_code=200, headers={}, text=PROFILE),
            response(302, headers={'Location': CDN}),
        ]
        file_clients = []
        for reply in (response(403, headers={}, prefix=b'Forbidden'), response()):
            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)
            client.get.return_value = reply
            file_clients.append(client)
        self.ns['requests'].Session.side_effect = [self.session, *file_clients]
        task = self.ns['resolve_chauthanh'](LINK)[0]
        self.assertEqual(self.ns['_chauthanh_runtime_link'](task), CDN)
        self.assertEqual(task.chauthanh_size, 1000)
        self.session.cookies.set.assert_called_once()
        self.session.post.assert_not_called()
        self.assertEqual(self.session.get.call_count, 4)
        for client in file_clients:
            self.assertNotIn('Cookie', client.get.call_args.kwargs['headers'])

    def test_advanced_http_login_clears_pc_selection_after_success(self):
        self.ns['_get_chauthanh_session'] = Mock()
        self.ns['chauthanh_login_status'] = SimpleNamespace(value='')
        self.ns['_chauthanh_login_clicked']()
        self.assertEqual(self.ns['token_chauthanh_session'].value, '')
        self.ns['_get_chauthanh_session'].assert_called_once_with(force=True, http_only=True)

    def test_preview_redacts_pc_input_even_after_cookie_rotation(self):
        self.session.cookies.values.return_value = ['new-server-cookie']
        preview = self.ns['_chauthanh_safe_preview'](
            '<div id="content-full">Error ' + self.value + ' new-server-cookie</div>', session=self.session)
        self.assertNotIn(self.value, preview)
        self.assertNotIn('new-server-cookie', preview)


class ChauThanhBrowserStartupTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_subject(with_login=True)
        self.ns['chauthanh_login_status'] = SimpleNamespace(value='')
        self.ns['chauthanh_browser_box'] = SimpleNamespace(children=[])
        self.ns['_chauthanh_enable_widgets'] = Mock()
        self.ns['display'] = Mock()

    def test_click_acknowledges_start_before_constructing_custom_widget(self):
        panel = object()
        def create_panel():
            self.assertIn('Preparing browser login', self.ns['chauthanh_login_status'].value)
            self.ns['_chauthanh_browser'] = SimpleNamespace(state='starting', status='Installing Chromium')
            return panel
        self.ns['_chauthanh_browser_widget'] = Mock(side_effect=create_panel)
        self.assertIs(self.ns['_chauthanh_open_browser'](Mock()), panel)
        self.assertEqual(self.ns['chauthanh_browser_box'].children, [panel])
        self.ns['_chauthanh_enable_widgets'].assert_called_once()

    def test_widget_construction_error_is_visible_and_does_not_disable_retry(self):
        self.ns['_chauthanh_browser_widget'] = Mock(side_effect=ModuleNotFoundError('internal detail'))
        self.assertIsNone(self.ns['_chauthanh_open_browser'](Mock()))
        self.assertIn('Could not open browser login', self.ns['chauthanh_login_status'].value)
        self.assertIn('ModuleNotFoundError', self.ns['chauthanh_login_status'].value)
        self.assertIsNone(self.ns['_chauthanh_browser'])
        panel = object()
        self.ns['_chauthanh_browser_widget'] = Mock(return_value=panel)
        self.assertIs(self.ns['_chauthanh_open_browser'](Mock()), panel)

    def test_failed_or_legacy_browser_does_not_make_button_silently_return(self):
        for previous in (SimpleNamespace(state='failed', submit=Mock()), SimpleNamespace(submit=Mock())):
            self.ns['_chauthanh_browser'] = previous
            panel = object()
            self.ns['_chauthanh_browser_widget'] = Mock(return_value=panel)
            self.assertIs(self.ns['_chauthanh_open_browser'](Mock()), panel)
            previous.submit.assert_called_once_with('close')

    def test_ready_browser_is_reused_and_explicitly_reports_its_state(self):
        panel = object()
        self.ns['_chauthanh_browser'] = SimpleNamespace(state='ready', status='Browser ready')
        self.ns['_chauthanh_browser_panel'] = panel
        self.ns['_chauthanh_browser_widget'] = Mock()
        self.assertIs(self.ns['_chauthanh_open_browser'](Mock()), panel)
        self.assertIn('Browser ready', self.ns['chauthanh_login_status'].value)
        self.ns['_chauthanh_browser_widget'].assert_not_called()

    def test_direct_cell_displays_a_new_widget_view_for_colab_metadata(self):
        panel = object()
        self.ns['_chauthanh_browser_widget'] = Mock(return_value=panel)
        self.assertIs(self.ns['_chauthanh_open_browser'](), panel)
        self.ns['display'].assert_called_once_with(panel)

    def test_partial_widget_failure_closes_browser_and_clears_poisoned_global(self):
        partial = SimpleNamespace(submit=Mock())
        def fail():
            self.ns['_chauthanh_browser'] = partial
            raise RuntimeError('initialisation failed')
        self.ns['_chauthanh_browser_widget'] = fail
        self.assertIsNone(self.ns['_chauthanh_open_browser'](Mock()))
        partial.submit.assert_called_once_with('close')
        self.assertIsNone(self.ns['_chauthanh_browser'])
        self.assertIsNone(self.ns['_chauthanh_browser_panel'])

    def test_background_start_failure_reports_stage_and_cleans_resources(self):
        messages = []
        browser = self.ns['_ChauThanhRuntimeBrowser'](on_status=messages.append)
        self.addCleanup(lambda: browser.pool.shutdown(wait=False))
        def action(name, payload):
            if name == 'start':
                browser.report('Installing Chromium')
                raise RuntimeError('failed')
        browser._action = Mock(side_effect=action)
        with self.assertRaises(RuntimeError):
            browser.submit('start').result(timeout=3)
        self.assertEqual(browser.state, 'failed')
        self.assertIn('Installing Chromium', browser.status)
        self.assertIn('retry', browser.status)
        self.assertTrue(any('startup failed' in message for message in messages))
        self.assertEqual(browser._action.call_args_list[-1].args, ('close', {}))

    def test_status_observer_failure_does_not_skip_cleanup(self):
        browser = self.ns['_ChauThanhRuntimeBrowser'](on_status=Mock(side_effect=RuntimeError()))
        self.addCleanup(lambda: browser.pool.shutdown(wait=False))
        browser.report('Starting display', 'starting')
        self.assertEqual(browser.state, 'starting')

    def test_real_button_and_widget_report_progress_and_failed_start(self):
        try:
            import anywidget
            import ipywidgets as widgets
        except ImportError:
            self.skipTest('Requires the notebook widget packages; also run with the project venv.')
        self.ns['widgets'] = widgets
        self.ns['chauthanh_login_status'] = widgets.HTML('')
        self.ns['chauthanh_browser_box'] = widgets.VBox([])
        class FakeBrowser:
            def __init__(self, on_status):
                self.on_status = on_status
                self.state, self.status = 'new', ''
                self.start = Future()
            def submit(self, action, payload=None):
                if action == 'start':
                    self.state, self.status = 'starting', 'Installing Chromium'
                    self.on_status(self.status)
                    return self.start
                future = Future()
                future.set_result(None)
                return future
        self.ns['_ChauThanhRuntimeBrowser'] = FakeBrowser
        button = widgets.Button(description='Open browser login')
        button.on_click(self.ns['_chauthanh_open_browser'])
        button.click()
        browser = self.ns['_chauthanh_browser']
        self.assertIsNotNone(browser)
        self.assertIn('Installing Chromium', self.ns['chauthanh_login_status'].value)
        panel = self.ns['chauthanh_browser_box'].children[0]
        self.assertIn('Installing Chromium', panel.children[0].value)
        browser.state, browser.status = 'failed', 'Browser startup failed during installation. Click to retry.'
        browser.start.set_exception(RuntimeError())
        self.assertIn('startup failed', self.ns['chauthanh_login_status'].value)
        button.click()
        self.assertIsNot(self.ns['_chauthanh_browser'], browser)


if __name__ == '__main__':
    unittest.main()
