"""Local tests for the standalone Drive renamer; no Colab or TMDB access needed."""

import tempfile
import unittest
from pathlib import Path

from rename_existing_files import (
    RenameOptions, TMDBClient, absolute_to_season, apply_renames, drive_folder, plan_renames,
    season_to_absolute,
)


class StubTMDB(TMDBClient):
    def __init__(self, match):
        super().__init__('test-key')
        self.match = match

    def search(self, kind, title, year=''):
        return self.match


MATCH = {
    'type': 'tv', 'id': 123, 'name': 'Example Show', 'year': '2020',
    'seasons': {1: 12, 2: 10}, 'is_anime': False,
}


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'Incoming'
        self.source.mkdir()
        self.folders = {
            'tv': 'TV Shows', 'movie': 'Movies',
            'anime_series': 'Anime Series', 'anime_movie': 'Anime Movies',
        }

    def tearDown(self):
        self.temp.cleanup()

    def options(self, **kwargs):
        return RenameOptions(source=self.source, folders=self.folders, **kwargs)

    def test_season_to_absolute_moves_video_and_subtitle(self):
        season = self.source / 'Example Show (2020)' / 'Season 02'
        season.mkdir(parents=True)
        video = season / 'Example.Show.S02E03.mkv'
        subtitle = season / 'Example.Show.S02E03.en.srt'
        video.write_bytes(b'video')
        subtitle.write_text('subtitle')
        rows = plan_renames(self.options(numbering='Season to absolute'),
                            StubTMDB(MATCH), self.root)
        self.assertEqual([row.status for row in rows], ['Ready', 'Ready'])
        destination = self.root / 'TV Shows' / 'Example Show (2020)' / 'Season 01'
        self.assertEqual({row.destination.name for row in rows},
                         {'Example Show - S01E15.mkv', 'Example Show - S01E15.en.srt'})
        self.assertEqual(apply_renames(rows, {video, subtitle}, self.root)['Moved'], 2)
        self.assertTrue((destination / 'Example Show - S01E15.mkv').exists())
        self.assertTrue((destination / 'Example Show - S01E15.en.srt').exists())
        self.assertFalse(video.exists())

    def test_absolute_to_season_moves_video_and_subtitle(self):
        season = self.source / 'Example Show (2020)' / 'Season 01'
        season.mkdir(parents=True)
        video = season / 'Example Show - S01E15.mkv'
        subtitle = season / 'Example Show - S01E15.en.srt'
        video.write_bytes(b'video')
        subtitle.write_text('subtitle')
        rows = plan_renames(self.options(numbering='Absolute to season'),
                            StubTMDB(MATCH), self.root)
        self.assertEqual([row.status for row in rows], ['Ready', 'Ready'])
        destination = self.root / 'TV Shows' / 'Example Show (2020)' / 'Season 02'
        self.assertEqual({row.destination.name for row in rows},
                         {'Example Show - S02E03.mkv', 'Example Show - S02E03.en.srt'})
        self.assertEqual(apply_renames(rows, {video, subtitle}, self.root)['Moved'], 2)
        self.assertTrue((destination / 'Example Show - S02E03.mkv').exists())
        self.assertTrue((destination / 'Example Show - S02E03.en.srt').exists())

    def test_absolute_to_season_accepts_bare_number(self):
        video = self.source / 'Example.Show.-.15.mkv'
        video.write_bytes(b'video')
        rows = plan_renames(self.options(numbering='Absolute to season'),
                            StubTMDB(MATCH), self.root)
        self.assertEqual(rows[0].destination.name, 'Example Show - S02E03.mkv')

    def test_absolute_to_season_requires_known_counts(self):
        with self.assertRaises(ValueError):
            absolute_to_season(23, MATCH['seasons'], strict=True)
        with self.assertRaises(ValueError):
            absolute_to_season(13, {1: 12, 3: 10}, strict=True)
        video = self.source / 'Example.Show.S01E23.mkv'
        video.write_bytes(b'video')
        rows = plan_renames(self.options(numbering='Absolute to season'),
                            StubTMDB(MATCH), self.root)
        self.assertEqual(rows[0].status, 'Skipped')

    def test_absolute_range_across_seasons_is_skipped(self):
        video = self.source / 'Example.Show.S01E12-E13.mkv'
        video.write_bytes(b'video')
        rows = plan_renames(self.options(numbering='Absolute to season'),
                            StubTMDB(MATCH), self.root)
        self.assertEqual(rows[0].status, 'Skipped')
        self.assertIn('crosses', rows[0].detail)

    def test_downloader_style_keeps_explicit_season(self):
        video = self.source / 'Example.Show.S02E03.mkv'
        video.write_bytes(b'video')
        rows = plan_renames(self.options(), StubTMDB(MATCH), self.root)
        self.assertEqual(rows[0].destination.name, 'Example Show - S02E03.mkv')
        self.assertEqual(rows[0].destination.parent.name, 'Season 02')

    def test_season_folder_selection_uses_parent_show_name(self):
        season = self.source / 'Example Show (2020)' / 'Season 02'
        season.mkdir(parents=True)
        video = season / '03.mkv'
        video.write_bytes(b'video')
        options = RenameOptions(source=season, folders=self.folders,
                                numbering='Season to absolute')
        rows = plan_renames(options, StubTMDB(MATCH), self.root)
        self.assertEqual(rows[0].status, 'Ready')
        self.assertEqual(rows[0].destination.name, 'Example Show - S01E15.mkv')

    def test_generic_source_is_not_used_as_show_name(self):
        video = self.source / '03.mkv'
        video.write_bytes(b'video')
        rows = plan_renames(self.options(), StubTMDB(None), self.root)
        self.assertEqual(rows[0].status, 'Skipped')
        self.assertIn('name could not be identified', rows[0].detail)

    def test_missing_counts_and_specials_are_not_guessed(self):
        with self.assertRaises(ValueError):
            season_to_absolute(3, 1, {1: 12})
        with self.assertRaises(ValueError):
            season_to_absolute(0, 1, {1: 12})
        video = self.source / 'Example.Show.S04E01.mkv'
        video.write_bytes(b'video')
        rows = plan_renames(self.options(numbering='Season to absolute'),
                            StubTMDB(MATCH), self.root)
        self.assertEqual(rows[0].status, 'Skipped')

    def test_existing_destination_and_duplicate_plan_are_blocked(self):
        first = self.source / 'Example.Show.S02E03.mkv'
        second = self.source / 'Example Show - S02E03.mp4'
        first.write_bytes(b'a')
        second.write_bytes(b'b')
        destination = self.root / 'TV Shows' / 'Example Show (2020)' / 'Season 02'
        destination.mkdir(parents=True)
        (destination / 'Example Show - S02E03.mkv').write_bytes(b'existing')
        rows = plan_renames(self.options(), StubTMDB(MATCH), self.root)
        self.assertEqual({row.source.name: row.status for row in rows},
                         {'Example.Show.S02E03.mkv': 'Conflict',
                          'Example Show - S02E03.mp4': 'Ready'})
        self.assertEqual((destination / 'Example Show - S02E03.mkv').read_bytes(), b'existing')

    def test_two_sources_targeting_same_name_conflict(self):
        first = self.source / 'Example.Show.S02E03.mkv'
        second = self.source / 'Example.Show.2x03.mkv'
        first.write_bytes(b'a')
        second.write_bytes(b'b')
        rows = plan_renames(self.options(), StubTMDB(MATCH), self.root)
        self.assertEqual([row.status for row in rows], ['Conflict', 'Conflict'])

    def test_changed_source_is_not_moved(self):
        video = self.source / 'Example.Show.S02E03.mkv'
        video.write_bytes(b'first')
        rows = plan_renames(self.options(), StubTMDB(MATCH), self.root)
        video.write_bytes(b'changed')
        result = apply_renames(rows, {video}, self.root)
        self.assertEqual(result['Failed'], 1)
        self.assertTrue(video.exists())

    def test_folder_cannot_escape_drive(self):
        with self.assertRaises(ValueError):
            drive_folder(self.root, '../outside')


if __name__ == '__main__':
    unittest.main()
