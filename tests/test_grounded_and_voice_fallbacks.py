import unittest
from unittest.mock import patch

from src import common, voice


class GroundedFallbackTests(unittest.TestCase):
    def test_groq_browser_search_fallback_is_used_after_gemini(self):
        with patch.object(common, "_gemini", side_effect=common.GeminiUnavailableError("Gemini quota")), \
             patch.object(common, "_groq", return_value={"scenes": [], "sources": ["source"]}) as groq, \
             patch.object(common, "_cloudflare") as cloudflare, \
             patch.object(common, "_openrouter") as openrouter:
            result = common.gemini("verify claims", grounding=True)
        self.assertEqual(result["sources"], ["source"])
        self.assertTrue(groq.call_args.kwargs["grounding"])
        cloudflare.assert_not_called()
        openrouter.assert_not_called()

    def test_groq_grounded_request_uses_responses_browser_search(self):
        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            "output_text": '{"scenes": [], "sources": ["https://example.org"]}'
        }
        env_values = {"GROQ_API_KEY": "test-key", "GROQ_API_KEY_2": ""}
        with patch.object(common, "env", side_effect=lambda name, required=True: env_values.get(name, "")), \
             patch.object(common.requests, "post", return_value=response) as post:
            result = common._groq("Return JSON", grounding=True, max_completion_tokens=1600)
        self.assertEqual(result["sources"], ["https://example.org"])
        self.assertTrue(post.call_args.args[0].endswith("/responses"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "openai/gpt-oss-20b")
        self.assertEqual(payload["tools"], [{"type": "browser_search"}])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["max_output_tokens"], 1600)
        self.assertEqual(payload["input"], "Return JSON")
        self.assertNotIn("response_format", payload)


class OfflineVoiceFallbackTests(unittest.TestCase):
    def test_espeak_is_final_whole_video_fallback(self):
        scenes = [{"narration": "A short narration.", "emotion": "calm"}]
        cfg = {"video": {"gemini_tts": True, "rate": "+0"}}
        with patch.object(voice, "cfg", return_value=cfg), \
             patch.object(voice, "env", side_effect=lambda name, required=False: "test-key" if name == "GEMINI_API_KEY" else ""), \
             patch.object(voice, "_gemini", return_value=False), \
             patch.object(voice, "_edge", side_effect=RuntimeError("network unavailable")), \
             patch.object(voice, "_espeak") as espeak, \
             patch.object(voice, "_to_wav"), \
             patch.object(voice, "probe", return_value=1.25), \
             patch.object(voice.subprocess, "run") as ffmpeg:
            out, durations, engine = voice.build_voice(scenes, ".")
        self.assertEqual(engine, "espeak")
        self.assertEqual(durations, [1.25])
        self.assertEqual(out.name, "voice.wav")
        espeak.assert_called_once()
        ffmpeg.assert_called_once()


if __name__ == "__main__":
    unittest.main()

