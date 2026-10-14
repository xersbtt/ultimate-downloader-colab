"""Exercise live additions against the real batch coordinator and parallel loop."""

import ast
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any, Dict
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import List, Optional, Tuple
from unittest import TestCase, main
from unittest.mock import Mock
from uuid import uuid4
import queue
import os
import re
import sys
import hashlib
import shutil
import tempfile
import threading
import time
from urllib.parse import unquote, urlparse
from unittest.mock import patch


SOURCE = Path(__file__).with_name('ultimate_downloader.py')
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def load_real(*names):
    wanted = set(names)
    nodes = [node for node in TREE.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted]
    assert {node.name for node in nodes} == wanted
    scope = dict(Lock=Lock, List=List, Optional=Optional, Tuple=Tuple, Dict=Dict, Any=Any,
                 uuid4=uuid4,
                 DownloadTask=SimpleNamespace,
                 LiveBatchManager=object,
                 _sync_live_controls=Mock(),
                 SEQUENTIAL_LINK_TYPES={'youtube', 'mega', 'magnet', 'magnet_file', 'tb_magnet_file'})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope


def task(name, kind='direct'):
    return SimpleNamespace(id=name, url=f'https://example.test/{name}', filename=name,
                           link_type=kind, status='pending', error=None)


class LiveManagerTests(TestCase):
    def setUp(self):
        self.manager = load_real('LiveBatchManager')['LiveBatchManager']([task('first')], 'video')

    def test_addition_during_parallel_pool_joins_next_free_slot(self):
        view = [self.manager.all_tasks[0]]
        self.manager.begin_parallel(view)
        extra = task('extra')
        self.assertTrue(self.manager.add([extra]))
        self.assertEqual(self.manager.take_parallel(), [extra])
        self.assertEqual(view, [self.manager.all_tasks[0], extra])
        self.assertTrue(self.manager.close_parallel_if_empty())
        self.assertIsNone(self.manager.next_wave_or_finish())
        self.assertFalse(self.manager.is_running())

    def test_sequential_addition_forms_another_wave(self):
        self.manager.begin_parallel([self.manager.all_tasks[0]])
        video = task('video', 'youtube')
        self.assertTrue(self.manager.add([video]))
        self.assertEqual(self.manager.take_parallel(), [])
        self.assertTrue(self.manager.close_parallel_if_empty())
        self.assertEqual(self.manager.next_wave_or_finish(), [video])
        self.assertIsNone(self.manager.next_wave_or_finish())

    def test_completion_and_addition_have_one_atomic_winner(self):
        self.manager.close_parallel_if_empty()
        gate = Event()
        outcomes = []

        def add():
            gate.wait()
            outcomes.append(self.manager.add([task('late')]))

        thread = Thread(target=add)
        thread.start()
        gate.set()
        wave = self.manager.next_wave_or_finish()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        if outcomes[0]:
            self.assertIn(self.manager.all_tasks[-1].id, ('late',))
            self.assertEqual(wave, [self.manager.all_tasks[-1]])
        else:
            self.assertIsNone(wave)
            self.assertEqual(len(self.manager.all_tasks), 1)


class ReviewedQueueStartTests(TestCase):
    def test_start_uses_live_batch_without_a_toggle(self):
        scope = load_real('start_from_queue')
        selected = task('first')
        hide_queue = Mock()
        launch = Mock()
        scope.update(queue_list=SimpleNamespace(value=('1. first',)),
                     pending_queue=[selected], _live_batch=None,
                     _running_live_batch=lambda: None,
                     hide_queue=hide_queue, _launch_live_batch=launch,
                     execute_selected_tasks=Mock(side_effect=AssertionError('synchronous batch started')),
                     print=Mock())

        scope['start_from_queue'](mode='video')

        hide_queue.assert_called_once_with()
        launch.assert_called_once_with([selected], 'video')


class LiveMetadataTests(TestCase):
    def test_new_preview_merges_naming_data_without_erasing_active_matches(self):
        scope = load_real('analyze_batch_episodes', 'analyze_batch_metadata')
        scope.update(re=re, os=os, _strip_size_suffix=lambda name: name,
                     _batch_episode_cache={'Active - 01.mkv': 41},
                     _tmdb_match_cache={'Active.mkv': {'id': 1}},
                     _match_cache_key=lambda name: name,
                     tmdb_is_enabled=lambda: True,
                     detect_episode_info=lambda _name: {'episode_detected': False},
                     clean_show_name=lambda name: name,
                     _tmdb_search=lambda *_args: {'id': 2},
                     _save_tmdb_query_cache=Mock(), print=Mock())

        scope['analyze_batch_episodes'](['New-01.mkv', 'New-02.mkv'], merge=True)
        scope['analyze_batch_metadata'](['New.mkv'], merge=True)

        self.assertEqual(scope['_batch_episode_cache']['Active - 01.mkv'], 41)
        self.assertEqual(scope['_batch_episode_cache']['New-02.mkv'], 2)
        self.assertEqual(scope['_tmdb_match_cache']['Active.mkv']['id'], 1)
        self.assertEqual(scope['_tmdb_match_cache']['New.mkv']['id'], 2)


class LivePipelineTests(TestCase):
    def test_new_parallel_file_runs_without_waiting_for_first_batch_to_end(self):
        scope = load_real('LiveBatchManager', '_run_download_pipeline')
        first, added, youtube = task('first'), task('added'), task('youtube', 'youtube')
        manager = scope['LiveBatchManager']([first, youtube], 'video')
        started, release = Event(), Event()
        order = []
        snapshots = []

        def worker(item, *_args):
            order.append(item.id)
            if item is first:
                started.set()
                self.assertTrue(release.wait(5))
            item.status = 'done'
            return item

        scope.update(
            queue=queue, threading=threading, time=time, deque=deque,
            ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
            wait=wait, FIRST_COMPLETED=FIRST_COMPLETED,
            start_keep_alive=Mock(), download_stats={}, _clear_per_task_bars=Mock(),
            _reset_cancel_state=Mock(), stop_hint=SimpleNamespace(value='', layout=SimpleNamespace(display='none')),
            playlist_selection=SimpleNamespace(value=''), subtitle_langs=SimpleNamespace(value=('en',)),
            save_session=lambda tasks, **_kw: snapshots.append([t.id for t in tasks]),
            token_tb=SimpleNamespace(value=''), async_moves_checkbox=SimpleNamespace(value=False),
            _reset_drive_xfer_stats=Mock(), progress_monitor=Mock(),
            download_worker=worker, _cancel_requested=False, update_progress_display=Mock(),
            _drive_xfer_summary=lambda: None, print=Mock(),
            process_youtube_link=lambda *_args, **_kw: (order.append('youtube') or 1, 0, 1),
            yt_success_cumulative=0, yt_fail_cumulative=0, stop_monitor=False,
            batch_start_time=None,
        )
        result = {}

        def run():
            try:
                result['counts'] = scope['_run_download_pipeline'](
                    all_tasks=manager.all_tasks, parallel_tasks=[first],
                    youtube_urls=[youtube.url], mega_urls=[], debrid_urls=[], mode='video',
                    gofile_token='', rd_key='', max_workers=1, live_manager=manager)
            except BaseException as exc:
                result['error'] = exc

        thread = Thread(target=run)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            self.assertTrue(manager.add([added]))
        finally:
            release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', result)
        self.assertEqual(order, ['first', 'added', 'youtube'])
        self.assertEqual(result['counts'], (3, 0))
        self.assertIn(['first', 'youtube', 'added'], snapshots)

    def test_stop_leaves_waiting_file_pending_for_resume(self):
        scope = load_real('LiveBatchManager', '_run_download_pipeline')
        first, waiting = task('first'), task('waiting')
        manager = scope['LiveBatchManager']([first, waiting], 'video')
        started, release = Event(), Event()
        snapshots = []

        def worker(item, *_args):
            started.set()
            self.assertTrue(release.wait(5))
            item.status = 'failed' if scope['_cancel_requested'] else 'done'
            return item

        scope.update(
            queue=queue, threading=threading, time=time, deque=deque,
            ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
            wait=wait, FIRST_COMPLETED=FIRST_COMPLETED,
            start_keep_alive=Mock(), download_stats={}, _clear_per_task_bars=Mock(),
            _reset_cancel_state=Mock(), stop_hint=SimpleNamespace(value='', layout=SimpleNamespace(display='none')),
            playlist_selection=SimpleNamespace(value=''), subtitle_langs=SimpleNamespace(value=('en',)),
            save_session=lambda tasks, **_kw: snapshots.append([(t.id, t.status) for t in tasks]),
            token_tb=SimpleNamespace(value=''), async_moves_checkbox=SimpleNamespace(value=False),
            _reset_drive_xfer_stats=Mock(), progress_monitor=Mock(), download_worker=worker,
            _cancel_requested=False, update_progress_display=Mock(),
            _drive_xfer_summary=lambda: None, print=Mock(),
            yt_success_cumulative=0, yt_fail_cumulative=0, stop_monitor=False,
            batch_start_time=None, _live_batch=manager,
        )
        result = {}

        def run():
            try:
                result['counts'] = scope['_run_download_pipeline'](
                    all_tasks=manager.all_tasks, parallel_tasks=[first, waiting],
                    youtube_urls=[], mega_urls=[], debrid_urls=[], mode='video',
                    gofile_token='', rd_key='', max_workers=1, live_manager=manager)
            except BaseException as exc:
                result['error'] = exc

        thread = Thread(target=run)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            scope['_cancel_requested'] = True
        finally:
            release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', result)
        self.assertEqual(first.status, 'failed')
        self.assertEqual(waiting.status, 'pending')
        self.assertEqual(result['counts'], (0, 1))
        self.assertIn([('first', 'failed'), ('waiting', 'pending')], snapshots)


class YoutubeNamingTests(TestCase):
    """Run the real YouTube processor and routing against local fake downloads."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.content.mkdir()
        self.scope = load_real(
            'LiveBatchManager', '_run_live_waves', '_run_download_pipeline',
            'resolve_youtube_playlist', 'process_youtube_link', 'normalize_playlist_range', 'ytdl_hook',
            '_configure_okru_extractor', 'url_matches_host', '_url_host',
            'sanitize_filename', 'clean_show_name', '_strip_size_suffix',
            '_split_subtitle_lang', '_normalize_sub_lang', '_match_cache_key',
            'analyze_batch_episodes', 'get_batch_episode', '_multi_ep_end', 'detect_episode_info',
            '_apply_queue_overrides', 'get_name_override', 'get_season_override',
            'get_episode_override', 'get_episode_end_override', 'get_part_override',
            'get_route_override', 'get_tmdb_match', 'resolve_library_is_anime',
            'determine_destination_path', 'check_duplicate_in_drive', 'handle_file_processing',
            '_queue_dest_preview')
        self.scope.update(
            os=os, re=re, unquote=unquote, urlparse=urlparse, hashlib=hashlib,
            queue=queue, threading=threading, time=time, deque=deque,
            ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
            wait=wait, FIRST_COMPLETED=FIRST_COMPLETED,
            COLAB_ROOT=str(self.content) + os.sep, DRIVE_BASE=str(self.root / 'drive') + os.sep,
            UD_CONFIG_PATH=str(self.root / 'config') + os.sep, COOKIE_PATH=str(self.root / 'cookies'),
            _wait_for_disk_space=Mock(return_value=True), DISK_START_GB=10,
            progress_lock=Lock(), progress_bar=SimpleNamespace(value=0, description=''),
            playlist_selection=SimpleNamespace(value=''), subtitle_langs=SimpleNamespace(value=('en',)),
            _cancel_requested=False, cancel_requested=lambda: False,
            is_auto_organize_enabled=lambda: True, tmdb_is_enabled=lambda: True,
            _batch_episode_cache={}, _tmdb_match_cache={},
            _season_override_cache={}, _episode_override_cache={}, _episode_end_override_cache={},
            _name_override_cache={}, _route_override_cache={}, _part_override_cache={},
            get_tv_path=lambda: 'TV Shows', get_anime_series_path=lambda: 'Anime Series',
            get_movie_path=lambda: 'Movies', get_anime_movies_path=lambda: 'Anime Movies',
            get_downloads_path=lambda: 'Downloads', get_youtube_path=lambda: 'YouTube',
            episode_numbering_toggle=SimpleNamespace(value='Absolute'),
            _ensure_dest_dir=lambda path: Path(path).mkdir(parents=True, exist_ok=True),
            _drive_path_exists=os.path.exists, move_with_progress=Mock(side_effect=shutil.move),
            log_download=Mock(), auto_extract_subs_checkbox=SimpleNamespace(value=False),
            SUB_EXTRACT_VIDEO_EXTS={'.mkv', '.mp4'}, print=Mock(),
            start_keep_alive=Mock(), download_stats={}, _clear_per_task_bars=Mock(),
            _reset_cancel_state=Mock(), stop_hint=SimpleNamespace(value='', layout=SimpleNamespace(display='none')),
            save_session=Mock(), token_rd=SimpleNamespace(value=''), token_tb=SimpleNamespace(value=''),
            async_moves_checkbox=SimpleNamespace(value=False), _reset_drive_xfer_stats=Mock(),
            progress_monitor=Mock(), update_progress_display=Mock(), _drive_xfer_summary=lambda: None,
            yt_success_cumulative=0, yt_fail_cumulative=0, stop_monitor=False, batch_start_time=None)
        constants = [node for node in TREE.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name)
                             and target.id in {'KEEP_EXTENSIONS', '_MULTI_EP_TAIL', '_SUB_LANG_MAP'}
                             for target in node.targets)]
        exec(compile(ast.Module(body=constants, type_ignores=[]), str(SOURCE), 'exec'), self.scope)
        self.info = dict(title='Downloaded title', webpage_url='https://youtube.test/video',
                         id='video', formats=[{}])
        self.downloaded = []
        self.options = []
        self.output_ext = 'mkv'
        self.write_outputs = True
        self.hook_events = []
        self.postprocessor_events = []
        self.progress_probe = lambda: None
        self.okru_metadata = None
        owner = self

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                owner.options.append(options)
                self.okru_extractor = SimpleNamespace(_parse_json=lambda value, *_a, **_kw: __import__('json').loads(value))

            def get_info_extractor(self, name):
                assert name == 'Odnoklassniki'
                return self.okru_extractor

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                if owner.okru_metadata is not None:
                    return self.okru_extractor._parse_json(owner.okru_metadata, 'video')
                if self.options.get('progress_hooks'):
                    owner.progress_probe()
                return owner.info

            def download(self, urls):
                if owner.okru_metadata is not None:
                    self.extract_info(urls[0])
                owner.downloaded.extend(urls)
                for event in owner.hook_events:
                    for hook in self.options.get('progress_hooks', []):
                        hook(event)
                    owner.progress_probe()
                if not owner.write_outputs:
                    return
                template = self.options['outtmpl']['default']
                base = template.replace('%(title)s', owner.info['title'])
                if not self.options.get('skip_download'):
                    Path(base.replace('%(ext)s', owner.output_ext)).write_bytes(b'video')
                Path(base.replace('%(ext)s', 'en.srt')).write_text('subtitle', encoding='utf-8')
                for event in owner.postprocessor_events:
                    for hook in self.options.get('postprocessor_hooks', []):
                        hook(event)
                    owner.progress_probe()

        self.modules = patch.dict(sys.modules, {'yt_dlp': SimpleNamespace(YoutubeDL=FakeYoutubeDL)})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def make_task(self, filename='Queue title.mkv', **overrides):
        return SimpleNamespace(id=filename, filename=filename, url=self.info['webpage_url'],
                               source='youtube', link_type='youtube', status='pending', error=None,
                               **overrides)

    def run_video(self, item, mode='video'):
        self.scope['_apply_queue_overrides']([item], merge=True)
        return self.scope['process_youtube_link'](item.url, mode, task=item)

    def library_files(self):
        return sorted(str(path.relative_to(self.root / 'drive')).replace('\\', '/')
                      for path in (self.root / 'drive').rglob('*') if path.is_file())

    def test_live_added_video_keeps_all_edits_and_paired_subtitles(self):
        active = self.make_task('Downloaded title.mkv', name_override='Active Show', episode_override=1)
        active.url, active.status, active.link_type = 'https://example.test/active', 'done', 'direct'
        self.scope['_apply_queue_overrides']([active])
        item = self.make_task(name_override='Correct Show', year_override='2025', season_override=2,
                              episode_override=7, episode_end_override=9, part_override=2,
                              route_override='anime_series')
        self.scope['_apply_queue_overrides']([item], merge=True)
        preview = self.scope['_queue_dest_preview'](item)
        manager = self.scope['LiveBatchManager']([active], 'video')
        manager.close_parallel_if_empty()
        self.assertTrue(manager.add([item]))

        counts = self.scope['_run_live_waves'](manager, 'video', '', '', '', 1, (1, 0))

        base = 'Anime Series/Correct Show (2025)/Season 02/Correct Show - S02E07-E09-pt2'
        self.assertEqual(preview, base + '.mkv')
        self.assertEqual(self.library_files(), [base + '.en.srt', base + '.mkv'])
        self.assertEqual((counts, item.status), ((2, 0), 'done'))
        self.scope['progress_monitor'].assert_called_once_with(manager.all_tasks)
        self.assertTrue(self.scope['stop_monitor'])
        self.assertEqual(self.scope['get_name_override'](active.filename), ('Active Show', ''))
        self.assertNotIn('Downloaded title.en.srt', self.scope['_name_override_cache'])

    def test_legacy_truncated_playlist_row_and_actual_container_keep_edits(self):
        self.output_ext = 'mp4'
        item = self.make_task('[5/20] A long title from an older saved playlist...',
                              name_override='Saved Show', season_override=3,
                              episode_override=12, part_override=0)

        self.assertEqual(self.run_video(item), (1, 0, 1))

        base = 'TV Shows/Saved Show/Season 03/Saved Show - S03E12'
        self.assertEqual(self.library_files(), [base + '.en.srt', base + '.mp4'])

    def test_duplicate_check_uses_renamed_destination_before_downloading(self):
        item = self.make_task(name_override='Correct Show', episode_override=7, season_override=2)
        dest = self.root / 'drive/TV Shows/Correct Show/Season 02/Correct Show - S02E07.mkv'
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b'existing video')

        self.assertEqual(self.run_video(item), (0, 0, 1))

        self.assertEqual(self.downloaded, [])
        self.assertEqual(dest.read_bytes(), b'existing video')

    def test_cached_tmdb_match_applies_to_video_and_subtitle(self):
        item = self.make_task()
        self.scope['_tmdb_match_cache'][item.filename] = dict(
            type='movie', name='Matched Movie', year='2020', is_anime=False, library_class='standard')

        self.assertEqual(self.run_video(item), (1, 0, 1))

        base = 'Movies/Matched Movie (2020)/Matched Movie'
        self.assertEqual(self.library_files(), [base + '.en.srt', base + '.mkv'])

    def test_legacy_or_unresolved_labels_use_actual_filename_for_episode_detection(self):
        self.info['title'] = 'Actual Show.S02E03'
        for label in ('[7/20] Old truncated preview...', 'https://youtube.test/video...'):
            with self.subTest(label=label):
                item = self.make_task(label)
                self.assertEqual(self.run_video(item), (1, 0, 1))
                base = 'TV Shows/Actual Show/Season 02/Actual Show - S02E03'
                self.assertEqual(self.library_files(), [base + '.en.srt', base + '.mkv'])
                for path in (self.root / 'drive').rglob('*'):
                    if path.is_file():
                        path.unlink()

    def test_subtitles_only_keeps_video_naming_settings(self):
        item = self.make_task(name_override='Correct Show', episode_override=7)

        self.assertEqual(self.run_video(item, mode='subtitles'), (1, 0, 1))

        self.assertEqual(self.library_files(), ['TV Shows/Correct Show/Season 01/Correct Show - S01E07.en.srt'])

    def test_downloads_route_and_auto_organise_off_keep_actual_filenames(self):
        for auto_organise in (True, False):
            with self.subTest(auto_organise=auto_organise):
                self.scope['is_auto_organize_enabled'] = lambda: auto_organise
                item = self.make_task(name_override='Unused Name', episode_override=7,
                                      route_override='downloads' if auto_organise else None)
                self.assertEqual(self.run_video(item), (1, 0, 1))
                self.assertEqual(self.library_files(), ['Downloads/Downloaded title.en.srt',
                                                       'Downloads/Downloaded title.mkv'])
                for path in (self.root / 'drive').rglob('*'):
                    if path.is_file():
                        path.unlink()

    def test_only_owned_files_are_processed_and_completed_retry_files_are_reused(self):
        unrelated = self.content / 'Other Show.en.srt'
        unrelated.write_text('other subtitle', encoding='utf-8')
        item = self.make_task(name_override='Correct Show', episode_override=7)
        download_dir = self.content / ('youtube_' + hashlib.sha256(item.url.encode()).hexdigest()[:16])
        download_dir.mkdir()
        (download_dir / 'Downloaded title.mkv').write_bytes(b'complete prior attempt')
        (download_dir / 'Downloaded title.en.srt.part').write_bytes(b'partial subtitle')
        self.write_outputs = False

        self.assertEqual(self.run_video(item), (1, 0, 1))

        self.assertEqual(self.library_files(), ['TV Shows/Correct Show/Season 01/Correct Show - S01E07.mkv'])
        self.assertTrue(unrelated.exists())
        self.assertTrue((download_dir / 'Downloaded title.en.srt.part').exists())

    def test_okru_download_accepts_decoded_metadata_and_keeps_queue_edits(self):
        self.info['webpage_url'] = 'https://ok.ru/video/123456'
        self.okru_metadata = self.info
        item = self.make_task(name_override='Correct Show', episode_override=7)
        self.assertEqual(self.run_video(item), (1, 0, 1))
        self.assertEqual(self.downloaded, [item.url])
        self.assertIn('TV Shows/Correct Show/Season 01/Correct Show - S01E07.mkv',
                      self.library_files())

    def test_resolution_preserves_full_titles_and_playlist_episode_identity(self):
        prefix = 'A very long show title ' + 'with extra words ' * 4
        titles = [prefix + f'[{episode:02d}]' for episode in (3, 4)]
        self.info = dict(_type='playlist', title='Playlist', entries=[
            dict(title=title, url=f'https://youtube.test/{episode}')
            for episode, title in enumerate(titles)])

        items = self.scope['resolve_youtube_playlist']('https://youtube.test/playlist')

        self.assertEqual([item.filename for item in items], [title + '.mkv' for title in titles])
        results = self.scope['analyze_batch_episodes']([item.filename for item in items])
        self.assertEqual(list(results.values()), [3, 4])
        self.info = dict(title=titles[0] + ': finale', webpage_url='https://youtube.test/single')
        single = self.scope['resolve_youtube_playlist'](self.info['webpage_url'])[0]
        self.assertEqual(single.filename, self.scope['sanitize_filename'](self.info['title'] + '.mkv'))

    def test_real_processor_reports_preparation_download_processing_and_drive_move(self):
        item = self.make_task(name_override='Correct Show', episode_override=7)
        phases = []
        self.hook_events = [dict(status='downloading', downloaded_bytes=40, total_bytes=100,
                                 speed=3 * 1024 * 1024), dict(status='finished')]
        self.postprocessor_events = [dict(status='started', postprocessor='Merger')]
        self.progress_probe = lambda: phases.append((item.status, dict(self.scope['download_stats'][item.id])))

        def move(source, destination):
            phases.append((item.status, dict(self.scope['download_stats'][item.id])))
            return shutil.move(source, destination)

        self.scope['move_with_progress'].side_effect = move
        self.assertEqual(self.run_video(item), (1, 0, 1))

        self.assertEqual(phases[0], ('downloading', dict(pct=0.0, speed_mbs=0.0, stage='Preparing video...')))
        self.assertEqual(phases[1], ('downloading', dict(pct=40.0, speed_mbs=3.0)))
        self.assertEqual(phases[2], ('downloading', dict(pct=100.0, speed_mbs=0.0, stage='Processing video...')))
        self.assertEqual(phases[3], phases[2])
        self.assertEqual([status for status, _stats in phases[4:]], ['moving', 'moving'])
        self.assertTrue(all(stats['pct'] == 100 and stats['speed_mbs'] == 0 for _status, stats in phases[4:]))

    def test_cancelled_youtube_transfer_stops_monitor_and_leaves_next_video_pending(self):
        first, waiting = self.make_task('first.mkv'), self.make_task('waiting.mkv')
        waiting.url += '/next'
        manager = self.scope['LiveBatchManager']([first, waiting], 'video')

        def download(*_args, **kwargs):
            self.assertEqual(kwargs['task'].status, 'downloading')
            self.scope['_cancel_requested'] = True
            return (0, 1, 1)

        self.scope['process_youtube_link'] = Mock(side_effect=download)
        self.scope['_run_download_pipeline'](
            all_tasks=manager.all_tasks, parallel_tasks=[], youtube_urls=[first.url, waiting.url],
            mega_urls=[], debrid_urls=[], mode='video', gofile_token='', rd_key='',
            max_workers=1, live_manager=manager)

        self.assertEqual((first.status, first.error), ('failed', 'Cancelled by user'))
        self.assertEqual(waiting.status, 'pending')
        self.assertTrue(self.scope['stop_monitor'])
        self.scope['process_youtube_link'].assert_called_once()


class YoutubeProgressTests(TestCase):
    def setUp(self):
        self.scope = load_real('ytdl_hook', '_ProgressRow', 'update_progress_display',
                               '_colab_live_status_snapshot', 'LiveBatchManager')
        self.previous, self.item = task('previous'), task('youtube', 'youtube')
        self.previous.status, self.item.status = 'done', 'downloading'
        self.item.filename = 'YouTube video.mkv'
        self.manager = self.scope['LiveBatchManager']([self.previous, self.item], 'video')
        self.panel = SimpleNamespace(set_bars=Mock(), set_title=Mock(), layout=SimpleNamespace(display='none'))
        self.scope.update(
            re=re, html=__import__('html'), time=SimpleNamespace(time=lambda: 123),
            progress_lock=Lock(), download_stats={}, last_display_speed=0, batch_start_time=None,
            _per_task_bars={}, _per_task_done_at={}, _PER_TASK_LINGER=2,
            _per_task_box=SimpleNamespace(children=[]), _per_task_accordion=self.panel,
            _colab_live_status_active=False, _disk_free_gb=lambda: float('inf'),
            progress_bar=SimpleNamespace(value=12, description='Idle', layout=SimpleNamespace(display='block')),
            status_label=SimpleNamespace(value='', layout=SimpleNamespace(display='block')),
            live_log=SimpleNamespace(layout=SimpleNamespace(display='block')),
            _live_batch=self.manager, _last_live_status=None, _live_status_tasks=self.manager.all_tasks,
            _live_log_lock=Lock(), _live_log_messages=deque(), JSON=lambda data: SimpleNamespace(data=data))

    def hook(self, event, **kwargs):
        self.scope['ytdl_hook'](event, task=self.item, **kwargs)

    def poll(self):
        return self.scope['_colab_live_status_snapshot'](self.manager.token).data

    def test_numeric_hook_reaches_standard_panel_and_colab_live_row(self):
        self.hook(dict(status='downloading', downloaded_bytes=400, total_bytes=1000,
                       speed=2 * 1024 * 1024))
        self.assertEqual(self.scope['progress_bar'].value, 12)  # hook preserves batch-wide progress

        self.scope['update_progress_display'](self.manager.all_tasks)

        bar = self.scope['_per_task_bars'][self.item.id]
        self.assertEqual((bar.value, bar.bar_style), (40, 'warning'))
        self.assertIn('40% (2.0 MB/s)', bar.description)
        self.assertEqual(self.panel.layout.display, 'block')
        self.assertEqual(self.scope['progress_bar'].value, 70)
        response = self.poll()
        self.assertEqual(response['progress'], 70)
        self.assertEqual(response['bars'][0]['value'], 40)
        self.assertIn('2.0 MB/s', response['bars'][0]['description'])

    def test_estimated_sizes_formatted_percentages_and_unknown_speed(self):
        self.hook(dict(status='downloading', downloaded_bytes=80, total_bytes=None,
                       total_bytes_estimate=200, speed=None))
        self.assertEqual(self.scope['download_stats'][self.item.id], dict(pct=40.0, speed_mbs=0.0))
        self.hook(dict(status='downloading', _percent_str='\x1b[0;94m 65.5%\x1b[0m', speed=None))
        self.assertEqual(self.scope['download_stats'][self.item.id]['pct'], 65.5)
        self.hook(dict(status='downloading', _percent_str='N/A%', speed=None))
        self.assertEqual(self.scope['download_stats'][self.item.id]['pct'], 65.5)

    def test_processing_stays_active_until_drive_move_and_next_format_updates(self):
        self.hook(dict(status='finished'))
        self.hook(dict(status='started', postprocessor='Merger'), postprocessing=True)
        self.scope['update_progress_display'](self.manager.all_tasks)

        bar = self.scope['_per_task_bars'][self.item.id]
        self.assertEqual((self.item.status, bar.value, bar.bar_style), ('downloading', 100, 'info'))
        self.assertIn('Processing video...', bar.description)
        self.assertIn('Processing video...', self.poll()['bars'][0]['description'])
        self.hook(dict(status='downloading', downloaded_bytes=10, total_bytes=100, speed=1024 * 1024))
        self.assertNotIn('stage', self.scope['download_stats'][self.item.id])
        self.assertEqual(self.poll()['bars'][0]['value'], 10)
        self.item.status = 'moving'
        self.scope['_per_task_bars'].clear()
        moving = self.poll()['bars'][0]
        self.assertEqual((moving['value'], moving['state']), (100, 'info'))
        self.assertIn('📤', moving['description'])

    def test_standalone_hook_keeps_overall_progress_available(self):
        self.scope['ytdl_hook'](dict(status='downloading', downloaded_bytes=40, total_bytes=100,
                                     speed=1024 * 1024))
        self.assertEqual(self.scope['progress_bar'].value, 40)
        self.assertIn('1.0 MB/s', self.scope['progress_bar'].description)


class LiveRetryTests(TestCase):
    def test_stop_during_retry_countdown_keeps_session(self):
        scope = load_real('_run_auto_retry_chain')
        state = {'pending': True, 'remaining': 2, 'total': 2}
        stopped = {'value': False}
        resume = Mock()
        scope.update(_auto_retry_state=state, cancel_requested=lambda: stopped['value'],
                     time=SimpleNamespace(sleep=lambda _seconds: stopped.__setitem__('value', True)),
                     execute_batch=resume, print=Mock())

        scope['_run_auto_retry_chain']('video')

        resume.assert_not_called()
        self.assertEqual(state['remaining'], 0)

    def test_retry_keeps_the_live_manager_and_consumes_the_existing_budget(self):
        scope = load_real('_run_auto_retry_chain')
        manager = object()
        scope.update(_auto_retry_state={'pending': True, 'remaining': 2, 'total': 2},
                     cancel_requested=lambda: False, time=SimpleNamespace(sleep=Mock()),
                     execute_batch=Mock(), print=Mock())
        scope['_run_auto_retry_chain']('video', live_manager=manager)
        scope['execute_batch'].assert_called_once_with(
            'video', resume=True, auto_retry_chain=True, live_manager=manager)
        self.assertEqual(scope['_auto_retry_state']['remaining'], 1)


class LiveDisplayTests(TestCase):
    def finished_controls_scope(self, *extra_names):
        scope = load_real('LiveBatchManager', '_launch_live_batch',
                          '_colab_live_status_snapshot', '_sync_live_controls', *extra_names)

        def widget(**state):
            browser = dict(state)
            browser_layout = {'display': 'none'}
            layout = SimpleNamespace(display='none')
            layout.send_state = lambda: browser_layout.update(display=layout.display)
            control = SimpleNamespace(layout=layout, browser=browser,
                                      browser_layout=browser_layout, **state)
            control.send_state = lambda: browser.update(
                {key: getattr(control, key) for key in state})
            return control

        for name in ('btn', 'btn_quick', 'btn_stop', 'btn_retry', 'btn_resume',
                     'btn_restart', 'btn_settings', 'btn_history',
                     'auto_organize_checkbox', 'debrid_service_toggle',
                     'concurrent_slider', 'auto_retry_input', 'btn_queue_start',
                     'btn_queue_start_subs'):
            scope[name] = widget(disabled=True, description='Add Links', icon='plus')
        scope['btn_stop'].browser_layout['display'] = 'inline-block'
        scope['btn_quick'].disabled = False
        scope['btn_resume'].disabled = False
        scope['btn_retry'].layout.display = 'inline-block'
        scope['btn_resume'].layout.display = 'inline-flex'
        scope['btn_restart'].layout.display = 'inline-block'
        scope['btn'].disabled = False
        scope.update(_live_batch=None, _live_status_tasks=None,
                     load_dir_settings=Mock(), drive_api_checkbox=SimpleNamespace(value=False),
                     _init_drive_api=Mock(),
                     _last_live_status=None, _colab_live_status_active=False,
                     progress_bar=widget(description='Idle'),
                     status_label=widget(value=''), live_log=widget(value=''),
                     stop_hint=widget(value=''), _per_task_accordion=widget(),
                     _per_task_bars={}, _live_log_lock=Lock(),
                     _live_log_messages=deque(), download_stats={},
                     JSON=lambda data: SimpleNamespace(data=data), re=re,
                     html=__import__('html'), time=SimpleNamespace(time=lambda: 123))
        return scope

    def test_finished_poll_delivers_retry_and_restores_stale_toolbar(self):
        scope = self.finished_controls_scope()
        scope['_last_live_status'] = {'token': 'finished', 'running': False,
                                     'counts': {'done': 8, 'total': 9, 'failed': 1}}
        # Python has finished cleanup, but the browser still shows running controls.
        self.assertEqual(scope['btn_retry'].browser_layout['display'], 'none')
        self.assertTrue(scope['btn_quick'].browser['disabled'])

        response = scope['_colab_live_status_snapshot']('finished').data

        self.assertFalse(response['running'])
        self.assertEqual(response['counts']['failed'], 1)
        self.assertEqual(scope['btn_retry'].browser_layout['display'], 'inline-block')
        self.assertEqual(scope['btn_restart'].browser_layout['display'], 'inline-block')
        self.assertEqual(scope['btn_resume'].browser_layout['display'], 'inline-flex')
        self.assertEqual(scope['btn_stop'].browser_layout['display'], 'none')
        self.assertFalse(scope['btn_quick'].browser['disabled'])
        self.assertFalse(scope['btn_resume'].browser['disabled'])

    def test_successful_finish_hides_retry_and_old_polls_do_not_sync_a_new_batch(self):
        scope = self.finished_controls_scope()
        scope['btn_retry'].layout.display = 'none'
        scope['btn_retry'].browser_layout['display'] = 'inline-block'
        scope['_last_live_status'] = {'token': 'finished', 'running': False}
        scope['_colab_live_status_snapshot']('finished')
        self.assertEqual(scope['btn_retry'].browser_layout['display'], 'none')

        scope['btn_quick'].browser['disabled'] = True
        scope['_live_batch'] = scope['LiveBatchManager']([task('new')], 'video')
        scope['_colab_live_status_snapshot']('finished')
        scope['_colab_live_status_snapshot']('unknown')
        self.assertTrue(scope['btn_quick'].browser['disabled'])

    def test_running_poll_delivers_add_links_after_background_preparation(self):
        scope = self.finished_controls_scope()
        manager = scope['LiveBatchManager']([task('active')], 'video')
        scope['_live_batch'] = manager
        scope['btn'].disabled = True
        scope['btn_stop'].layout.display = 'inline-block'
        scope['btn_quick'].disabled = True
        scope['btn_resume'].disabled = True
        scope['_colab_live_status_snapshot'](manager.token)
        self.assertTrue(scope['btn'].browser['disabled'])

        # Preparation completes in the worker; the browser hasn't received it yet.
        scope['btn'].disabled = False
        self.assertTrue(scope['btn'].browser['disabled'])
        response = scope['_colab_live_status_snapshot'](manager.token).data

        self.assertTrue(response['running'])
        self.assertFalse(scope['btn'].browser['disabled'])
        self.assertEqual(scope['btn'].browser['description'], 'Add Links')
        self.assertEqual(scope['btn_stop'].browser_layout['display'], 'inline-block')
        self.assertTrue(scope['btn_quick'].browser['disabled'])
        self.assertTrue(scope['btn_resume'].browser['disabled'])

    def test_short_batch_retains_final_controls_before_first_browser_poll(self):
        scope = self.finished_controls_scope()
        item = task('failed')
        item.status = 'failed'
        callbacks, workers = {}, []
        output = SimpleNamespace(register_callback=lambda name, fn: callbacks.update({name: fn}))
        colab = SimpleNamespace(output=output)
        scope.update(_restore_standard_status_widgets=Mock(), _reset_cancel_state=Mock(),
                     _set_live_controls=Mock(), clear_output=Mock(), display=Mock(),
                     input_ui=object(), _colab_live_status_html=Mock(),
                     execute_selected_tasks=Mock(), _auto_retry_state={'pending': False},
                     Thread=lambda target, **_kw: SimpleNamespace(start=lambda: workers.append(target)),
                     queue_ui=SimpleNamespace(layout=SimpleNamespace(display='none')),
                     pending_queue=[], os=os, SESSION_FILE='unused', print=Mock())

        with patch.dict(sys.modules, {'google': SimpleNamespace(colab=colab), 'google.colab': colab}):
            scope['_launch_live_batch']([item], 'video')
        token = scope['_live_batch'].token
        workers[0]()
        self.assertIsNone(scope['_live_batch'])
        self.assertFalse(scope['_last_live_status']['running'])
        # The final status must exist even though the browser never polled while running.
        response = callbacks['ultimate_downloader.live_status'](token).data
        self.assertEqual(response['counts'], {'done': 0, 'total': 1, 'failed': 1})
        self.assertEqual(scope['btn_retry'].browser_layout['display'], 'inline-block')
        self.assertEqual(scope['btn_stop'].browser_layout['display'], 'none')
        self.assertFalse(scope['btn_settings'].browser['disabled'])
        self.assertEqual(scope['btn'].browser['description'], 'Resolve Links')

    def test_poll_shows_sequential_transfer_without_a_progress_monitor(self):
        scope = load_real('LiveBatchManager', '_colab_live_status_snapshot')
        original = task('original')
        original.status = 'failed'
        manager = scope['LiveBatchManager']([original], 'video')
        current = task('current')
        current.filename = 'Current file.mkv'
        current.status = 'downloading'
        scope.update(_live_batch=manager, _live_status_tasks=[current],
                     _last_live_status=None, _colab_live_status_active=True,
                     progress_bar=SimpleNamespace(description='DL: 35%', layout=SimpleNamespace(display='none')),
                     status_label=SimpleNamespace(value='', layout=SimpleNamespace(display='none')),
                     _per_task_accordion=SimpleNamespace(layout=SimpleNamespace(display='none')),
                     live_log=SimpleNamespace(layout=SimpleNamespace(display='none')),
                     _per_task_bars={}, _live_log_lock=Lock(), _live_log_messages=deque(),
                     download_stats={'current': {'pct': 35, 'speed_mbs': 4}},
                     JSON=lambda data: SimpleNamespace(data=data), re=re,
                     html=__import__('html'), time=SimpleNamespace(time=lambda: 123))

        response = scope['_colab_live_status_snapshot'](manager.token).data

        self.assertEqual(response['description'], '⚡ 0/1')
        self.assertEqual(response['progress'], 35)
        self.assertEqual(response['bars'], [dict(id='current', description='Current file.mkv  35% (4.0 MB/s)',
                                                 value=35, state='warning')])

    def test_failed_file_reason_remains_visible_after_its_progress_bar_disappears(self):
        scope = self.finished_controls_scope()
        failed = task('episode')
        failed.status = 'failed'
        failed.error = 'PikPak saving shared file: request failed [FILE_NOT_FOUND]'
        scope['_live_batch'] = scope['LiveBatchManager']([failed], 'video')

        response = scope['_colab_live_status_snapshot'](scope['_live_batch'].token).data

        self.assertEqual(response['bars'], [])
        self.assertEqual(response['counts']['failed'], 1)
        self.assertEqual(response['errors'], [{'name': 'episode', 'error': failed.error}])

    def test_live_status_shows_server_cooldown_instead_of_starting(self):
        scope = self.finished_controls_scope()
        active = task('episode')
        active.status = 'downloading'
        scope['_live_batch'] = scope['LiveBatchManager']([active], 'video')
        scope['download_stats']['episode'] = dict(
            pct=45, speed_mbs=0, retry_reason='Server unavailable (HTTP 503)', retry_until=138)

        response = scope['_colab_live_status_snapshot'](scope['_live_batch'].token).data

        self.assertEqual(response['bars'][0]['value'], 45)
        self.assertIn('HTTP 503', response['bars'][0]['description'])
        self.assertIn('retry in 15s', response['bars'][0]['description'])
        self.assertEqual(response['bars'][0]['state'], 'info')

    def test_sequential_torbox_file_is_active_during_transfer(self):
        scope = load_real('process_tb_magnet_file_tasks')
        item = task('torbox')
        item.filename = 'TorBox file.mkv'
        states = []

        def download(*_args, **_kwargs):
            states.append(item.status)
            return 'file.mkv'

        def move(*_args, **_kwargs):
            states.append(item.status)

        scope.update(_group_tasks_by_torrent=lambda _tasks: {'torrents/1': [('2', item)]},
                     _TB_REQUESTDL_ID_PARAM={'torrents': 'torrent_id'},
                     _tb_fetch_item=lambda *_args: {'download_state': 'completed'},
                     _tb_progress_pct=lambda _item: 100, _update_torrent_progress=Mock(),
                     _tb_requestdl=lambda *_args: ('https://example.test/file', ''),
                     _strip_size_suffix=lambda name: name, download_with_aria2=download,
                     handle_file_processing=move, _reset_progress_bar=Mock(),
                     progress_lock=Lock(), progress_bar=SimpleNamespace(),
                     _cancel_requested=False, COLAB_ROOT='/tmp', DUPLICATE_SKIP=object(),
                     print=Mock(), time=time)

        self.assertEqual(scope['process_tb_magnet_file_tasks']([item], 'token'), 1)
        self.assertEqual(states, ['downloading', 'moving'])
        self.assertEqual(item.status, 'done')

    def test_browser_poll_reads_live_state_without_background_widget_delivery(self):
        scope = load_real('LiveBatchManager', '_colab_live_status_snapshot')
        item = task('one')
        item.status = 'downloading'
        manager = scope['LiveBatchManager']([item], 'video')
        bar = SimpleNamespace(description='File one 40%', value=40, bar_style='warning')
        progress = SimpleNamespace(value=40, description='DL 0/1', layout=SimpleNamespace(display='block'))
        status = SimpleNamespace(value='<small>2 MB/s</small>', layout=SimpleNamespace(display='block'))
        panel = SimpleNamespace(layout=SimpleNamespace(display='block'))
        log = SimpleNamespace(layout=SimpleNamespace(display='block'))
        scope.update(_live_batch=manager, _last_live_status=None,
                     _colab_live_status_active=False, progress_bar=progress,
                     status_label=status, _per_task_accordion=panel, live_log=log,
                     _per_task_bars={'one': bar}, _live_log_lock=Lock(),
                     _live_log_messages=deque(['Downloading file one\n']),
                     download_stats={'one': {'pct': 40, 'speed_mbs': 2}},
                     JSON=lambda data: SimpleNamespace(data=data), re=re,
                     html=__import__('html'), time=SimpleNamespace(time=lambda: 123))

        response = scope['_colab_live_status_snapshot'](manager.token).data

        self.assertEqual(response['progress'], 40)
        self.assertEqual(response['description'], '⚡ 0/1')
        self.assertEqual(response['updated_at'], 123)
        self.assertEqual(response['summary'], '2 MB/s')
        self.assertEqual(response['bars'][0]['value'], 40)
        self.assertIn('Downloading file one', response['log'])
        self.assertEqual(progress.layout.display, 'none')
        self.assertEqual(status.layout.display, 'none')
        self.assertTrue(scope['_colab_live_status_active'])

        # The old widget still describes the original wave; the panel must
        # immediately use the manager's expanded task list instead.
        self.assertTrue(manager.add([task('two')]))
        progress.value = 90
        progress.description = 'DL 0/1'
        updated = scope['_colab_live_status_snapshot'](manager.token).data
        self.assertEqual(updated['counts']['total'], 2)
        self.assertEqual(updated['description'], '⚡ 0/2')
        self.assertEqual(updated['progress'], 20)

        item.status = 'done'
        manager.all_tasks[1].status = 'moving'
        self.assertTrue(manager.add([task('three')]))
        moving = scope['_colab_live_status_snapshot'](manager.token).data
        self.assertEqual(moving['description'], '📤 1/3')
        self.assertAlmostEqual(moving['progress'], 200 / 3)

    def test_custom_panel_syncs_one_payload_without_hidden_progress_widgets(self):
        scope = load_real('_ProgressRow', 'update_progress_display')
        bars = []
        panel = SimpleNamespace(
            set_bars=lambda rows: bars.append([(row.model_id, row.value) for row in rows]),
            set_title=Mock(), layout=SimpleNamespace(display='none'))
        scope.update(
            time=SimpleNamespace(time=lambda: 100), last_display_speed=0,
            batch_start_time=None, download_stats={'one': {'pct': 40, 'speed_mbs': 2}},
            _disk_free_gb=lambda: float('inf'), progress_bar=SimpleNamespace(),
            status_label=SimpleNamespace(), _per_task_bars={}, _per_task_done_at={},
            _PER_TASK_LINGER=2, _per_task_accordion=panel,
            _colab_live_status_active=False,
            _per_task_box=SimpleNamespace(children=[]),
            widgets=SimpleNamespace(FloatProgress=Mock(side_effect=AssertionError('hidden widget created'))))
        item = task('one')
        item.status = 'downloading'

        scope['update_progress_display']([item])
        scope['download_stats']['one']['pct'] = 65
        scope['update_progress_display']([item])

        self.assertEqual(bars, [[('one', 40)], [('one', 65)]])
        self.assertIsInstance(scope['_per_task_bars']['one'], scope['_ProgressRow'])

    def test_background_messages_append_without_capturing_other_threads(self):
        scope = load_real('print')
        log = SimpleNamespace(value='')
        ordinary_print = Mock()
        scope.update(_live_batch=object(), current_thread=lambda: object(),
                     main_thread=lambda: object(), _live_log_lock=Lock(),
                     _live_log_messages=deque(maxlen=100), live_log=log,
                     html=__import__('html'), _builtin_print=ordinary_print,
                     _colab_live_status_active=False)

        scope['print']('Downloading', 'file.mkv')
        self.assertIn('Downloading file.mkv', log.value)
        scope['print']('<secret>')
        self.assertIn('&lt;secret&gt;', log.value)
        ordinary_print.assert_not_called()

        scope['current_thread'] = scope['main_thread'] = lambda: 'main'
        scope['_colab_live_status_active'] = True
        scope['print']('Added another link')
        self.assertIn('Added another link', ''.join(scope['_live_log_messages']))
        ordinary_print.assert_not_called()

        scope['_colab_live_status_active'] = False
        scope['print']('Adding links')
        ordinary_print.assert_called_once_with('Adding links')

    def test_progress_monitor_sleeps_after_display_error(self):
        scope = load_real('progress_monitor')
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            scope['stop_monitor'] = True

        scope.update(stop_monitor=False, update_progress_display=Mock(side_effect=ValueError('UI')),
                     time=SimpleNamespace(sleep=sleep), print=Mock())
        scope['progress_monitor']([], interval=0.5)

        self.assertEqual(sleeps, [0.5])
        scope['print'].assert_called_once()


class LiveResumeTests(TestCase):
    def setUp(self):
        self.scope = LiveDisplayTests().finished_controls_scope(
            'execute_batch', '_run_live_waves', '_run_auto_retry_chain', '_arm_auto_retry')
        self.workers = []
        self.cancel = False
        self.done = task('done', 'pikpak')
        self.done.status = 'done'
        self.skipped = task('skipped', 'pikpak')
        self.skipped.status = 'skipped'
        self.retry = task('retry', 'pikpak')
        self.retry.status = 'failed'
        self.retry.error = 'old failure'
        self.retry.pikpak_saved_file_id = 'saved-account-file'
        self.partial = task('partial', 'pikpak')
        self.partial.status = 'downloading'
        self.moving = task('moving', 'pikpak')
        self.moving.status = 'moving'
        self.saved = {'tasks': [vars(t).copy() for t in
                               (self.done, self.skipped, self.retry, self.partial, self.moving)],
                      'yt_success': 7, 'yt_fail': 3, 'playlist_range': '2-5',
                      'subtitle_langs': ['vi']}
        scope = self.scope
        scope.update(
            load_session=Mock(return_value=self.saved), task_from_dict=lambda d: SimpleNamespace(**d),
            cancel_requested=lambda: self.cancel, print=Mock(),
            settings_ui=SimpleNamespace(layout=SimpleNamespace(display='none')),
            token_gf=SimpleNamespace(value=''), token_rd=SimpleNamespace(value=''),
            token_tb=SimpleNamespace(value=''), get_active_debrid=lambda: ('none', '', ''),
            playlist_selection=SimpleNamespace(value=''), subtitle_langs=SimpleNamespace(value=('en',)),
            tmdb_is_enabled=lambda: False, _apply_queue_overrides=Mock(),
            setup_environment=Mock(), _run_download_pipeline=Mock(return_value=(0, 0)),
            start_keep_alive=Mock(), stop_keep_alive=Mock(), save_session=Mock(),
            clear_session=Mock(), check_resume_available=Mock(), reset_progress=Mock(),
            _restore_standard_status_widgets=Mock(), _reset_cancel_state=Mock(),
            _set_live_controls=lambda: scope['btn'].__dict__.update(description='Add Links', disabled=False),
            clear_output=Mock(), display=Mock(), input_ui=object(), _colab_live_status_html=Mock(),
            execute_selected_tasks=Mock(side_effect=AssertionError('new-batch executor used for resume')),
            queue_ui=SimpleNamespace(layout=SimpleNamespace(display='none')), pending_queue=[],
            os=os, SESSION_FILE='unused',
            Thread=lambda target, **_kw: SimpleNamespace(start=lambda: self.workers.append(target)),
            _auto_retry_state={'pending': False, 'remaining': 0, 'total': 0},
            time=SimpleNamespace(time=lambda: 123, sleep=Mock()))
        scope['concurrent_slider'].value = 5
        scope['auto_retry_input'].value = ''

    def launch(self):
        callbacks = {}
        output = SimpleNamespace(register_callback=lambda name, fn: callbacks.update({name: fn}))
        colab = SimpleNamespace(output=output)
        with patch.dict(sys.modules, {'google': SimpleNamespace(colab=colab), 'google.colab': colab}):
            self.scope['execute_batch']('video', resume=True)
        self.assertEqual(len(self.workers), 1)
        self.scope['_run_download_pipeline'].assert_not_called()
        return self.scope['_live_batch']

    def test_resume_authenticates_on_caller_before_background_batch(self):
        scope = self.scope
        calls = []
        caller = threading.current_thread()

        def restore():
            scope['drive_api_checkbox'].value = True

        def authenticate():
            calls.append(threading.current_thread())
            self.assertIsNone(scope['_live_batch'])
            self.assertEqual(self.workers, [])
            return True

        scope['load_dir_settings'].side_effect = restore
        scope['_init_drive_api'].side_effect = authenticate
        self.launch()
        self.assertEqual(calls, [caller])

    def test_resume_with_mount_selected_does_not_request_api_access(self):
        self.launch()
        self.scope['_init_drive_api'].assert_not_called()

    def test_resume_returns_control_and_runs_additions_with_completed_files_retained(self):
        scope = self.scope
        manager = self.launch()
        added = task('added', 'youtube')
        calls = []

        def pipeline(**kwargs):
            calls.append(kwargs)
            self.assertIs(kwargs['all_tasks'], manager.all_tasks)
            self.assertIs(kwargs['live_manager'], manager)
            self.assertEqual(kwargs['max_workers'], 5)
            self.assertEqual(scope['btn'].description, 'Add Links')
            self.assertFalse(scope['btn'].disabled)
            if len(calls) == 1:
                self.assertEqual([t.id for t in kwargs['parallel_tasks']], ['retry', 'partial', 'moving'])
                self.assertTrue(all(t.status == 'pending' and t.error is None for t in kwargs['parallel_tasks']))
                self.assertEqual(kwargs['parallel_tasks'][0].pikpak_saved_file_id, 'saved-account-file')
                self.assertEqual(scope['yt_success_cumulative'], 7)
                self.assertEqual(scope['yt_fail_cumulative'], 0)
                self.assertTrue(manager.add([added]))
            else:
                self.assertEqual(kwargs['youtube_urls'], [added.url])
                added.status = 'done'
            for t in kwargs['parallel_tasks']:
                t.status = 'done'
            return (4 + (len(calls) - 1), 0)

        scope['_run_download_pipeline'].side_effect = pipeline
        self.workers[0]()
        self.assertEqual(len(calls), 2)
        self.assertEqual([t.id for t in manager.all_tasks], ['done', 'skipped', 'retry', 'partial', 'moving', 'added'])
        self.assertEqual(manager.all_tasks[0].status, 'done')
        self.assertEqual(manager.all_tasks[1].status, 'skipped')
        self.assertEqual(scope['playlist_selection'].value, '2-5')
        self.assertEqual(scope['subtitle_langs'].value, ('vi',))
        self.assertIsNone(scope['_live_batch'])
        scope['clear_session'].assert_called_once()

    def test_stop_during_resume_preparation_saves_pending_tasks_and_starts_no_transfer(self):
        scope = self.scope
        manager = self.launch()
        scope['setup_environment'].side_effect = lambda *_args: setattr(self, 'cancel', True)
        self.workers[0]()
        scope['_run_download_pipeline'].assert_not_called()
        self.assertEqual(scope['save_session'].call_args.args[0], manager.all_tasks)
        self.assertEqual([t.status for t in manager.all_tasks], ['done', 'skipped', 'pending', 'pending', 'pending'])
        scope['clear_session'].assert_not_called()

    def test_auto_retry_uses_same_live_batch_and_does_not_rearm_budget(self):
        scope = self.scope
        scope['auto_retry_input'].value = '1'
        manager = self.launch()
        calls = []

        def pipeline(**kwargs):
            calls.append(kwargs)
            self.assertIs(kwargs['live_manager'], manager)
            self.assertTrue(manager.is_running())
            for t in kwargs['parallel_tasks']:
                t.status = 'failed' if len(calls) == 1 else 'done'
            return (1, 3) if len(calls) == 1 else (4, 0)

        scope['_run_download_pipeline'].side_effect = pipeline
        self.workers[0]()
        self.assertEqual(len(calls), 2)
        self.assertEqual(scope['_auto_retry_state']['remaining'], 0)
        self.assertFalse(scope['_auto_retry_state']['pending'])
        self.assertEqual(len(self.workers), 1)
        scope['clear_session'].assert_called_once()

    def test_second_resume_cannot_start_another_live_batch(self):
        self.launch()
        self.scope['execute_batch']('video', resume=True)
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.scope['load_session'].call_count, 1)

    def test_quick_download_retry_preserves_remaining_budget(self):
        scope = self.scope
        scope['auto_retry_input'].value = '3'
        scope['_auto_retry_state'].update(remaining=0, total=1, pending=False)

        scope['execute_batch']('video', resume=True, auto_retry_chain=True)

        self.assertEqual(self.workers, [])
        scope['_run_download_pipeline'].assert_called_once()
        self.assertIsNone(scope['_run_download_pipeline'].call_args.kwargs['live_manager'])
        self.assertEqual(scope['_auto_retry_state'], {'remaining': 0, 'total': 1, 'pending': False})


if __name__ == '__main__':
    main()
