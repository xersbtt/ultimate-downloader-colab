"""Queue append and folder-aware naming regressions without Colab/network setup."""

import ast
import copy
import os
import re
import unittest
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from test_queue_editing import SelectionWidget
from test_tmdb_anime_classification import _load_destination_subject


SOURCE = Path(__file__).with_name('ultimate_downloader.py')
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def load_definitions(namespace, *names):
    wanted = set(names)
    nodes = [node for node in TREE.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted
             or isinstance(node, ast.Assign) and any(
                 isinstance(target, ast.Name) and target.id in wanted for target in node.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)


def base_namespace():
    return dict(os=os, re=re, List=List, Dict=Dict, Tuple=Tuple, Optional=Optional,
                Any=Any, dataclass=dataclass, field=field, fields=fields, uuid4=uuid4,
                unquote=unquote, urlparse=urlparse, parse_qs=parse_qs, print=Mock())


class FolderIdentityTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_destination_subject()
        self.ns.update(base_namespace(), _batch_episode_cache={})
        load_definitions(self.ns, 'DownloadTask', '_TASK_FIELDS', 'task_from_dict',
                         'KEEP_EXTENSIONS', '_MULTI_EP_TAIL', '_TB_ENDPOINT_BY_TYPE',
                         '_TB_FOLDER_IGNORED_EXTENSIONS', '_TB_FOLDER_SAMPLE_MAX_BYTES',
                         'sanitize_filename', 'clean_show_name', '_strip_size_suffix',
                         '_split_subtitle_lang', '_match_cache_key', 'get_batch_episode',
                         'analyze_batch_episodes', '_multi_ep_end', 'detect_episode_info',
                         '_tb_contextual_filename', '_tb_folder_file_is_irrelevant',
                         '_tb_file_in_extras_folder',
                         '_make_torrent_file_task', 'resolve_tb_folder_files',
                         'resolve_tb_magnet_files',
                         'analyze_batch_metadata')
        self.ns.update(_tmdb_match_cache={}, tmdb_is_enabled=lambda: True,
                       _save_tmdb_query_cache=Mock(), _tmdb_search=Mock(return_value=None))

    def contextual(self, path, item=''):
        return self.ns['_tb_contextual_filename'](path, item)

    def info(self, name):
        return self.ns['detect_episode_info'](name)

    def destination(self, name):
        clean = self.ns['_strip_size_suffix'](name)
        return self.ns['determine_destination_path'](clean, dry_run=True)[0].replace('\\', '/')

    def test_seasons_and_shows_have_distinct_names_cache_keys_and_destinations(self):
        paths = [f'{show} Season {season}/Episode 1.mkv'
                 for show in ('The Simpsons', 'Futurama') for season in (1, 2)]
        names = [self.contextual(path) for path in paths]
        self.ns['analyze_batch_episodes'](names)
        self.assertEqual(len(set(names)), 4)
        self.assertEqual(len({self.ns['_match_cache_key'](name) for name in names}), 4)
        destinations = [self.destination(name) for name in names]
        self.assertEqual(len(set(destinations)), 4)
        self.assertTrue(destinations[0].endswith('The Simpsons/Season 01/The Simpsons - S01E01.mkv'))
        self.assertTrue(destinations[1].endswith('The Simpsons/Season 02/The Simpsons - S02E01.mkv'))
        self.ns['analyze_batch_metadata'](names)
        self.assertEqual(self.ns['_tmdb_search'].call_count, 2)
        self.ns['_tmdb_search'].assert_any_call('tv', 'The Simpsons', None)

    def test_nested_folders_windows_paths_subtitle_directories_and_short_names(self):
        examples = [
            ('The Simpsons/Season 02/Episode 3.mkv', '', 'The Simpsons', 2, 3),
            (r'Batch\The Simpsons\S03\01.mkv', '', 'The Simpsons', 3, 1),
            ('The Simpsons Season 2/Subs/Episode 3.en.srt', '', 'The Simpsons', 2, 3),
            ('Episode 3.mkv', 'The Simpsons Season 4', 'The Simpsons', 4, 3),
            ('Season 02/Episode 3.mkv', 'The Simpsons Season 4', 'The Simpsons', 2, 3),
            ('ER/Season 02/Episode 3.mkv', '', 'ER', 2, 3),
            ('The.Simpsons.S00.1080p/Episode 3.mkv', '', 'The Simpsons', 0, 3),
        ]
        for path, item, show, season, episode in examples:
            with self.subTest(path=path):
                info = self.info(self.contextual(path, item))
                self.assertEqual((info['show_name'], info['season'], info['episode']),
                                 (show, season, episode))

    def test_explicit_file_identity_wins_and_ranges_survive(self):
        full = 'Actual.Show.S03E04-E06.mkv'
        self.assertEqual(self.contextual(f'Wrong Show Season 9/{full}'), full)
        for full in ('24.S03E04.mkv', '[Actual Show] S03E04.mkv'):
            self.assertEqual(self.contextual(f'Wrong Show Season 9/{full}'), full)
        bare = self.contextual('The Simpsons Season 9/S02E03-E05 - Episode Title.mkv')
        info = self.info(bare)
        self.assertEqual((info['show_name'], info['season'], info['episode'], info['episode_end']),
                         ('The Simpsons', 2, 3, 5))
        named = self.info(self.contextual('Wrong Show Season 2/Actual Show - 03.mkv'))
        self.assertEqual((named['show_name'], named['season'], named['episode']),
                         ('Actual Show', 2, 3))

    def test_pack_episode_range_is_not_copied_into_each_file(self):
        name = self.contextual('The Simpsons S02E01-E22/05.mkv')
        info = self.info(name)
        self.assertEqual((info['show_name'], info['season'], info['episode'], info['episode_end']),
                         ('The Simpsons', 2, 5, None))

    def test_folder_season_prevents_tmdb_absolute_remapping(self):
        self.ns['match']['value'] = {'type': 'tv', 'name': 'The Simpsons', 'year': '1989',
                                    'seasons': {'1': 13, '2': 22}}
        self.ns['_map_absolute_episode'] = Mock(side_effect=AssertionError('must keep folder season'))
        dest = self.destination(self.contextual('The Simpsons Season 2/Episode 1.mkv'))
        self.assertTrue(dest.endswith('The Simpsons (1989)/Season 02/The Simpsons - S02E01.mkv'))

    def test_subtitle_pairs_and_as_is_downloads_keep_identity(self):
        video = self.contextual('The Simpsons Season 2/Episode 1.mkv')
        sub = self.contextual('The Simpsons Season 2/Subs/Episode 1.en.srt')
        self.assertEqual(os.path.splitext(video)[0], sub[:-len('.en.srt')])
        self.ns['is_auto_organize_enabled'] = lambda: False
        first = self.destination(self.contextual('The Simpsons Season 1/Episode 1.mkv'))
        second = self.destination(video)
        self.assertNotEqual(first, second)
        self.assertIn('Downloads/', first)

    def test_show_folder_without_season_keeps_absolute_number_mapping_available(self):
        name = self.contextual('One Piece/1085.mkv')
        info = self.info(name)
        self.assertEqual((info['show_name'], info['episode'], info['has_sxe']),
                         ('One Piece', 1085, False))
        self.ns['match']['value'] = {'type': 'tv', 'name': 'One Piece', 'year': '1999'}
        self.ns['_map_absolute_episode'] = Mock(return_value=(20, 194))
        self.assertIn('Season 20/One Piece - S20E194.mkv', self.destination(name))

    def test_resolver_keeps_identity_after_session_round_trip_for_all_item_types(self):
        self.ns['_tb_fetch_item'] = Mock(return_value={
            'name': 'The Simpsons', 'files': [
                {'id': i, 'name': f'The Simpsons Season {season}/Episode 1.mkv', 'size': 10 * 1024**2}
                for i, season in enumerate((1, 2))]})
        for kind in ('torrents', 'usenet', 'webdl'):
            with self.subTest(kind=kind):
                tasks = self.ns['resolve_tb_folder_files'](
                    f'https://torbox.app/download?id=42&type={kind}', 'test-key')
                restored = [self.ns['task_from_dict'](asdict(task)) for task in tasks]
                self.assertEqual([t.filename for t in restored], [t.filename for t in tasks])
                self.assertNotEqual(restored[0].original_url, restored[1].original_url)
                self.assertNotEqual(self.destination(restored[0].filename),
                                    self.destination(restored[1].filename))

    def test_extras_folders_resolve_with_default_selection_hint(self):
        self.ns['_tb_fetch_item'] = Mock(return_value={
            'name': 'The Simpsons', 'files': [
                {'id': 1, 'name': 'The Simpsons/Season 2/Episode 1.mkv', 'size': 10 * 1024**2},
                {'id': 2, 'name': r'The Simpsons\Extras\Behind.the.Scenes\Making Of.mkv',
                 'size': 10 * 1024**2},
                {'id': 3, 'name': 'The Simpsons/Specials/Episode 2.mkv', 'size': 10 * 1024**2},
                {'id': 4, 'name': 'The Simpsons/Bonus Features/Sample.mkv', 'size': 500 * 1024},
            ]})
        tasks = self.ns['resolve_tb_folder_files'](
            'https://torbox.app/download?id=42&type=torrents', 'test-key')
        self.assertEqual([t.original_url for t in tasks], ['42:1', '42:2', '42:3', '42:4'])
        self.assertEqual([t.selected_by_default for t in tasks], [True, False, True, False])
        self.assertEqual([self.ns['task_from_dict'](asdict(t)).selected_by_default for t in tasks],
                         [True, False, True, False])

    def test_magnet_files_use_the_same_extras_selection_hint(self):
        self.ns.update(TORBOX_API_BASE='https://api.test',
                       _get_tb_headers=lambda _key: {},
                       requests=SimpleNamespace(post=Mock(return_value=SimpleNamespace(
                           json=lambda: {'success': True, 'data': {'torrent_id': 42}}))),
                       _tb_fetch_item=Mock(return_value={
                           'status': 'completed', 'files': [
                               {'id': 1, 'name': 'Movie/Main.mkv', 'size': 10 * 1024**2},
                               {'id': 2, 'name': 'Movie/Trailers/Preview.mkv', 'size': 500 * 1024},
                           ]}))
        tasks = self.ns['resolve_tb_magnet_files']('magnet:?xt=test', 'test-key')
        self.assertEqual([t.original_url for t in tasks], ['42:1', '42:2'])
        self.assertEqual([t.selected_by_default for t in tasks], [True, False])

    def test_missing_context_non_episode_and_stale_batch_data(self):
        self.assertEqual(self.contextual('Episode 1.mkv'), 'Episode 1.mkv')
        self.assertEqual(self.contextual('Some Folder/Movie.2020.mkv'), 'Movie.2020.mkv')
        self.ns['_batch_episode_cache']['Episode 1.mkv'] = 25
        self.assertEqual(self.info(self.contextual('The Simpsons Season 2/Episode 1.mkv'))['episode'], 1)


class AppendQueueTests(unittest.TestCase):
    def setUp(self):
        self.ns = base_namespace()
        load_definitions(self.ns, 'DownloadTask', '_strip_size_suffix', '_queue_task_key',
                         '_selected_queue_indices', 'update_queue_display',
                         'show_queue_preview', 'queue_add_links', 'on_resolve_links', '_resolve_queue_links')
        self.ns.update(queue_list=SelectionWidget(), pending_queue=[], _live_batch=None,
                       _queue_dest_preview=lambda task: None, get_tmdb_match=lambda name: None,
                       tmdb_is_enabled=lambda: True, TMDB_CLEARED={'cleared': True},
                       is_auto_organize_enabled=lambda: True,
                       analyze_batch_episodes=Mock(return_value={}),
                       analyze_batch_metadata=Mock(return_value=0),
                       _apply_tmdb_overrides=Mock(), _apply_queue_overrides=Mock(),
                       get_youtube_subtitles=Mock(return_value={}),
                       _running_live_batch=lambda: None,
                       save_session=Mock(), start_keep_alive=Mock(), stop_keep_alive=Mock(),
                       reset_progress=Mock())
        for name in ('text_area', 'playlist_selection', 'queue_options', 'playlist_options',
                     'tmdb_group', 'identity_row', 'route_row', 'season_override_row',
                     'queue_edit_actions', 'queue_ui', 'btn', 'btn_quick',
                     'btn_queue_start', 'btn_queue_start_subs', 'btn_queue_cancel'):
            self.ns[name] = SimpleNamespace(value='', disabled=False, layout=SimpleNamespace(display='none'))
        self.ns['subtitle_langs'] = SimpleNamespace(value=('ja',), options=[('Japanese', 'ja')])
        self.old = [self.task('Old 1.mkv'), self.task('Old 2.mkv')]
        self.old[0].name_override, self.old[0].season_override = 'Manual Title', 7
        self.ns['show_queue_preview'](self.old, 'video')
        self.ns['queue_list'].value = (self.ns['queue_list'].options[0],)

    def task(self, filename, **kwargs):
        return self.ns['DownloadTask'](url=f'https://test/{filename}', filename=filename,
                                       source='test', link_type=kwargs.pop('link_type', 'direct'), **kwargs)

    def add(self, tasks):
        self.ns['_resolve_queue_links'] = Mock(return_value=tasks)
        self.ns['text_area'].value = 'https://test/new\nhttps://test/new'
        self.ns['on_resolve_links']()

    def test_live_addition_stays_in_preview_without_overwriting_active_session(self):
        active = SimpleNamespace(lock=Lock(), all_tasks=self.old,
                                 is_running=lambda: True)
        self.ns['_live_batch'] = active
        self.ns['_running_live_batch'] = lambda: active
        self.ns['_match_cache_key'] = lambda filename: filename
        self.ns['_set_live_controls'] = Mock()
        self.ns['pending_queue'] = []
        self.ns['queue_list'].value = ()
        self.ns['queue_list'].options = []
        self.ns['queue_ui'].layout.display = 'none'
        self.ns['save_session'].reset_mock()

        new = self.task('New While Downloading.mkv')
        self.add([new])

        self.assertEqual(self.ns['pending_queue'], [new])
        self.assertEqual(self.ns['_selected_queue_indices'](), [0])
        self.ns['save_session'].assert_not_called()
        self.ns['stop_keep_alive'].assert_not_called()
        self.ns['reset_progress'].assert_not_called()

    def test_append_preserves_order_overrides_selection_and_saves_combined_queue(self):
        before = copy.deepcopy([asdict(t) for t in self.old])
        new = self.task('New.mkv')
        self.add([new])
        self.assertEqual(self.ns['pending_queue'], self.old + [new])
        self.assertEqual([asdict(t) for t in self.old], before)
        self.assertEqual(self.ns['_selected_queue_indices'](), [0, 2])
        self.ns['save_session'].assert_called_once_with(self.old + [new], playlist_range='')
        self.ns['analyze_batch_metadata'].assert_called_with([t.filename for t in self.old + [new]])
        self.ns['_apply_queue_overrides'].assert_called_with(self.old + [new])
        self.ns['_resolve_queue_links'].assert_called_once_with(['https://test/new'])
        self.assertEqual(self.ns['text_area'].value, '')
        self.assertEqual(self.ns['btn'].description, 'Add Links')
        self.assertFalse(self.ns['btn'].disabled)

    def test_duplicate_torbox_files_are_skipped_without_losing_other_seasons(self):
        first = self.task('Episode 1.mkv', link_type='tb_magnet_file', original_url='42:1')
        self.ns['show_queue_preview']([first], 'video')
        duplicate = self.task('Renamed.mkv', link_type='tb_magnet_file', original_url='42:1')
        second = self.task('Episode 1.mkv', link_type='tb_magnet_file', original_url='42:2')
        self.add([duplicate, second, copy.deepcopy(second)])
        self.assertEqual(self.ns['pending_queue'], [first, second])

    def test_failure_empty_result_and_interrupt_keep_queue_and_retry_input(self):
        for outcome in ([], RuntimeError('offline'), KeyboardInterrupt()):
            with self.subTest(outcome=outcome):
                self.ns['_resolve_queue_links'] = Mock()
                if isinstance(outcome, BaseException):
                    self.ns['_resolve_queue_links'].side_effect = outcome
                else:
                    self.ns['_resolve_queue_links'].return_value = outcome
                self.ns['text_area'].value = 'https://test/new'
                self.ns['queue_add_links']()
                self.assertEqual(self.ns['pending_queue'], self.old)
                self.assertEqual(self.ns['_selected_queue_indices'](), [0])
                self.assertEqual(self.ns['text_area'].value, 'https://test/new')
                self.assertFalse(self.ns['btn'].disabled)
        self.ns['save_session'].assert_not_called()

    def test_blank_input_does_not_resolve(self):
        self.ns['_resolve_queue_links'] = Mock()
        self.ns['text_area'].value = '\n '
        self.ns['queue_add_links']()
        self.ns['_resolve_queue_links'].assert_not_called()

    def test_append_keeps_streaming_subtitles_playlist_range_and_empty_selection(self):
        videos = [self.task(str(i), link_type='youtube') for i in range(2)]
        self.ns['show_queue_preview'](videos, 'video')
        self.ns['subtitle_langs'].value = ('ja',)
        self.ns['playlist_selection'].value = '3-5'
        self.ns['queue_list'].value = ()
        new = self.task('New.mkv')
        self.add([new])
        self.assertEqual(self.ns['subtitle_langs'].value, ('ja',))
        self.assertEqual(self.ns['playlist_selection'].value, '3-5')
        self.assertEqual(self.ns['_selected_queue_indices'](), [2])

    def test_extras_stay_in_queue_but_are_not_preselected_on_add(self):
        main = self.task('Episode 1.mkv')
        extra = self.task('Making Of.mkv', selected_by_default=False)
        self.add([main, extra])
        self.assertEqual(self.ns['pending_queue'], self.old + [main, extra])
        self.assertEqual(self.ns['_selected_queue_indices'](), [0, 2])
        self.ns['queue_list'].value = tuple(self.ns['queue_list'].options)
        self.assertEqual(self.ns['_selected_queue_indices'](), [0, 1, 2, 3])

    def test_extras_are_not_preselected_in_a_new_queue(self):
        main = self.task('Episode 1.mkv')
        extra = self.task('Making Of.mkv', selected_by_default=False)
        self.ns['show_queue_preview']([main, extra], 'video')
        self.assertEqual(self.ns['pending_queue'], [main, extra])
        self.assertEqual(self.ns['_selected_queue_indices'](), [0])

    def test_shared_resolver_expands_playlists_and_retains_deferred_tasks(self):
        self.ns.update(token_gf=SimpleNamespace(value=''),
                       get_active_debrid=lambda: ('tb', '', 'tb-key'), STREAMING_HOSTS=(),
                       url_matches_host=lambda *_args: False, setup_environment=Mock(),
                       get_gofile_session=lambda _token: ('session', {}))
        direct = self.task('File.mkv')
        video = self.task('Video', link_type='youtube')
        self.ns['resolve_all_links'] = Mock(return_value=([direct], ['playlist'], ['mega'], ['magnet:?xt=test']))
        self.ns['resolve_youtube_playlist'] = Mock(return_value=[video])
        tasks = self.ns['_resolve_queue_links'](['https://test/file'])
        self.assertEqual([t.link_type for t in tasks], ['direct', 'youtube', 'mega', 'magnet'])
        self.assertEqual(tasks[:2], [direct, video])


if __name__ == '__main__':
    unittest.main()
