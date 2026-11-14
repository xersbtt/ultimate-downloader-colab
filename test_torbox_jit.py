"""Focused regression tests for TorBox just-in-time link resolution.

The notebook module builds its widget UI at import time, so these tests load only
the relevant function definitions from its AST and supply small test doubles for
their notebook/runtime dependencies.
"""

import ast
import os
import queue
import re
import threading
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse


@dataclass
class FakeTask:
    url: str
    filename: str
    source: str = "magnet"
    link_type: str = "tb_magnet_file"
    original_url: Optional[str] = None
    id: str = "task"
    status: str = "pending"
    error: Optional[str] = None
    cookie: Optional[str] = None


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _load_subject():
    source_path = Path(__file__).with_name("ultimate_downloader.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    wanted = {
        "_make_torrent_file_task",
        "_tb_folder_file_is_irrelevant",
        "_tb_file_in_extras_folder",
        "resolve_tb_folder_files",
        "_group_tasks_by_torrent",
        "_tb_defer_requestdl",
        "_tb_wait_requestdl_slot",
        "_tb_err_is_throttle",
        "_tb_requestdl",
        "_tb_fetch_item",
        "_tb_progress_pct",
        "resolve_tb_link",
        "convert_tb_tasks_to_parallel",
        "_tb_task_requestdl_parts",
        "_tb_task_download_url",
        "_tb_url_refresher",
        "download_worker",
    }
    definitions = [
        node for node in tree.body
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted)
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in {
                "KEEP_EXTENSIONS", "_TB_FOLDER_IGNORED_EXTENSIONS",
                "_TB_FOLDER_SAMPLE_MAX_BYTES", "_TB_ENDPOINT_BY_TYPE",
            } for target in node.targets))
    ]
    module = ast.Module(body=definitions, type_ignores=[])
    duplicate_skip = object()
    clock = FakeClock()
    namespace = {
        "Callable": Callable,
        "Dict": Dict,
        "DownloadTask": FakeTask,
        "List": List,
        "Optional": Optional,
        "Tuple": Tuple,
        "queue": queue,
        "os": os,
        "re": re,
        "parse_qs": parse_qs,
        "unquote": unquote,
        "urlparse": urlparse,
        "_TB_REQUESTDL_ID_PARAM": {
            "torrents": "torrent_id",
            "usenet": "usenet_id",
            "webdl": "web_id",
        },
        "_cancel_requested": False,
        "cancel_requested": lambda: False,
        "time": clock,
        "TB_REQUESTDL_MIN_INTERVAL_SECS": 1.0,
        "TB_REQUESTDL_429_COOLDOWN_SECS": 60.0,
        "_tb_requestdl_rate_lock": threading.Lock(),
        "_tb_requestdl_next_at": 0.0,
        "TORBOX_API_BASE": "https://api.torbox.test/v1/api",
        "_get_tb_headers": lambda key: {"Authorization": f"Bearer {key}"},
        "_tb_extract_download_url": lambda data: data.get("data", ""),
        "_strip_size_suffix": lambda name: name.rsplit(" (", 1)[0] if name.endswith(" MB)") else name,
        "_tb_contextual_filename": lambda name, item: os.path.basename(name.replace('\\', '/')),
        "token_tb": type("Token", (), {"value": "widget-key"})(),
        "print_lock": threading.Lock(),
        "progress_lock": threading.Lock(),
        "progress_bar": SimpleNamespace(value=0, description="", bar_style=""),
        "sanitize_filename": lambda name: name,
        "_reset_progress_bar": Mock(),
        "COLAB_ROOT": "/content/",
        "DUPLICATE_SKIP": duplicate_skip,
        "download_stats": {},
        "handle_file_processing": lambda *_args, **_kwargs: None,
        "print": lambda *_args, **_kwargs: None,
        "clock": clock,
    }
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace


class TorBoxJustInTimeTests(unittest.TestCase):
    def setUp(self):
        self.subject = _load_subject()

    def test_webdl_sends_multipart_link_and_matches_numeric_poll_id(self):
        post = Mock()
        post.return_value.json.return_value = {
            "success": True, "data": {"webdownload_id": "42"}
        }
        get = Mock()
        get.return_value.json.return_value = {
            "success": True,
            "data": [{"id": 42, "name": "Example.mkv", "download_finished": True,
                      "files": [{"id": 7}]}],
        }
        self.subject["requests"] = SimpleNamespace(post=post, get=get)
        request_link = Mock(return_value=("https://cdn.test/video.mkv", ""))
        self.subject["_tb_requestdl"] = request_link
        url = "https://example.test/Example.mkv"

        result = self.subject["resolve_tb_link"](url, "test-key")

        self.assertEqual(result, [("https://cdn.test/video.mkv", "Example.mkv")])
        self.assertEqual(post.call_args.kwargs["files"], {"link": (None, url)})
        request_link.assert_called_once_with("webdl", "web_id", "42", 7, "test-key")

    def test_folder_filters_unwanted_files_before_queueing(self):
        mib = 1024 * 1024
        files = [
            ("Release/episode.mkv", 500 * mib),
            ("Release/track.FLAC", 200 * mib),
            ("Release/cover.JPG", 5 * mib),
            ("Release/show_SAMPLE.mkv", 99 * mib),
            ("Release/sample-clip.mp4", 10 * mib),
            ("Release/Sample.Movie.mkv", 100 * mib),
            ("Release/Resampled.mkv", 10 * mib),
            ("Release/sample.SRT", 1024),
            ("Sample Release/episode2.mkv", 10 * mib),
            ("Release/readme.nfo", 1024),
        ]
        self.subject["_tb_fetch_item"] = lambda *_args: {
            "name": "Release", "files": [
                {"id": index, "name": name, "size": size}
                for index, (name, size) in enumerate(files)
            ],
        }
        for kind, group in [("torrents", "42"), ("usenet", "usenet/42"),
                            ("webdl", "webdl/42")]:
            with self.subTest(kind=kind):
                tasks = self.subject["resolve_tb_folder_files"](
                    f"https://torbox.app/download?id=42&type={kind}", "secret")
                self.assertEqual([task.original_url for task in tasks],
                                 [f"{group}:{i}" for i in (0, 5, 6, 7, 8)])

    def test_folder_ignores_common_audio_and_image_formats_at_any_size(self):
        ignored = self.subject["_tb_folder_file_is_irrelevant"]
        for ext in ("mp3", "m4a", "wav", "aac", "ogg", "opus", "mka",
                    "png", "jpeg", "webp", "gif", "avif", "heic", "svg"):
            with self.subTest(extension=ext):
                self.assertTrue(ignored(f"extra.{ext.upper()}", 500 * 1024 * 1024))

    def _tasks(self, count=153):
        return [
            FakeTask(
                url="magnet:?xt=stable",
                filename=f"episode-{index:03d}.mkv (10.0 MB)",
                original_url=f"42:{index}",
                id=f"task-{index}",
            )
            for index in range(count)
        ]

    def test_cached_large_folder_is_queued_without_prefetching_links(self):
        request_calls = []
        self.subject["_tb_fetch_item"] = lambda *_args: {"download_state": "cached"}
        self.subject["_tb_requestdl"] = lambda *args, **kwargs: request_calls.append((args, kwargs))

        tasks = self._tasks()
        ready, sequential = self.subject["convert_tb_tasks_to_parallel"](tasks, "secret")

        self.assertEqual(len(ready), 153)
        self.assertEqual(sequential, [])
        self.assertEqual(request_calls, [])
        self.assertTrue(all(task.url == "magnet:?xt=stable" for task in ready))
        self.assertTrue(all(task.filename.endswith(".mkv") for task in ready))

    def test_uncached_folder_keeps_existing_sequential_cache_wait(self):
        self.subject["_tb_fetch_item"] = lambda *_args: {"download_state": "downloading"}
        tasks = self._tasks(3)

        ready, sequential = self.subject["convert_tb_tasks_to_parallel"](tasks, "secret")

        self.assertEqual(ready, [])
        self.assertEqual(sequential, tasks)

    def test_request_slots_are_globally_spaced_and_share_cooldowns(self):
        wait_for_slot = self.subject["_tb_wait_requestdl_slot"]
        clock = self.subject["clock"]

        self.assertTrue(wait_for_slot())
        self.assertTrue(wait_for_slot())
        self.assertEqual(sum(clock.sleeps), 1.0)

        self.subject["_tb_defer_requestdl"](60.0)
        before = clock.now
        self.assertTrue(wait_for_slot())
        self.assertEqual(clock.now - before, 60.0)

    def test_429_without_retry_after_uses_shared_sixty_second_cooldown(self):
        class Response:
            def __init__(self, status, data=None):
                self.status_code = status
                self.headers = {}
                self.text = ""
                self._data = data

            def json(self):
                if self._data is None:
                    raise ValueError("non-JSON")
                return self._data

        responses = [Response(429), Response(200, {"data": "https://store.tb-cdn.io/ok"})]
        calls = []

        class Requests:
            @staticmethod
            def get(*args, **kwargs):
                calls.append((args, kwargs))
                return responses.pop(0)

        self.subject["requests"] = Requests
        clock = self.subject["clock"]
        started = clock.now

        url, error = self.subject["_tb_requestdl"](
            "torrents", "torrent_id", 42, 7, "secret", attempts=2
        )

        self.assertEqual(url, "https://store.tb-cdn.io/ok")
        self.assertEqual(error, "")
        self.assertEqual(len(calls), 2)
        self.assertEqual(clock.now - started, 60.0)

    def test_worker_mints_initial_link_only_when_it_starts(self):
        request_calls = []
        aria_calls = []

        def requestdl(*args, **kwargs):
            request_calls.append((args, kwargs))
            return f"https://store.tb-cdn.io/link-{len(request_calls)}", ""

        def download(url, filename, root, cookie, **kwargs):
            aria_calls.append((url, filename, root, cookie, kwargs))
            return self.subject["DUPLICATE_SKIP"]

        self.subject["_tb_requestdl"] = requestdl
        self.subject["download_with_aria2"] = download
        task = self._tasks(1)[0]

        result = self.subject["download_worker"](task, "", None, "explicit-key")

        self.assertEqual(result.status, "skipped")
        self.assertEqual(task.url, "magnet:?xt=stable")
        self.assertEqual(len(request_calls), 1)
        self.assertEqual(request_calls[0][0], ("torrents", "torrent_id", "42", "0", "explicit-key"))
        self.assertEqual(request_calls[0][1], {"attempts": 5})
        self.assertEqual(aria_calls[0][0], "https://store.tb-cdn.io/link-1")
        self.assertEqual(aria_calls[0][1], "episode-000.mkv")

        refreshed = aria_calls[0][4]["url_refresh"]()
        self.assertEqual(refreshed, "https://store.tb-cdn.io/link-2")
        self.assertEqual(request_calls[1][1], {"attempts": 2})

    def test_link_resolution_failure_does_not_start_aria2(self):
        self.subject["_tb_requestdl"] = lambda *_args, **_kwargs: ("", "TorBox HTTP 429")
        self.subject["download_with_aria2"] = lambda *_args, **_kwargs: self.fail("aria2 should not start")
        task = self._tasks(1)[0]

        result = self.subject["download_worker"](task, "", None, "explicit-key")

        self.assertEqual(result.status, "failed")
        self.assertIn("TorBox HTTP 429", result.error)


if __name__ == "__main__":
    unittest.main()
