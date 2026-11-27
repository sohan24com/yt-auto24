import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from googleapiclient.errors import HttpError

from src import common, drive, youtube
from tools import get_tokens


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _DriveFiles:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.updated = []
        self.created = []
        self.deleted = []

    def list(self, **kwargs):
        return _Request({"files": self.existing})

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return _Request({})

    def delete(self, **kwargs):
        self.deleted.append(kwargs)
        return _Request({})

    def create(self, **kwargs):
        self.created.append(kwargs)
        return _UploadRequest()


class _UploadRequest:
    def next_chunk(self, num_retries=0):
        return None, {"id": "new-drive-id"}


class _DriveService:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


class IntegrationSafetyTests(unittest.TestCase):
    def test_memory_writes_are_atomic_and_corrupt_records_do_not_break_loading(self):
        with tempfile.TemporaryDirectory() as td, patch("src.common.MEM", Path(td)):
            common.save_video({"video_id": "abc", "title": "A test"})
            self.assertEqual("A test", common.load_videos()[0]["title"])
            (Path(td) / "videos" / "abc.json").write_text("{broken", encoding="utf-8")
            self.assertEqual([], common.load_videos())
            (Path(td) / "insights.json").write_text("{broken", encoding="utf-8")
            self.assertEqual({"weights": None, "lessons": []}, common.load_insights())

    def test_drive_cleanup_uses_recoverable_trash_and_rejects_unsafe_retention(self):
        files = _DriveFiles()
        files.list = lambda **kwargs: _Request({"files": [{"id": "old-file", "name": "old.mp4"}]})
        with patch("src.drive.svc", return_value=_DriveService(files)), \
             patch("src.drive.folder_id", return_value="folder"):
            self.assertEqual(1, drive.cleanup(days=10))
            with self.assertRaises(ValueError):
                drive.cleanup(days=0)
        self.assertEqual([{"fileId": "old-file", "body": {"trashed": True}}], files.updated)
        self.assertEqual([], files.deleted)

    def test_drive_upload_is_idempotent_for_same_content(self):
        files = _DriveFiles(existing=[{"id": "already-uploaded"}])
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "sample.bin"
            source.write_bytes(b"same content")
            with patch("src.drive.svc", return_value=_DriveService(files)), \
                 patch("src.drive.folder_id", return_value="folder"):
                self.assertEqual("already-uploaded", drive.upload(source))
        self.assertEqual([], files.created)

    def test_youtube_thumbnail_failure_is_reported_to_caller(self):
        service = SimpleNamespace(thumbnails=lambda: SimpleNamespace(
            set=lambda **kwargs: SimpleNamespace(execute=lambda: (_ for _ in ()).throw(
                HttpError(SimpleNamespace(status=403, reason="forbidden"), b"forbidden")))))
        with patch("src.youtube.yt", return_value=service), patch("src.youtube.MediaFileUpload"):
            self.assertFalse(youtube.set_thumbnail("video", "thumbnail.jpg"))

    def test_oauth_helper_refuses_to_write_missing_refresh_token(self):
        flow = SimpleNamespace(run_local_server=lambda **kwargs: SimpleNamespace(refresh_token=None))
        with patch("tools.get_tokens.InstalledAppFlow.from_client_secrets_file", return_value=flow):
            with self.assertRaisesRegex(RuntimeError, "did not return a refresh token"):
                get_tokens.main(["youtube"])


if __name__ == "__main__":
    unittest.main()
