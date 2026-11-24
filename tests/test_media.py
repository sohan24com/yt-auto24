import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import edit, footage, pipeline, shorts


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
