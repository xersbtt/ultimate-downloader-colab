"""Focused regression tests for TMDB anime classification and routing.

``ultimate_downloader.py`` builds its notebook UI at import time, so this test
module loads only the pure classifier/routing function definitions from its AST
and supplies small test doubles for their runtime dependencies.
"""

import ast
import copy
import os
import re
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple


SOURCE_PATH = Path(__file__).with_name("ultimate_downloader.py")


def _function_nodes(*names):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"), filename=str(SOURCE_PATH))
    wanted = set(names)
    found = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    }
    missing = wanted - found.keys()
    if missing:
        raise AssertionError(f"Missing function(s) in ultimate_downloader.py: {sorted(missing)}")
    return [found[name] for name in names]


def _load_classifier():
    module = ast.Module(
        body=_function_nodes(
            "_tmdb_genre_ids",
            "_tmdb_country_codes",
            "_tmdb_keyword_names",
            "classify_tmdb_library",
        ),
        type_ignores=[],
    )
    namespace = {
        "TMDB_ANIMATION_GENRE_ID": 16,
        "TMDB_ANIME_KEYWORDS": {"anime", "japanese animation"},
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace["classify_tmdb_library"]


class TmdbAnimeClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classify = staticmethod(_load_classifier())

    def assertClassification(self, result, expected_class, expected_anime):
        classification = self.classify(result)
        self.assertEqual(
            set(classification),
            {"library_class", "is_anime", "anime_evidence"},
        )
        self.assertEqual(classification["library_class"], expected_class)
        self.assertIs(classification["is_anime"], expected_anime)
        self.assertIsInstance(classification["anime_evidence"], list)
        self.assertTrue(all(isinstance(item, str) for item in classification["anime_evidence"]))
        if expected_anime:
            self.assertTrue(classification["anime_evidence"])
        return classification

    def test_search_tv_animation_with_japanese_language_is_anime(self):
        self.assertClassification(
            {
                "genre_ids": [16, 10765],
                "original_language": "ja",
                "origin_country": ["JP"],
            },
            "anime",
            True,
        )

    def test_full_movie_animation_with_japanese_production_country_is_anime(self):
        self.assertClassification(
            {
                "genres": [{"id": 16, "name": "Animation"}, {"id": 14, "name": "Fantasy"}],
                "original_language": "en",
                "production_countries": [{"iso_3166_1": "JP", "name": "Japan"}],
            },
            "anime",
            True,
        )

    def test_exact_keyword_can_supply_the_second_anime_signal(self):
        for keyword_container in (
            {"keywords": [{"name": "Anime"}]},       # movie keyword response
            {"results": [{"name": "japanese animation"}]},  # TV keyword response
            [{"name": "  ANIME  "}],                # already-flattened details
        ):
            with self.subTest(keyword_container=keyword_container):
                self.assertClassification(
                    {
                        "genre_ids": [16],
                        "original_language": "en",
                        "keywords": keyword_container,
                    },
                    "anime",
                    True,
                )

    def test_animation_without_japanese_evidence_is_standard(self):
        self.assertClassification(
            {
                "genre_ids": [16, 35],
                "original_language": "en",
                "origin_country": ["US"],
                "production_countries": [{"iso_3166_1": "US"}],
            },
            "standard",
            False,
        )

    def test_japanese_live_action_is_standard_even_with_anime_keyword(self):
        self.assertClassification(
            {
                "genre_ids": [18, 10759],
                "original_language": "ja",
                "origin_country": ["JP"],
                "keywords": {"results": [{"name": "anime"}]},
            },
            "standard",
            False,
        )

    def test_near_match_keyword_does_not_create_anime_signal(self):
        self.assertClassification(
            {
                "genre_ids": [16],
                "original_language": "en",
                "keywords": {
                    "keywords": [
                        {"name": "anime-inspired"},
                        {"name": "anime adaptation"},
                        {"name": "animation"},
                    ]
                },
            },
            "standard",
            False,
        )

    def test_missing_genre_field_is_unknown(self):
        self.assertClassification(
            {"original_language": "ja", "origin_country": ["JP"]},
            "unknown",
            False,
        )

    def test_present_but_empty_genre_field_is_standard(self):
        self.assertClassification(
            {"genre_ids": [], "original_language": "ja", "origin_country": ["JP"]},
            "standard",
            False,
        )

    def test_classifier_does_not_mutate_tmdb_result(self):
        result = {
            "genres": [{"id": 16, "name": "Animation"}],
            "production_countries": [{"iso_3166_1": "JP"}],
            "keywords": {"keywords": [{"name": "anime"}]},
        }
        before = copy.deepcopy(result)

        self.assertClassification(result, "anime", True)

        self.assertEqual(result, before)


def _load_normalize_subject(details=None):
    module = ast.Module(
        body=_function_nodes(
            "_tmdb_genre_ids",
            "_tmdb_country_codes",
            "_tmdb_keyword_names",
            "classify_tmdb_library",
            "_tmdb_match_is_current",
            "_tmdb_normalize",
        ),
        type_ignores=[],
    )
    namespace = {
        "sanitize_filename": lambda value: value,
        "_tmdb_fetch_details": lambda _kind, _tmdb_id: details,
        "TMDB_ANIMATION_GENRE_ID": 16,
        "TMDB_ANIME_KEYWORDS": {"anime", "japanese animation"},
        "TMDB_METADATA_VERSION": 1,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class TmdbNormalizationTests(unittest.TestCase):
    def test_normalized_match_carries_anime_classification(self):
        subject = _load_normalize_subject(
            {"seasons": [{"season_number": 1, "episode_count": 12}]}
        )

        normalized = subject["_tmdb_normalize"](
            "tv",
            {
                "id": 37854,
                "name": "One Piece",
                "first_air_date": "1999-10-20",
                "genre_ids": [16, 10759],
                "original_language": "ja",
                "origin_country": ["JP"],
            },
        )

        self.assertEqual(normalized["library_class"], "anime")
        self.assertIs(normalized["is_anime"], True)
        self.assertTrue(normalized["anime_evidence"])
        self.assertEqual(normalized["metadata_version"], 1)
        self.assertTrue(subject["_tmdb_match_is_current"](normalized))
        self.assertEqual(normalized["seasons"], {"1": 12})

    def test_normalized_match_preserves_unknown_when_genres_are_unavailable(self):
        subject = _load_normalize_subject()

        normalized = subject["_tmdb_normalize"](
            "movie",
            {
                "id": 1,
                "title": "Unclassified Film",
                "release_date": "2024-01-01",
                "original_language": "ja",
            },
        )

        self.assertEqual(normalized["library_class"], "unknown")
        self.assertIs(normalized["is_anime"], False)
        self.assertEqual(normalized["anime_evidence"], [])
        self.assertEqual(normalized["metadata_version"], 0)
        self.assertFalse(subject["_tmdb_match_is_current"](normalized))

    def test_ambiguous_animated_movie_with_failed_details_stays_incomplete(self):
        subject = _load_normalize_subject(details=None)

        normalized = subject["_tmdb_normalize"](
            "movie",
            {
                "id": 2,
                "title": "Ambiguous Animation",
                "release_date": "2024-01-01",
                "genre_ids": [16],
                "original_language": "en",
            },
        )

        self.assertEqual(normalized["library_class"], "standard")
        self.assertIs(normalized["is_anime"], False)
        self.assertEqual(normalized["metadata_version"], 0)
        self.assertFalse(subject["_tmdb_match_is_current"](normalized))

    def test_successful_required_details_mark_classification_current(self):
        subject = _load_normalize_subject(
            {
                "genres": [{"id": 16, "name": "Animation"}],
                "production_countries": [{"iso_3166_1": "JP", "name": "Japan"}],
                "keywords": {"keywords": []},
            }
        )

        normalized = subject["_tmdb_normalize"](
            "movie",
            {
                "id": 3,
                "title": "Japanese Co-production",
                "release_date": "2024-01-01",
                "genre_ids": [16],
                "original_language": "en",
            },
        )

        self.assertEqual(normalized["library_class"], "anime")
        self.assertIs(normalized["is_anime"], True)
        self.assertEqual(normalized["metadata_version"], 1)
        self.assertTrue(subject["_tmdb_match_is_current"](normalized))


def _load_cache_subject(cache_path):
    module = ast.Module(
        body=_function_nodes("_tmdb_match_is_current", "_load_tmdb_query_cache"),
        type_ignores=[],
    )
    namespace = {
        "os": os,
        "json": json,
        "TMDB_CACHE_FILE": str(cache_path),
        # The classifier schema is versioned independently of search matching.
        # This deliberately matches the legacy file's search-cache version.
        "TMDB_CACHE_VERSION": 2,
        "TMDB_METADATA_VERSION": 1,
        "_tmdb_query_cache": {},
        "_tmdb_query_cache_loaded": False,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class TmdbCacheMigrationTests(unittest.TestCase):
    def test_old_success_without_classification_is_preserved_for_lazy_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "tmdb_cache.json"
            legacy = {
                "type": "tv",
                "id": 37854,
                "name": "One Piece",
                "year": "1999",
                "seasons": {"1": 8},
            }
            cache_path.write_text(
                json.dumps(
                    {
                        "__version__": 2,
                        "tv|one piece|": legacy,
                        "movie|missing|": None,
                    }
                ),
                encoding="utf-8",
            )
            subject = _load_cache_subject(cache_path)

            subject["_load_tmdb_query_cache"]()

            self.assertEqual(
                subject["_tmdb_query_cache"],
                {
                    "__version__": 2,
                    "tv|one piece|": legacy,
                    "movie|missing|": None,
                },
            )


def _load_search_subject(cached_match, fetch_by_id):
    module = ast.Module(
        body=_function_nodes("_tmdb_match_is_current", "_tmdb_search"),
        type_ignores=[],
    )
    namespace = {
        "Optional": Optional,
        "TMDB_METADATA_VERSION": 1,
        "TMDB_MATCH_THRESHOLD": 0.60,
        "_tmdb_query_cache": {"tv|one piece|": cached_match},
        "_load_tmdb_query_cache": lambda: None,
        "_tmdb_fetch_by_id": fetch_by_id,
        # The remaining search dependencies are deliberately explosive: a cache
        # hit must return before falling through to a fresh text search.
        "re": re,
        "_tmdb_get": lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit unexpectedly performed a text search")
        ),
        "_tmdb_similarity": lambda *_args: 0.0,
        "_tmdb_alt_titles": lambda *_args: [],
        "_tmdb_normalize": lambda *_args: (_ for _ in ()).throw(
            AssertionError("cache hit unexpectedly normalized a search result")
        ),
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class TmdbLazyCacheRefreshTests(unittest.TestCase):
    def setUp(self):
        self.legacy = {
            "type": "tv",
            "id": 37854,
            "name": "One Piece",
            "year": "1999",
            "seasons": {"1": 8},
        }
        self.current = {
            **self.legacy,
            "metadata_version": 1,
            "library_class": "anime",
            "is_anime": True,
            "anime_evidence": ["genre:animation", "language:ja"],
        }

    def test_legacy_hit_refreshes_by_id_once_and_replaces_cached_value(self):
        fetches = []

        def fetch_by_id(kind, tmdb_id):
            fetches.append((kind, tmdb_id))
            return self.current

        subject = _load_search_subject(copy.deepcopy(self.legacy), fetch_by_id)

        first = subject["_tmdb_search"]("tv", "One Piece", None)
        second = subject["_tmdb_search"]("tv", "One Piece", None)

        self.assertIs(first, self.current)
        self.assertIs(second, self.current)
        self.assertIs(subject["_tmdb_query_cache"]["tv|one piece|"], self.current)
        self.assertEqual(fetches, [("tv", 37854)])

    def test_failed_refresh_keeps_legacy_identity_and_retries_later(self):
        fetches = []

        def fetch_by_id(kind, tmdb_id):
            fetches.append((kind, tmdb_id))
            return None

        cached = copy.deepcopy(self.legacy)
        subject = _load_search_subject(cached, fetch_by_id)

        first = subject["_tmdb_search"]("tv", "One Piece", None)
        second = subject["_tmdb_search"]("tv", "One Piece", None)

        self.assertIs(first, cached)
        self.assertIs(second, cached)
        self.assertEqual(first["name"], "One Piece")
        self.assertIs(subject["_tmdb_query_cache"]["tv|one piece|"], cached)
        self.assertEqual(fetches, [("tv", 37854), ("tv", 37854)])

    def test_current_classified_hit_does_not_fetch(self):
        fetches = []
        subject = _load_search_subject(
            copy.deepcopy(self.current),
            lambda *args: fetches.append(args) or self.fail("current hit must not refresh"),
        )

        result = subject["_tmdb_search"]("tv", "One Piece", None)

        self.assertEqual(result, self.current)
        self.assertEqual(fetches, [])


def _load_override_subject(fetch_by_id):
    module = ast.Module(
        body=_function_nodes("_tmdb_match_is_current", "_apply_tmdb_overrides"),
        type_ignores=[],
    )
    namespace = {
        "TMDB_METADATA_VERSION": 1,
        "TMDB_CLEARED": {"cleared": True},
        "_tmdb_match_cache": {},
        "_tmdb_fetch_by_id": fetch_by_id,
        "_match_cache_key": lambda filename: filename.casefold(),
        "List": List,
        "DownloadTask": object,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class TmdbPersistedOverrideTests(unittest.TestCase):
    def test_shared_legacy_overrides_are_enriched_with_one_details_fetch(self):
        fetches = []
        upgraded = {
            "type": "tv",
            "id": 37854,
            "name": "One Piece",
            "year": "1999",
            "seasons": {"1": 8},
            "metadata_version": 1,
            "library_class": "anime",
            "is_anime": True,
            "anime_evidence": ["genre:animation", "language:ja"],
        }

        def fetch_by_id(kind, tmdb_id):
            fetches.append((kind, tmdb_id))
            return upgraded

        subject = _load_override_subject(fetch_by_id)
        legacy = {
            "type": "tv",
            "id": 37854,
            "name": "One Piece",
            "year": "1999",
            "seasons": {"1": 8},
        }
        tasks = [
            SimpleNamespace(filename="One.Piece.001.mkv", tmdb_override=copy.deepcopy(legacy)),
            SimpleNamespace(filename="One.Piece.002.mkv", tmdb_override=copy.deepcopy(legacy)),
        ]

        subject["_apply_tmdb_overrides"](tasks)

        self.assertEqual(fetches, [("tv", 37854)])
        self.assertIs(tasks[0].tmdb_override, upgraded)
        self.assertIs(tasks[1].tmdb_override, upgraded)
        self.assertIs(subject["_tmdb_match_cache"]["one.piece.001.mkv"], upgraded)
        self.assertIs(subject["_tmdb_match_cache"]["one.piece.002.mkv"], upgraded)

    def test_cleared_override_removes_cached_match_without_fetching(self):
        fetches = []
        subject = _load_override_subject(
            lambda *args: fetches.append(args) or self.fail("cleared override must not fetch")
        )
        subject["_tmdb_match_cache"].update(
            {
                "one.piece.001.mkv": {"id": 37854},
                "one.piece.002.mkv": {"id": 37854},
            }
        )
        tasks = [
            SimpleNamespace(filename="One.Piece.001.mkv", tmdb_override={"cleared": True}),
            SimpleNamespace(filename="One.Piece.002.mkv", tmdb_override={"cleared": True}),
        ]

        subject["_apply_tmdb_overrides"](tasks)

        self.assertEqual(fetches, [])
        self.assertEqual(subject["_tmdb_match_cache"], {})
        self.assertEqual(tasks[0].tmdb_override, {"cleared": True})
        self.assertEqual(tasks[1].tmdb_override, {"cleared": True})

    def test_failed_legacy_upgrade_is_still_deduplicated_within_the_batch(self):
        fetches = []

        def fetch_by_id(kind, tmdb_id):
            fetches.append((kind, tmdb_id))
            return None

        subject = _load_override_subject(fetch_by_id)
        legacy = {"type": "movie", "id": 129, "name": "Spirited Away", "year": "2001"}
        tasks = [
            SimpleNamespace(filename="Spirited.Away.mkv", tmdb_override=copy.deepcopy(legacy)),
            SimpleNamespace(filename="Spirited.Away.en.srt", tmdb_override=copy.deepcopy(legacy)),
        ]

        subject["_apply_tmdb_overrides"](tasks)

        self.assertEqual(fetches, [("movie", 129)])
        self.assertEqual(tasks[0].tmdb_override, legacy)
        self.assertEqual(tasks[1].tmdb_override, legacy)
        self.assertEqual(len(subject["_tmdb_match_cache"]), 2)


def _load_details_subject(tmdb_get):
    module = ast.Module(body=_function_nodes("_tmdb_fetch_details"), type_ignores=[])
    namespace = {
        "_tmdb_details_cache": {},
        "_tmdb_get": tmdb_get,
        "Optional": Optional,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class TmdbDetailsCacheTests(unittest.TestCase):
    def test_success_is_memoized_by_kind_and_stringified_id(self):
        calls = []

        def tmdb_get(path, params):
            calls.append((path, params))
            return {"id": 42, "title": "Result"}

        subject = _load_details_subject(tmdb_get)

        first = subject["_tmdb_fetch_details"]("movie", 42)
        second = subject["_tmdb_fetch_details"]("movie", "42")
        tv_result = subject["_tmdb_fetch_details"]("tv", 42)

        self.assertIs(first, second)
        self.assertEqual(tv_result, {"id": 42, "title": "Result"})
        self.assertEqual(
            calls,
            [
                ("/movie/42", {"append_to_response": "keywords"}),
                ("/tv/42", {"append_to_response": "keywords"}),
            ],
        )

    def test_failure_is_not_memoized_so_a_later_call_can_retry(self):
        calls = []

        def tmdb_get(path, params):
            calls.append((path, params))
            return None

        subject = _load_details_subject(tmdb_get)

        self.assertIsNone(subject["_tmdb_fetch_details"]("movie", 404))
        self.assertIsNone(subject["_tmdb_fetch_details"]("movie", 404))

        self.assertEqual(
            calls,
            [
                ("/movie/404", {"append_to_response": "keywords"}),
                ("/movie/404", {"append_to_response": "keywords"}),
            ],
        )


def _load_destination_subject():
    module = ast.Module(
        body=_function_nodes("resolve_library_is_anime", "determine_destination_path"),
        type_ignores=[],
    )
    route = {"value": None}
    match = {"value": None}
    forced_name = {"value": None}
    episode_info = {
        "value": {
            "part_suffix": "",
            "english_part_suffix": "",
            "show_name": "Example Show",
            "season": 1,
            "episode": 2,
            "episode_end": None,
            "is_tv": True,
            "episode_detected": True,
            "has_sxe": True,
        }
    }
    namespace = {
        "Optional": Optional,
        "Tuple": Tuple,
        "os": os,
        "re": re,
        "DRIVE_BASE": "/library/",
        "sanitize_filename": lambda filename: filename,
        "is_auto_organize_enabled": lambda: True,
        "get_route_override": lambda _filename: route["value"],
        "get_downloads_path": lambda: "Downloads",
        "get_anime_movies_path": lambda: "Anime Movies",
        "get_movie_path": lambda: "Movies",
        "get_youtube_path": lambda: "YouTube",
        "get_anime_series_path": lambda: "Anime Series",
        "get_tv_path": lambda: "TV Shows",
        "_ensure_dest_dir": lambda _path: None,
        "detect_episode_info": lambda _filename: dict(episode_info["value"]),
        "get_name_override": lambda _filename: forced_name["value"],
        "get_season_override": lambda _filename: None,
        "get_episode_override": lambda _filename: None,
        "get_episode_end_override": lambda _filename: None,
        "get_part_override": lambda _filename: None,
        "get_tmdb_match": lambda _filename: match["value"],
        "_map_absolute_episode": lambda episode, _seasons: (1, episode),
        "episode_numbering_toggle": type("Toggle", (), {"value": "Season match"})(),
        "clean_show_name": lambda value: value.replace(".", " ").strip(),
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    namespace["route"] = route
    namespace["match"] = match
    namespace["forced_name"] = forced_name
    namespace["episode_info"] = episode_info
    return namespace


class TmdbAnimeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.subject = _load_destination_subject()

    def _destination(self):
        return self.subject["determine_destination_path"]("Example.Show.S01E02.mkv", dry_run=True)

    def test_auto_route_uses_tmdb_anime_class_for_series(self):
        self.subject["match"]["value"] = {
            "type": "tv",
            "name": "Example Show",
            "year": "2024",
            "seasons": {},
            "library_class": "anime",
            "is_anime": True,
            "anime_evidence": ["original_language:ja"],
        }

        path, category = self._destination()

        self.assertEqual(category, "Anime Series")
        self.assertIn("Anime Series", path)

    def test_explicit_standard_route_overrides_tmdb_anime_class(self):
        self.subject["route"]["value"] = "tv"
        self.subject["match"]["value"] = {
            "type": "tv",
            "name": "Example Show",
            "year": "2024",
            "seasons": {},
            "library_class": "anime",
            "is_anime": True,
            "anime_evidence": ["original_language:ja"],
        }

        path, category = self._destination()

        self.assertEqual(category, "TV")
        self.assertIn("TV Shows", path)
        self.assertNotIn("Anime Series", path)

    def test_explicit_anime_route_overrides_standard_tmdb_class(self):
        self.subject["route"]["value"] = "anime_series"
        self.subject["match"]["value"] = {
            "type": "tv",
            "name": "Example Show",
            "year": "2024",
            "seasons": {},
            "library_class": "standard",
            "is_anime": False,
            "anime_evidence": [],
        }

        path, category = self._destination()

        self.assertEqual(category, "Anime Series")
        self.assertIn("Anime Series", path)

    def test_auto_route_uses_tmdb_anime_class_for_movies(self):
        self.subject["episode_info"]["value"].update(
            {
                "show_name": "Spirited Away",
                "is_tv": False,
                "episode_detected": False,
                "has_sxe": False,
            }
        )
        self.subject["match"]["value"] = {
            "type": "movie",
            "name": "Spirited Away",
            "year": "2001",
            "library_class": "anime",
            "is_anime": True,
            "anime_evidence": ["original_language:ja"],
        }

        path, category = self._destination()

        self.assertEqual(category, "Anime Movies")
        self.assertIn("Anime Movies", path)

    def test_forced_name_keeps_tmdb_anime_routing_signal(self):
        self.subject["forced_name"]["value"] = ("My Forced Name", "2020")
        self.subject["match"]["value"] = {
            "type": "tv",
            "name": "Ignored Canonical Name",
            "year": "2024",
            "seasons": {},
            "library_class": "anime",
            "is_anime": True,
            "anime_evidence": ["origin_country:JP"],
        }

        path, category = self._destination()

        self.assertEqual(category, "Anime Series")
        self.assertIn("Anime Series", path)
        self.assertIn("My Forced Name (2020)", path)
        self.assertNotIn("Ignored Canonical Name", path)

    def test_auto_route_keeps_existing_standard_fallback_without_tmdb_metadata(self):
        path, category = self._destination()

        self.assertEqual(category, "TV")
        self.assertIn("TV Shows", path)


if __name__ == "__main__":
    unittest.main()
