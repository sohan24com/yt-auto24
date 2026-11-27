import unittest
from unittest.mock import MagicMock, patch

from src import google_auth, youtube


class GoogleAuthScopeTests(unittest.TestCase):
    def test_regular_youtube_credentials_keep_legacy_scopes(self):
        with patch("src.google_auth.env", return_value="test-credential"):
            scopes = google_auth.creds().scopes
        self.assertNotIn("https://www.googleapis.com/auth/youtube.force-ssl", scopes)

    def test_comment_credentials_request_youtube_force_ssl(self):
        with patch("src.google_auth.env", return_value="test-credential"):
            scopes = google_auth.comment_creds().scopes
        self.assertIn("https://www.googleapis.com/auth/youtube.force-ssl", scopes)

    def test_add_comment_uses_the_comment_scoped_client(self):
        service = MagicMock()
        service.commentThreads.return_value.insert.return_value.execute.return_value = {"id": "thread"}
        with patch("src.youtube.comments_api", return_value=service):
            result = youtube.add_comment("video-id", "Full documentary")

        self.assertEqual({"id": "thread"}, result)
        service.commentThreads.return_value.insert.assert_called_once_with(
            part="snippet",
            body={"snippet": {"videoId": "video-id", "topLevelComment": {
                "snippet": {"textOriginal": "Full documentary"}}}},
        )


if __name__ == "__main__":
    unittest.main()
