"""Exercise live additions against the real batch coordinator and parallel loop."""

import ast
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Dict
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import List, Optional, Tuple
from unittest import TestCase, main
from unittest.mock import Mock
from uuid import uuid4
import queue
import os
import re
import threading
import time


SOURCE = Path(__file__).with_name('ultimate_downloader.py')
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def load_real(*names):
    wanted = set(names)
    nodes = [node for node in TREE.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted]
    assert {node.name for node in nodes} == wanted
    scope = dict(Lock=Lock, List=List, Optional=Optional, Tuple=Tuple, Dict=Dict,
                 uuid4=uuid4,
                 DownloadTask=SimpleNamespace,
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


class LiveDisplayTests(TestCase):
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


if __name__ == '__main__':
    main()
