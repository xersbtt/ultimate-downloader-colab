"""Exercise Drive cancellation without importing the notebook's UI or Google SDK."""

import ast
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Optional
from unittest.mock import Mock, patch


class DriveCancellationTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("ultimate_downloader.py")
        wanted = {"_drive_api_upload", "_move_with_progress_impl", "move_with_progress",
                  "_drive_mover", "download_worker", "cancel_requested"}
        tree = ast.parse(source.read_text(encoding="utf-8"))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        self.assertEqual({n.name for n in nodes}, wanted)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.src = Path(self.temp.name, "video.mkv")
        self.src.write_bytes(b"complete local video")
        self.dest = str(Path(self.temp.name, "drive", "video.mkv"))
        self.media = Mock()
        self.request = Mock()
        service = Mock()
        service.files.return_value.create.return_value = self.request
        self.ns = dict(os=os, queue=queue, time=time, Optional=Optional,
                       DownloadTask=SimpleNamespace, _cancel_requested=False,
                       DRIVE_BASE=str(Path(self.temp.name, "drive")),
                       DRIVE_API_CHUNK_MB=8, DRIVE_API_MAX_ATTEMPTS=3,
                       _drive_folder_id=Mock(return_value="folder"),
                       _drive_clear_existing=Mock(), _drive_service=lambda: service,
                       _use_drive_api=lambda: True, _mark_drive_uploaded=Mock(),
                       _warn_api_fallback=Mock(), _record_drive_transfer=Mock(),
                       _disk_guard_lock=threading.Lock(), _moves_in_flight=0,
                       print_lock=threading.Lock(), _tb_url_refresher=Mock(return_value=None),
                       download_with_aria2=Mock(return_value=str(self.src)),
                       COLAB_ROOT=self.temp.name, DUPLICATE_SKIP=object(), download_stats={})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)
        self.ns["handle_file_processing"] = lambda path, source: self.ns["move_with_progress"](path, self.dest)
        sdk = ModuleType("googleapiclient")
        http = ModuleType("googleapiclient.http")
        http.MediaFileUpload = Mock(return_value=self.media)
        sdk.http = http
        modules = patch.dict(sys.modules, {"googleapiclient": sdk, "googleapiclient.http": http})
        modules.start()
        self.addCleanup(modules.stop)

    def task(self):
        return SimpleNamespace(status="moving", error=None, source="generic", url="url",
                               filename="video.mkv", link_type="direct", cookie=None, id="task")

    def rate_limit_then_cancel(self):
        self.ns["_cancel_requested"] = True
        raise RuntimeError("403 User rate limit exceeded")

    def assert_preserved(self):
        self.assertEqual(self.src.read_bytes(), b"complete local video")
        self.assertFalse(Path(self.dest).exists())
        self.assertEqual(self.ns["_moves_in_flight"], 0)
        self.ns["_warn_api_fallback"].assert_not_called()
        self.ns["_mark_drive_uploaded"].assert_not_called()
        self.ns["_record_drive_transfer"].assert_not_called()

    def test_rate_limit_interrupt_saves_tasks_and_consumes_sentinel(self):
        self.request.next_chunk.side_effect = self.rate_limit_then_cancel
        active, queued = self.task(), self.task()
        moves = queue.Queue()
        for item in ((active, str(self.src)), (queued, str(self.src)), None):
            moves.put(item)
        saved = []
        escaped = []
        with patch.object(threading, "excepthook", side_effect=escaped.append):
            worker = threading.Thread(target=self.ns["_drive_mover"],
                                      args=(moves, lambda: saved.append((active.status, queued.status))),
                                      daemon=True)
            worker.start()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(escaped, [])
        self.assertEqual(moves.unfinished_tasks, 0)
        self.assertEqual(len(saved), 2)
        for task in (active, queued):
            self.assertEqual((task.status, task.error), ("failed", "Cancelled by user"))
        self.request.next_chunk.assert_called_once()
        self.media.stream.return_value.close.assert_called_once()
        self.assert_preserved()

    def test_inline_upload_cancellation_returns_retryable_task(self):
        self.request.next_chunk.side_effect = self.rate_limit_then_cancel
        task = self.task()
        self.assertIs(self.ns["download_worker"](task, ""), task)
        self.assertEqual((task.status, task.error), ("failed", "Cancelled by user"))
        self.assert_preserved()

    def test_cancellation_between_chunks_is_handled(self):
        def chunk():
            self.ns["_cancel_requested"] = True
            return None, None
        self.request.next_chunk.side_effect = chunk
        task = self.task()
        self.ns["download_worker"](task, "")
        self.assertEqual(task.error, "Cancelled by user")
        self.assert_preserved()

    def test_success_still_completes_and_removes_local_file(self):
        self.request.next_chunk.return_value = (None, {"id": "uploaded", "size": self.src.stat().st_size})
        task = self.task()
        self.ns["download_worker"](task, "")
        self.assertEqual(task.status, "done")
        self.assertFalse(self.src.exists())
        self.ns["_record_drive_transfer"].assert_called_once()

    def test_unrelated_keyboard_interrupt_still_propagates(self):
        self.ns["handle_file_processing"] = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.ns["download_worker"](self.task(), "")


if __name__ == "__main__":
    unittest.main()
