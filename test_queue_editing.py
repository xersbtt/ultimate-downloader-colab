"""Queue edit regressions, loading notebook definitions without running Colab setup."""

import ast
import copy
import os
import re
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import unquote
from uuid import uuid4


class SelectionWidget:
    """Model SelectMultiple clearing its selection when option labels change."""

    def __init__(self):
        self.value = ()
        self._options = ()

    @property
    def options(self):
        return self._options

    @options.setter
    def options(self, values):
        self._options = tuple(values)
        self.value = ()


class QueueEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).with_name('ultimate_downloader.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        names = {
            'DownloadTask', 'update_queue_display', '_selected_queue_indices',
            '_strip_size_suffix', '_split_subtitle_lang', '_match_cache_key',
            'sanitize_filename', '_number_rows_sequentially', '_parse_episode_range',
            '_set_episode_overrides', '_apply_queue_overrides', 'apply_queue_changes',
            'apply_tmdb_override', 'clear_tmdb_override', 'apply_season_override',
            'clear_season_override', 'apply_renumber', 'clear_renumber',
            'apply_name_override', 'clear_name_override', 'apply_part_override',
            'remove_part_override', 'apply_route_override', 'clear_route_override',
            'queue_sort_alpha', 'queue_remove_selected',
        }
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                 and n.name in names]
        assert {n.name for n in nodes} == names
        cls.code = compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec')

    def setUp(self):
        self.ns = dict(os=os, re=re, unquote=unquote, List=List, Dict=Dict, Tuple=Tuple,
                       Optional=Optional, dataclass=dataclass, field=field, uuid4=uuid4,
                       KEEP_EXTENSIONS={'.srt', '.ass', '.sub', '.vtt'},
                       TMDB_CLEARED={'cleared': True}, queue_list=SelectionWidget(),
                       _queue_dest_preview=lambda task: str((task.name_override, task.season_override,
                                                           task.episode_override)),
                       get_tmdb_match=lambda name: None, tmdb_is_enabled=lambda: True,
                       tmdb_enabled_checkbox=SimpleNamespace(value=True), print=Mock(),
                       queue_sort_ascending=True, btn_queue_sort=SimpleNamespace(),
                       hide_queue=Mock())
        for name in ('tmdb_match', 'season_override', 'episode_override',
                     'episode_end_override', 'name_override', 'part_override', 'route_override'):
            self.ns[f'_{name}_cache'] = {}
        for name in ('tmdb_override_input', 'queue_name_input', 'queue_year_input',
                     'season_override_input', 'renumber_start_input', 'part_override_input'):
            self.ns[name] = SimpleNamespace(value='')
        self.ns['route_dropdown'] = SimpleNamespace(value='tv', options=[('TV Series', 'tv')])
        self.match = {'id': 1, 'type': 'tv', 'name': 'Matched Show', 'year': '2025'}
        self.ns['_resolve_tmdb_override'] = Mock(return_value=self.match)
        exec(self.code, self.ns)
        self.rows = [self.ns['DownloadTask']('https://example.test/file', filename, 'test', 'direct')
                     for filename in ('Other.mkv', 'Show - 28.mkv', 'Show - 28.en.srt',
                                      'Show - 29.mkv', 'Unselected.mkv')]
        self.ns['pending_queue'] = self.rows
        self.ns['update_queue_display'](preserve_selection=False)
        self.select(1, 2, 3)

    def select(self, *indices):
        self.ns['queue_list'].value = tuple(self.ns['queue_list'].options[i] for i in indices)

    def test_every_individual_edit_and_clear_keeps_selection(self):
        operations = [
            ('apply_tmdb_override', 'tmdb_override_input', 'tv:1'),
            ('clear_tmdb_override', None, None),
            ('apply_name_override', 'queue_name_input', 'New Show'),
            ('clear_name_override', None, None),
            ('apply_season_override', 'season_override_input', '2'),
            ('clear_season_override', None, None),
            ('apply_renumber', 'renumber_start_input', '7-9'),
            ('clear_renumber', None, None),
            ('apply_part_override', 'part_override_input', '1'),
            ('remove_part_override', None, None),
            ('apply_route_override', None, None),
            ('clear_route_override', None, None),
        ]
        for handler, widget, value in operations:
            with self.subTest(handler=handler):
                if widget:
                    self.ns[widget].value = value
                self.ns[handler]()
                self.assertEqual(self.ns['_selected_queue_indices'](), [1, 2, 3])

    def test_combined_edit_changes_only_selection_and_pairs_subtitle_ranges(self):
        self.ns['queue_name_input'].value = 'Correct Show'
        self.ns['queue_year_input'].value = '2025'
        self.ns['season_override_input'].value = '0'
        self.ns['renumber_start_input'].value = '7-9'
        self.ns['part_override_input'].value = '2'
        self.ns['tmdb_override_input'].value = 'tv:1'
        before = copy.deepcopy([vars(self.rows[0]), vars(self.rows[4])])
        refresh = Mock(wraps=self.ns['update_queue_display'])
        self.ns['update_queue_display'] = refresh
        self.ns['apply_queue_changes']()
        refresh.assert_called_once()
        self.assertEqual(self.ns['_selected_queue_indices'](), [1, 2, 3])
        self.assertEqual([vars(self.rows[0]), vars(self.rows[4])], before)
        for row, episode, end, part in zip(self.rows[1:4], (7, 7, 10), (9, 9, None), (2, 2, 3)):
            self.assertEqual((row.name_override, row.year_override, row.season_override),
                             ('Correct Show', '2025', 0))
            self.assertEqual((row.episode_override, row.episode_end_override, row.part_override),
                             (episode, end, part))
            key = self.ns['_match_cache_key'](row.filename)
            self.assertEqual(self.ns['_episode_override_cache'][key], episode)
            self.assertEqual(self.ns['_name_override_cache'][key], ('Correct Show', '2025'))
            self.assertEqual(self.ns['_tmdb_match_cache'][key], self.match)
        self.assertEqual(self.ns['queue_name_input'].value, '')

    def test_invalid_field_does_not_apply_any_changes_or_clear_drafts(self):
        for widget, value in [('queue_year_input', '202x'), ('season_override_input', '-1'),
                              ('renumber_start_input', '9-7'), ('renumber_start_input', 'abc'),
                              ('part_override_input', '0')]:
            with self.subTest(widget=widget, value=value):
                self.ns['queue_name_input'].value = 'Draft Show'
                self.ns[widget].value = value
                before = copy.deepcopy([vars(row) for row in self.rows])
                self.ns['apply_queue_changes']()
                self.assertEqual([vars(row) for row in self.rows], before)
                self.assertEqual(self.ns['_name_override_cache'], {})
                self.assertEqual(self.ns['queue_name_input'].value, 'Draft Show')
                self.assertEqual(self.ns[widget].value, value)
                self.assertEqual(self.ns['_selected_queue_indices'](), [1, 2, 3])
                self.ns[widget].value = ''

    def test_failed_match_leaves_other_edits_pending(self):
        self.ns['queue_name_input'].value = 'Draft'
        self.ns['tmdb_override_input'].value = 'Missing Show'
        self.ns['_resolve_tmdb_override'].return_value = None
        self.ns['apply_queue_changes']()
        self.assertTrue(all(t.name_override is None for t in self.rows))
        self.assertEqual(self.ns['queue_name_input'].value, 'Draft')

    def test_blank_fields_keep_existing_overrides_and_do_not_default_to_episode_one(self):
        row = self.rows[1]
        row.year_override, row.episode_override, row.route_override = '2020', 12, 'anime_series'
        row.part_override, row.season_override = 3, 4
        self.ns['queue_name_input'].value = 'New Show'
        self.ns['apply_queue_changes']()
        self.assertEqual((row.year_override, row.episode_override, row.route_override,
                          row.part_override, row.season_override), ('2020', 12, 'anime_series', 3, 4))

    def test_empty_selection_stays_empty_and_new_queue_selects_all(self):
        self.select()
        self.ns['queue_name_input'].value = 'Draft'
        self.ns['apply_queue_changes']()
        self.ns['update_queue_display']()
        self.ns['queue_sort_alpha']()
        self.assertEqual(self.ns['queue_list'].value, ())
        self.assertTrue(all(t.name_override is None for t in self.rows))
        self.ns['update_queue_display'](preserve_selection=False)
        self.assertEqual(len(self.ns['queue_list'].value), 5)

    def test_removal_does_not_select_replacement_rows(self):
        self.select(1)
        self.ns['queue_remove_selected']()
        self.assertEqual(len(self.ns['pending_queue']), 4)
        self.assertEqual(self.ns['queue_list'].value, ())

    def test_unresolved_filename_prevents_partial_combined_edit(self):
        self.rows[1].filename = ''
        self.ns['queue_name_input'].value = 'Draft'
        self.ns['renumber_start_input'].value = '2'
        self.ns['apply_queue_changes']()
        self.assertTrue(all(t.name_override is None and t.episode_override is None for t in self.rows))

    def test_plain_renumber_replaces_previous_range(self):
        self.ns['renumber_start_input'].value = '7-9'
        self.ns['apply_renumber']()
        self.ns['renumber_start_input'].value = '1'
        self.ns['apply_queue_changes']()
        self.assertEqual([t.episode_override for t in self.rows[1:4]], [1, 1, 2])
        self.assertTrue(all(t.episode_end_override is None for t in self.rows))
        self.assertEqual(self.ns['_episode_end_override_cache'], {})


if __name__ == '__main__':
    unittest.main()
