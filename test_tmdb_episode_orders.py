"""Episode-order regressions without importing the downloader's Colab pipeline."""

import ast
import copy
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

from test_tmdb_anime_classification import _load_destination_subject


COUNTS = {1: 4, 2: 3}
MATCH = {'id': 123, 'type': 'tv', 'name': 'Example Show', 'year': '2020',
         'is_anime': False, 'seasons': COUNTS}
# API arrays deliberately arrive out of order. Episode positions belong to the
# selected group; season_number/episode_number still identify the aired episode.
GROUP = {'id': 'split', 'name': 'TVDB Order', 'groups': [
    {'order': 2, 'episodes': [
        {'season_number': 2, 'episode_number': 3, 'order': 2},
        {'season_number': 2, 'episode_number': 1, 'order': 0},
        {'season_number': 2, 'episode_number': 2, 'order': 1},
    ]},
    {'order': 0, 'episodes': [{'season_number': 0, 'episode_number': 1, 'order': 0}]},
    {'order': 1, 'episodes': [
        {'season_number': 1, 'episode_number': 4, 'order': 2},
        {'season_number': 1, 'episode_number': 1, 'order': 0},
        {'season_number': 1, 'episode_number': 2, 'order': 1},
    ]},
]}
GROUP_LIST = [{'id': 'split', 'name': 'TVDB Order', 'type': 1, 'group_count': 3,
               'episode_count': 7},
              {'id': 'dvd', 'name': 'Box Set', 'type': 3, 'group_count': 3,
               'episode_count': 7}]


def load_downloader():
    path = Path(__file__).with_name('ultimate_downloader.py')
    names = {'episode_order_options', 'build_episode_group', 'map_episode_order',
             'map_episode_order_range', 'episode_position_tables', '_tmdb_episode_order_get',
             '_selected_episode_order_matches', 'load_episode_orders', 'apply_episode_order',
             '_apply_tmdb_overrides', '_queue_dest_preview', '_map_absolute_episode'}
    nodes = [node for node in ast.parse(path.read_text(encoding='utf-8')).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    ns = {'Optional': Optional, 'List': list, 'Dict': dict, 'Tuple': tuple, 'DownloadTask': object, 'os': os}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns


class EpisodeOrderMappingTests(unittest.TestCase):
    def test_all_named_orders_are_listed_without_auto_selecting_first_group(self):
        ns = load_downloader()
        options = ns['episode_order_options'](GROUP_LIST)
        self.assertEqual([value for _, value in options], ['', 'split', 'dvd'])
        self.assertIn('Original Air Date', options[1][0])
        self.assertIn('DVD', options[2][0])

    def test_identity_positions_and_specials_survive_json(self):
        ns = load_downloader()
        group = json.loads(json.dumps(ns['build_episode_group'](GROUP)))
        self.assertEqual(group['mapping']['0:1'], [0, 1])
        self.assertEqual(ns['map_episode_order'](4, COUNTS, group), (1, 3))
        self.assertEqual(ns['map_episode_order'](5, {'1': 4, '2': 3}, group), (2, 1))
        self.assertEqual(group['reverse']['1:3'], [1, 4])

    def test_omitted_filler_is_rejected_without_shifting_later_episodes(self):
        ns = load_downloader()
        group = ns['build_episode_group'](GROUP)
        with self.assertRaisesRegex(ValueError, 'absent'):
            ns['map_episode_order'](3, COUNTS, group)
        self.assertEqual(ns['map_episode_order'](4, COUNTS, group), (1, 3))

    def test_consecutive_range_maps_both_ends(self):
        ns = load_downloader()
        group = ns['build_episode_group'](GROUP)
        self.assertEqual(ns['map_episode_order_range'](5, 7, COUNTS, group), (2, 1, 3))

    def test_cross_season_missing_interior_and_reordered_ranges_are_rejected(self):
        ns = load_downloader()
        group = ns['build_episode_group'](GROUP)
        for start, end in [(4, 5), (2, 4), (7, 5)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                ns['map_episode_order_range'](start, end, COUNTS, group)
        reordered = copy.deepcopy(GROUP)
        reordered['groups'][0]['episodes'][1]['order'] = 1
        reordered['groups'][0]['episodes'][2]['order'] = 0
        with self.assertRaisesRegex(ValueError, 'not consecutive'):
            ns['map_episode_order_range'](5, 7, COUNTS, ns['build_episode_group'](reordered))

    def test_missing_counts_unknown_future_and_zero_are_rejected(self):
        ns = load_downloader()
        group = ns['build_episode_group'](GROUP)
        for number, counts in [(1, {}), (5, {1: 4, 3: 3}), (8, COUNTS), (0, COUNTS)]:
            with self.subTest(number=number), self.assertRaises(ValueError):
                ns['map_episode_order'](number, counts, group)

    def test_ambiguous_or_empty_group_is_rejected(self):
        ns = load_downloader()
        for data in [dict(GROUP, groups=[]),
                     dict(GROUP, groups=GROUP['groups'] + [GROUP['groups'][0]])]:
            with self.assertRaises(ValueError):
                ns['build_episode_group'](data)


class ContinuedNumberFixture:
    """Naruto-shaped metadata: season 2's literal TMDB episodes start at 53."""

    counts = {1: 52, 2: 52, 3: 54, 4: 62}
    splits = [35, 48, 48, 48, 41]

    def __init__(self):
        self.match = dict(MATCH, id=46260, name='Naruto', year='2002',
                          seasons=self.counts, is_anime=True)
        episodes = []
        absolute = 0
        for season, count in self.counts.items():
            for _ in range(count):
                absolute += 1
                episodes.append({'season_number': season, 'episode_number': absolute})
        groups = [{'order': 0, 'episodes': [
            {'season_number': 0, 'episode_number': number, 'order': number - 1} for number in (1, 2)]}]
        start = 0
        for season, count in enumerate(self.splits, 1):
            groups.append({'order': season, 'episodes': [dict(episode, order=index)
                for index, episode in enumerate(episodes[start:start + count])]})
            start += count
        self.group = {'id': 'naruto-tvdb', 'name': 'TVDB Order', 'groups': groups}

class ContinuedEpisodeNumberTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ContinuedNumberFixture()

    def test_main_downloader_mapper_handles_continued_episode_ids(self):
        ns = load_downloader()
        group = ns['build_episode_group'](self.fixture.group)
        tables = ns['episode_position_tables'](self.fixture.counts, [group],
                     lambda season: self.fail('complete groups should supply the positions'))
        enriched = dict(group, aired_episode_numbers=tables)
        self.assertEqual(ns['map_episode_order'](53, self.fixture.counts, enriched), (2, 18))
        self.assertEqual(ns['map_episode_order'](220, self.fixture.counts, enriched), (5, 41))

    def test_main_resume_upgrades_saved_orders_without_position_tables(self):
        ns = load_downloader()
        task = SimpleNamespace(filename='Naruto - 53.mkv', tmdb_override=dict(
            self.fixture.match, episode_group=ns['build_episode_group'](self.fixture.group)))
        ns.update(TMDB_CLEARED={'cleared': True}, _match_cache_key=lambda name: name,
                  _tmdb_match_is_current=lambda match: True, _tmdb_match_cache={})
        ns['_apply_tmdb_overrides']([task])
        group = task.tmdb_override['episode_group']
        self.assertEqual(group['aired_episode_numbers']['2'][0], 53)
        self.assertEqual(ns['map_episode_order'](53, self.fixture.counts, group), (2, 18))


class DownloaderOrderTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_downloader()
        self.tasks = [SimpleNamespace(filename=name, tmdb_override=None)
                      for name in ['Show - 4.mkv', 'Show - 4.en.srt', 'Other - 4.mkv']]
        self.matches = {task.filename: dict(MATCH) for task in self.tasks[:2]}
        self.matches[self.tasks[2].filename] = dict(MATCH, id=456)
        self.selected = [0, 1]
        self.get = Mock(side_effect=lambda path, params: {
            '/tv/123/episode_groups': {'results': GROUP_LIST},
            '/tv/episode_group/split': GROUP,
            '/tv/123/season/1': {'episodes': [{'episode_number': number} for number in range(1, 5)]}}[path])
        self.ns.update(pending_queue=self.tasks, _selected_queue_indices=lambda: self.selected,
                       tmdb_is_enabled=lambda: True, get_tmdb_match=self.matches.get,
                       _match_cache_key=lambda name: name, _tmdb_match_cache=self.matches,
                       _tmdb_get=self.get, _tmdb_episode_order_cache={}, _episode_order_show_id=None,
                       btn_episode_orders=SimpleNamespace(disabled=False),
                       tmdb_episode_order=SimpleNamespace(value='', options=[]),
                       episode_numbering_toggle=SimpleNamespace(value='Absolute'),
                       update_queue_display=Mock(), print=Mock())

    def apply_split(self):
        self.ns['load_episode_orders']()
        self.ns['tmdb_episode_order'].value = 'split'
        self.ns['apply_episode_order']()

    def test_load_and_apply_changes_only_selected_files_and_preserves_auto_match(self):
        original = dict(self.matches[self.tasks[0].filename])
        self.apply_split()
        for task in self.tasks[:2]:
            self.assertEqual(task.tmdb_override['episode_group']['id'], 'split')
        self.assertNotIn('episode_group', original)
        self.assertIsNone(self.tasks[2].tmdb_override)
        self.assertEqual(self.ns['episode_numbering_toggle'].value, 'Season match')
        self.ns['update_queue_display'].assert_called_once_with(preserve_selection=True)

    def test_standard_order_clears_selected_group(self):
        self.apply_split()
        self.ns['tmdb_episode_order'].value = ''
        self.ns['apply_episode_order']()
        self.assertNotIn('episode_group', self.tasks[0].tmdb_override)

    def test_switching_show_requires_loading_its_orders(self):
        self.ns['load_episode_orders']()
        self.selected[:] = [2]
        self.ns['tmdb_episode_order'].value = 'split'
        self.ns['apply_episode_order']()
        self.assertIsNone(self.tasks[2].tmdb_override)

    def test_mixed_selection_is_rejected_before_fetch_or_mutation(self):
        self.selected[:] = [0, 2]
        self.ns['load_episode_orders']()
        self.get.assert_not_called()
        self.assertTrue(all(task.tmdb_override is None for task in self.tasks))

    def test_video_choice_also_persists_for_an_unselected_matching_subtitle(self):
        self.selected[:] = [0]
        self.ns['_match_cache_key'] = lambda name: name.replace('.en.srt', '.srt')
        self.apply_split()
        self.assertEqual(self.tasks[1].tmdb_override['episode_group']['id'], 'split')
        self.assertIsNone(self.tasks[2].tmdb_override)

    def test_empty_selection_and_disabled_matching_do_not_fetch(self):
        self.selected[:] = []
        self.ns['load_episode_orders']()
        self.selected[:] = [0]
        self.ns['tmdb_is_enabled'] = lambda: False
        self.ns['load_episode_orders']()
        self.get.assert_not_called()
        self.assertFalse(self.ns['btn_episode_orders'].disabled)

    def test_show_without_alternate_orders_keeps_only_explicit_standard_choice(self):
        self.get.side_effect = lambda *args: {'results': []}
        self.ns['load_episode_orders']()
        self.assertEqual(self.ns['tmdb_episode_order'].options, [('Original Air Date (standard)', '')])
        self.ns['apply_episode_order']()
        self.assertNotIn('episode_group', self.tasks[0].tmdb_override)

    def test_failed_request_is_retryable_and_cannot_apply_partial_changes(self):
        self.ns['load_episode_orders']()
        self.ns['tmdb_episode_order'].value = 'split'
        self.get.side_effect = [None, GROUP, {'episodes': [{'episode_number': number} for number in range(1, 5)]}]
        self.ns['apply_episode_order']()
        self.assertTrue(all(task.tmdb_override is None for task in self.tasks))
        self.ns['apply_episode_order']()
        self.assertEqual(self.tasks[0].tmdb_override['episode_group']['id'], 'split')

    def test_session_rehydration_preserves_order_when_metadata_is_refreshed(self):
        self.apply_split()
        stored = json.loads(json.dumps(self.tasks[0].tmdb_override))
        task = SimpleNamespace(filename='resumed.mkv', tmdb_override=stored)
        self.ns.update(TMDB_CLEARED={'cleared': True}, _tmdb_match_is_current=lambda match: False,
                       _tmdb_fetch_by_id=lambda *args: dict(MATCH, metadata_version=2))
        self.ns['_apply_tmdb_overrides']([task])
        self.assertEqual(task.tmdb_override['episode_group'], stored['episode_group'])
        self.assertEqual(self.matches['resumed.mkv']['episode_group']['id'], 'split')

    def destination_subject(self, episode=4, end=None):
        subject = _load_destination_subject()
        for name in ('map_episode_order', 'map_episode_order_range'):
            subject[name] = self.ns[name]
        subject['episode_info']['value'].update(episode=episode, episode_end=end, has_sxe=False)
        subject['match']['value'] = dict(MATCH, episode_group=self.ns['build_episode_group'](GROUP))
        return subject

    def test_destination_and_range_use_selected_mapping(self):
        subject = self.destination_subject(5, 7)
        dest, _ = subject['determine_destination_path']('Show - 5-7.mkv', 'generic', dry_run=True)
        self.assertTrue(dest.endswith('Season 02/Example Show - S02E01-E03.mkv') or
                        dest.endswith('Season 02\\Example Show - S02E01-E03.mkv'))

    def test_missing_episode_stops_destination_instead_of_falling_back(self):
        subject = self.destination_subject(3)
        with self.assertRaisesRegex(ValueError, 'absent'):
            subject['determine_destination_path']('Show - 3.mkv', 'generic', dry_run=True)

    def test_standard_order_also_maps_range_end_and_rejects_cross_season_files(self):
        subject = self.destination_subject(5, 7)
        subject['match']['value'].pop('episode_group')
        subject['_map_absolute_episode'] = self.ns['_map_absolute_episode']
        subject['match']['value']['seasons'] = {'1': 4, '2': 3}
        dest, _ = subject['determine_destination_path']('Show - 5-7.mkv', 'generic', dry_run=True)
        self.assertIn('S02E01-E03', dest)
        subject['episode_info']['value'].update(episode=4, episode_end=5)
        with self.assertRaisesRegex(ValueError, 'crosses'):
            subject['determine_destination_path']('Show - 4-5.mkv', 'generic', dry_run=True)

    def test_explicit_season_absolute_mode_and_manual_overrides_keep_numbering(self):
        for change in ['explicit', 'absolute', 'season', 'episode']:
            subject = self.destination_subject(4)
            if change == 'explicit':
                subject['episode_info']['value']['has_sxe'] = True
            elif change == 'absolute':
                subject['episode_numbering_toggle'].value = 'Absolute'
            elif change == 'season':
                subject['get_season_override'] = lambda filename: 3
            else:
                subject['get_episode_override'] = lambda filename: 9
            dest, _ = subject['determine_destination_path']('Show - 4.mkv', 'generic', dry_run=True)
            expected = 'S03E04' if change == 'season' else 'S01E09' if change == 'episode' else 'S01E04'
            self.assertIn(expected, dest)

    def test_failed_mapping_is_visible_in_queue_preview(self):
        self.ns.update(_strip_size_suffix=lambda name: name, _split_subtitle_lang=lambda name: (name, ''),
                       determine_destination_path=Mock(side_effect=ValueError('Episode absent from selected order')))
        preview = self.ns['_queue_dest_preview'](SimpleNamespace(filename='Show - 3.mkv', source='generic'))
        self.assertEqual(preview, 'Review needed: Episode absent from selected order')


if __name__ == '__main__':
    unittest.main()
