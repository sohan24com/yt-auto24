import base64
import unittest
from unittest.mock import patch

from src import common, voice


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = "provider error"

    def json(self):
        return self._payload


class GeminiTextErrorTests(unittest.TestCase):
    def _env(self, name, required=True):
        return {
            "GEMINI_API_KEY": "test-key-one",
            "GEMINI_API_KEY_2": "test-key-two",
        }.get(name, "")

    def test_quota_error_stops_key_rotation_and_classifies_fallback_reason(self):
        with patch.object(common, "_GEMINI_PAUSED_REASON", None), \
             patch.object(common, "_model_list", return_value=["model-a", "model-b"]), \
             patch.object(common, "env", side_effect=self._env), \
             patch("src.common.requests.post", return_value=FakeResponse(429)) as post:
            with self.assertRaises(common.GeminiUnavailableError) as error:
                common._gemini("prompt", json_out=False)

        self.assertEqual(4, post.call_count)
        self.assertIn("quota or rate limit", str(error.exception))

    def test_permission_error_stops_key_rotation(self):
        with patch.object(common, "_GEMINI_PAUSED_REASON", None), \
             patch.object(common, "_model_list", return_value=["model-a", "model-b"]), \
             patch.object(common, "env", side_effect=self._env), \
             patch("src.common.requests.post", return_value=FakeResponse(403)) as post:
            with self.assertRaises(common.GeminiUnavailableError) as error:
                common._gemini("prompt", json_out=False)

        self.assertEqual(1, post.call_count)
        self.assertIn("project permission or free-tier access", str(error.exception))

    def test_request_or_model_error_tries_next_model_with_same_key(self):
        good = FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]})
        for status in (400, 404):
            with self.subTest(status=status), \
                 patch.object(common, "_GEMINI_PAUSED_REASON", None), \
                 patch.object(common, "_model_list", return_value=["bad-candidate", "available-model"]), \
                 patch.object(common, "env", side_effect=self._env), \
                 patch("src.common.requests.post", side_effect=[FakeResponse(status), good]) as post:
                result = common._gemini("prompt", json_out=False)

            self.assertEqual("answer", result)
            self.assertEqual(2, post.call_count)
            self.assertIn("bad-candidate", post.call_args_list[0].args[0])
            self.assertIn("available-model", post.call_args_list[1].args[0])
            self.assertEqual("test-key-one", post.call_args_list[1].kwargs["params"]["key"])

    def test_quota_failure_does_not_call_non_gemini_provider(self):
        with patch.object(common, "_gemini", side_effect=common.GeminiUnavailableError("quota")), \
             patch.object(common, "_groq") as groq, \
             patch.object(common, "_cloudflare") as cloudflare, \
             patch.object(common, "_openrouter") as openrouter:
            with self.assertRaises(common.GeminiUnavailableError):
                common.gemini("prompt", json_out=False)

        groq.assert_not_called()
        cloudflare.assert_not_called()
        openrouter.assert_not_called()


class GeminiTtsErrorTests(unittest.TestCase):
    def test_permission_and_quota_errors_skip_other_endpoint_and_keys(self):
        for status in (403, 429):
            with self.subTest(status=status), \
                 patch("src.voice.cfg", return_value={"video": {
                     "gemini_tts_model": "tts-model", "gemini_tts_fallback_model": "fallback-tts",
                     "gemini_voice": "Schedar"}}), \
                 patch("src.voice.requests.post", return_value=FakeResponse(status)) as post:
                self.assertFalse(voice._gemini("narration", "out.wav", "calm", ["key-one", "key-two"]))

            self.assertEqual(1, post.call_count)

    def test_bad_interactions_request_uses_classic_tts_once(self):
        audio = base64.b64encode(b"RIFF" + b"test audio").decode("ascii")
        good = FakeResponse(200, {"audio": {"data": audio}})
        with patch("src.voice.cfg", return_value={"video": {
                 "gemini_tts_model": "tts-model", "gemini_tts_fallback_model": "fallback-tts",
                 "gemini_voice": "Schedar"}}), \
             patch("src.voice.requests.post", side_effect=[FakeResponse(400), good]) as post, \
             patch("src.voice._write_audio") as write_audio:
            result = voice._gemini("narration", "out.wav", "calm", ["key-one"])

        self.assertTrue(result)
        self.assertEqual(2, post.call_count)
        write_audio.assert_called_once_with(audio, "out.wav")


if __name__ == "__main__":
    unittest.main()

