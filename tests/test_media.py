import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import common, edit, footage, google_auth, pipeline, shorts


class PipelineTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg and ffprobe are required for media tests")
        self.temp = tempfile.TemporaryDirectory()
        self.wd = Path(self.temp.name)
        self.source = self.wd / "source.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(self.source),
        ], check=True, capture_output=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_ffprobe_accepts_complete_landscape_and_rejects_wrong_duration(self):
        self.assertEqual([], pipeline.media_problems(
            self.source, expected_duration=4.0, min_bytes=0))
        errors = pipeline.media_problems(
            self.source, expected_duration=9.0, min_bytes=0)
        self.assertTrue(any("duration" in error for error in errors))

    @staticmethod
    def _gemini_response(status, payload=None, text=""):
        payload = payload or {}
        return SimpleNamespace(status_code=status, text=text, json=lambda: payload)

    def _gemini_test_env(self):
        return patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-key-1",
            "GEMINI_API_KEY_2": "test-key-2",
            "GEMINI_API_KEY_3": "",
            "GEMINI_API_KEY_4": "",
            "GEMINI_API_KEY_5": "",
        }, clear=False)

    def test_gemini_rotates_to_next_key_on_quota(self):
        success = {"candidates": [{"content": {"parts": [{"text": "{\\"ok\\": true}"}]}}]}
        with self._gemini_test_env(), \\
             patch("src.common._model_list", return_value=["model-a"]), \\
             patch("src.common.requests.post", side_effect=[
                 self._gemini_response(429, text="quota"),
                 self._gemini_response(200, success),
             ]) as post:
            result = common.gemini("test prompt", request_timeout=1, total_timeout=5)
        self.assertTrue(result["ok"])
        self.assertEqual(["test-key-1", "test-key-2"],
                         [call.kwargs["params"]["key"] for call in post.call_args_list])

    def test_gemini_moves_to_next_model_on_capacity_error(self):
        success = {"candidates": [{"content": {"parts": [{"text": "{\\"ok\\": true}"}]}}]}
        with self._gemini_test_env(), \\
             patch("src.common._model_list", return_value=["model-a", "model-b"]), \\
             patch("src.common.requests.post", side_effect=[
                 self._gemini_response(503, text="high demand"),
                 self._gemini_response(200, success),
             ]) as post, \\
             patch("src.common.time.sleep"):
            result = common.gemini("test prompt", request_timeout=1, total_timeout=5)
        self.assertTrue(result["ok"])
        self.assertIn("/model-b:", post.call_args_list[1].args[0])
        self.assertEqual("test-key-1", post.call_args_list[1].kwargs["params"]["key"])

    def test_gemini_exhaustion_is_bounded_and_descriptive(self):
        failures = [self._gemini_response(403, text="free tier unavailable") for _ in range(4)]
        with self._gemini_test_env(), \\
             patch("src.common._model_list", return_value=["model-a", "model-b"]), \\
             patch("src.common.requests.post", side_effect=failures) as post:
            with self.assertRaises(common.GeminiUnavailableError) as error:
                common.gemini("test prompt", request_timeout=1, total_timeout=5)
        self.assertEqual(4, post.call_count)
        self.assertIn("HTTP 403 x4", str(error.exception))

    def test_footage_candidate_match_metadata_uses_candidate_text(self):
        footage.reset()
        candidate = ("image", "pexels-p123", "https://example.test/a.jpg",
                     "Pexels", "ancient bronze gears")
        with patch("src.footage._download", return_value=True):
            asset = footage._try([candidate], "ancient bronze gears", set(), self.wd, 1)
        self.assertEqual(3, asset["match_score"])
        self.assertTrue({"ancient", "bronze", "gears"}.issubset(asset["match_words"]))

    def test_existing_youtube_token_scopes_remain_compatible(self):
        self.assertNotIn("https://www.googleapis.com/auth/youtube.force-ssl",
                         google_auth.YT_SCOPES)

    def test_script_retry_prompt_includes_validation_feedback(self):
        from src import script
        with patch("src.script.cfg", return_value={
                "video": {"words_min": 100, "words_max": 300},
                "channel": {"tone": "curious"}}), \\
             patch("src.script.load_insights", return_value={"lessons": []}), \\
             patch("src.script.gemini", return_value={}) as gemini_call:
            script.write_script({"topic": "Test topic", "angle": "A real question"},
                                validation_feedback=["5-second hook too long"])
        self.assertIn("5-second hook too long", gemini_call.call_args.args[0])

    def test_ffprobe_missing_stream_durations_fall_back_to_container(self):
        output = {
            "format": {"duration": "4.0"},
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080, "duration": "N/A"},
                {"codec_type": "audio", "duration": None},
            ],
        }
        with patch("src.pipeline.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(output))):
            self.assertEqual([], pipeline.media_problems(
                self.source, expected_duration=4.0, min_bytes=0))

    def test_short_is_portrait_and_within_duration(self):
        short = self.wd / "short.mp4"
        shorts.make_short(self.source, 0.5, 2.5, short)
        self.assertEqual([], pipeline.media_problems(
            short, expected_duration=2.5, portrait=True, min_bytes=0))

    def test_short_picker_uses_nonfinal_scene(self):
        scenes = [
            {"title": "Opening", "narration": "A quiet setup with enough words to qualify for a short scene."},
            {"title": "The clue", "emotion": "suspenseful",
             "narration": ("But why did the machine stop? The answer hides in a date from 1200 years ago. "
                           "Researchers found a clue that changed what we understood about this ancient device.")},
            {"title": "Final answer", "narration": "The final answer explains everything."},
        ]
        start, duration, index = shorts.choose_moment(scenes, [0, 20, 70], [20, 50, 15], 40)
        self.assertEqual(1, index)
        self.assertGreaterEqual(start, 20)
        self.assertLessEqual(start + duration, 70)

    def test_script_validator_checks_only_the_opening_sentence(self):
        from src import script
        narration = ("A clear question appears in the old text today. "
                     + " ".join(["detail"] * 11))
        data = {
            "scenes": [{"narration": narration} for _ in range(10)],
            "hook_text": "THE LOST CLUE",
        }
        with patch("src.script.cfg", return_value={"video": {"words_min": 100, "words_max": 300}}):
            self.assertEqual([], script.valid(data))

    def test_unmatched_shot_uses_labeled_context_card(self):
        with patch("src.footage._stock", return_value=[]), \
             patch("src.footage._nasa", return_value=[]), \
             patch("src.footage._archive", return_value=[]):
            asset = footage.gather_shot(
                "antikythera gears", "ancient bronze mechanism",
                "Researchers examine the damaged bronze fragments.", "Greece ancient period",
                set(), self.wd, [], total_shots=1)
        self.assertTrue(asset["is_context_fallback"])
        self.assertTrue(asset["path"].is_file())
        self.assertEqual("Generated context card", asset["source"])

    def test_scene_music_profiles_and_generation(self):
        self.assertEqual("suspense", edit._music_mood("hushed, suspenseful"))
        self.assertEqual("tension", edit._music_mood("urgent danger"))
        out = self.wd / "mood.wav"
        edit.make_music(3.0, out, self.wd,
                        scenes=[{"emotion": "suspenseful"}, {"emotion": "warm and reflective"}],
                        durs=[1.5, 1.5])
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 1000)

    def test_metadata_falls_back_without_gemini_and_keeps_claims_grounded(self):
        from src import script
        topic = {"topic": "The Antikythera Mechanism", "angle": "How ancient gears tracked the stars",
                 "category": "history"}
        scenes = [(0, {"title": "Bronze gears"}), (30, {"title": "The star calendar"})]
        with patch("src.script.gemini", side_effect=RuntimeError("quota exhausted")):
            meta = script.make_metadata(topic, scenes, [], "Generated context cards")
        self.assertEqual(3, len(meta["thumb_texts"]))
        self.assertIn("Antikythera", meta["title"])
        self.assertIn("The Antikythera Mechanism", meta["description"])

    def test_script_validator_rejects_long_hook(self):
        from src import script
        scene = {"narration": " ".join(["word"] * 20)}
        data = {"scenes": [scene] * 10, "hook_text": "THE LOST CLUE"}
        with patch("src.script.cfg", return_value={"video": {"words_min": 100, "words_max": 300}}):
            problems = script.valid(data)
        self.assertTrue(any("hook" in problem for problem in problems))

    def test_subtitle_file_contains_source_labels(self):
        ass = self.wd / "captions.ass"
        scenes = [{"title": "A clue", "narration": "A specific fact changes the story."}]
        edit.make_ass(scenes, [5.0], ass, "THE LOST CLUE",
                      shot_labels=[(0.0, 5.0, "ARCHIVAL CLIP • INTERNET ARCHIVE")])
        text = ass.read_text(encoding="utf-8")
        self.assertIn("THE LOST", text)
        self.assertIn("CLUE", text)
        self.assertIn("ARCHIVAL CLIP", text)


if __name__ == "__main__":
    unittest.main()
