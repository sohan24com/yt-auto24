import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import common, edit, footage, pipeline


class CheckpointAndSafetyTests(unittest.TestCase):
    def test_checkpoint_round_trip_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"version": pipeline.CHECKPOINT_VERSION, "stage": "voice", "durations": [3.2]}
            pipeline._save_state(tmp, state)
            self.assertEqual(state, pipeline._load_state(tmp))
            self.assertFalse((Path(tmp) / "checkpoint.tmp").exists())

    def test_incompatible_checkpoint_version_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "checkpoint.json").write_text('{"version": 999}', encoding="utf-8")
            self.assertEqual({"version": pipeline.CHECKPOINT_VERSION}, pipeline._load_state(tmp))

    def test_config_fingerprint_changes_with_production_inputs(self):
        today = __import__("datetime").date(2026, 11, 27)
        cfg = {"channel": {"tone": "calm"}, "video": {"voice": "A"}, "category_mix": {"history": 1}}
        original = pipeline._state_fingerprint(cfg, today)
        cfg["video"]["voice"] = "B"
        self.assertNotEqual(original, pipeline._state_fingerprint(cfg, today))

    def test_diagnostics_redact_configured_secrets_and_url_keys(self):
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "super-secret-value"}, clear=False):
            message = common.redact_secrets(
                "secret=super-secret-value https://api.test/?key=other-secret")
        self.assertNotIn("super-secret-value", message)
        self.assertNotIn("other-secret", message)
        self.assertIn("[REDACTED]", message)

    def test_scene_grade_tracks_emotion_without_paid_dependencies(self):
        suspense = edit.grade_for("hushed, suspenseful")
        wonder = edit.grade_for("awe and discovery")
        self.assertIn("saturation=0.88", suspense)
        self.assertIn("saturation=1.16", wonder)
        self.assertNotEqual(suspense, wonder)

    def test_story_shot_plan_is_deterministic_and_duration_exact(self):
        scene = {"role": "hook", "emotion": "urgent", "narration": "But why? The clue was hidden."}
        first = edit.plan_shots(13.7, fast=8, scene=scene, scene_index=0, scene_count=8)
        self.assertEqual(first, edit.plan_shots(13.7, fast=8, scene=scene,
                                                scene_index=0, scene_count=8))
        self.assertAlmostEqual(13.7, sum(first), places=2)
        self.assertGreater(len(first), 2)

    def test_caption_schedule_wraps_phrases_and_stays_within_scene(self):
        cues = edit.caption_schedule("First clause. Second clause follows, with more detail.", 5.0, 12.0)
        self.assertGreaterEqual(len(cues), 1)
        self.assertAlmostEqual(12.0, cues[0][1])
        self.assertLessEqual(cues[-1][2], 17.0)
        self.assertTrue(all(end > start for _, start, end in cues))
        self.assertTrue(all(len(edit._caption_lines(text).split("\\N")) <= 2
                            for text, _, _ in cues))

    def test_audio_mix_uses_one_program_voice_and_sidechain_copy(self):
        graph = edit._mix_filter([8.0], 30.0)
        self.assertIn("asplit=3[voice_main][voice_sc_music][voice_sc_amb]", graph)
        self.assertIn("[voice_main][music][ambience][impact][s0]amix=inputs=5", graph)
        self.assertEqual(2, graph.count("[voice_main]"))  # split output plus one program-mix input
        self.assertIn("acompressor=threshold=0.12", graph)
        self.assertIn("sidechaincompress=threshold=0.035:ratio=6", graph)

    def test_optional_denoising_is_bounded_and_opt_in(self):
        with patch("src.edit.cfg", return_value={"audio": {"voice_noise_reduction_db": 16}}):
            graph = edit._mix_filter([], 30)
        self.assertIn("afftdn=nr=16:nf=-50:tn=1", graph)

    def test_ffmpeg_loudness_json_is_parsed_from_diagnostic_output(self):
        report = '{"input_i":"-15.2", "input_tp":"-1.4", "input_lra":"6.1", "input_thresh":"-25.0", "target_offset":"0.0"}'
        with patch("src.edit.subprocess.run", return_value=__import__("types").SimpleNamespace(
                returncode=0, stderr="noise\n" + report + "\n")):
            measured = edit._loudnorm_report("mix.wav")
        self.assertAlmostEqual(-15.2, float(measured["input_i"]))
        self.assertAlmostEqual(-1.4, float(measured["input_tp"]))

    def test_mastering_retries_dynamic_normalization_when_linear_pass_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "premix.wav", Path(tmp) / "master.wav"
            source.write_bytes(b"premix")
            measurements = [
                {"input_i": "-20", "input_tp": "-2", "input_lra": "5", "input_thresh": "-30", "target_offset": "0"},
                {"input_i": "-16", "input_tp": "-0.5", "input_lra": "5", "input_thresh": "-25", "target_offset": "0"},
                {"input_i": "-18", "input_tp": "-1.2", "input_lra": "5", "input_thresh": "-27", "target_offset": "0"},
            ]
            def render(command):
                Path(command[-1]).write_bytes(b"rendered")
            with patch("src.edit._loudnorm_report", side_effect=measurements), \
                 patch("src.edit.run", side_effect=render) as run:
                result = edit._normalize_file(source, output, -18, -1, 10)
            self.assertEqual(2, run.call_count)
            self.assertIn("linear=false", " ".join(run.call_args.args[0]))
            self.assertTrue(output.is_file())
            self.assertEqual("-18", result["input_i"])

    def test_story_roles_and_sound_cues_follow_act_changes(self):
        scenes = [{"emotion": "curious", "role": "hook"},
                  {"emotion": "quiet", "role": "setup"},
                  {"emotion": "urgent", "role": "reveal"},
                  {"emotion": "reflective", "role": "resolution"}]
        self.assertEqual("hook", __import__("src.director", fromlist=["scene_role"]).scene_role(0, 4))
        cues = __import__("src.director", fromlist=["sound_cues"]).sound_cues(
            scenes, [0, 10, 30, 50], maximum=2)
        self.assertLessEqual(len(cues), 2)

    def test_visual_hash_detects_matching_image_and_rejects_bad_frame_size(self):
        frame = bytes([i % 256 for i in range(72)])
        self.assertEqual(edit._dhash(frame), edit._dhash(frame))
        with self.assertRaises(ValueError):
            edit._dhash(b"short")

    def test_build_resumes_validated_assets_and_voice_without_regenerating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            voice = root / "voice.wav"
            voice.write_bytes(b"checkpoint voice")
            asset_path = root / "pexels-p1.jpg"
            asset_path.write_bytes(b"asset")
            asset = {"path": asset_path, "kind": "image", "reused": False,
                     "source": "Pexels", "match_words": {"ancient", "gear"},
                     "match_score": 2, "content_hash": "hash-1"}
            script = {"scenes": [{"title": "A clue", "narration": "Words."}],
                      "hook_text": "THE LOST CLUE", "setting": "Greece",
                      "wiki_terms": [], "sources": []}
            state = {
                "version": pipeline.CHECKPOINT_VERSION,
                "topic": {"topic": "Old gears", "angle": "How they worked", "category": "history"},
                "script": script, "voice": {"durations": [5.0], "engine": "edge"},
                "geometry": {"plans": [[5.0]], "beats": [["A bronze gear turns."]],
                             "queries": [[{"q": "ancient bronze gears", "alt": "old mechanism",
                                           "framing": "detail", "movement": "push_in",
                                           "transition": "cut"}]]},
                "assets": [pipeline._asset_to_json(asset)],
            }
            (root / "audio_report.json").write_text("{}", encoding="utf-8")
            (root / "final.mp4").write_bytes(b"validated render checkpoint")
            pipeline._save_state(root, state)
            with patch("src.pipeline.cfg", return_value={"video": {"min_seconds": 1, "max_seconds": 10,
                                                                       "max_footage_reuse": 1},
                                                          "publishing": {"require_search_linked_fact_check": False}}), \
                 patch("src.pipeline.F.reset"), \
                 patch("src.pipeline.STORY.validate_blueprint", return_value=[]), \
                 patch("src.pipeline.STORY.quality_score", return_value={}), \
                 patch("src.pipeline.F.wiki_pool", return_value=[]), \
                 patch("src.pipeline.S.valid", return_value=[]), \
                 patch("src.pipeline.T.choose_topic") as choose_topic, \
                 patch("src.pipeline.V.build_voice") as build_voice, \
                 patch("src.pipeline.E.assemble") as assemble, \
                 patch("src.pipeline.media_problems", return_value=[]), \
                 patch("src.pipeline.gate", return_value=[]):
                result = pipeline.build_once(root)
            self.assertEqual("Old gears", result[0]["topic"])
            self.assertEqual(5.0, result[6])
            choose_topic.assert_not_called()
            build_voice.assert_not_called()
            assemble.assert_not_called()

    def test_invalid_script_retries_with_explicit_word_budget(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("src.pipeline.cfg", return_value={"video": {"words_min": 1150, "words_max": 1400}}), \
             patch("src.pipeline.F.reset"), \
             patch("src.pipeline.T.choose_topic", return_value={"topic": "A topic", "angle": "A question"}), \
             patch("src.pipeline.S.write_script", return_value={}) as write_script, \
             patch("src.pipeline.S.valid", return_value=["word count 1795"]):
            with self.assertRaisesRegex(RuntimeError, "word count 1795"):
                pipeline.build_once(tmp)
        feedback = write_script.call_args_list[1].kwargs["validation_feedback"]
        self.assertTrue(any("target 1275 words" in issue and "hard maximum 1400" in issue
                            for issue in feedback))
        self.assertEqual({}, write_script.call_args_list[1].kwargs["previous_draft"])
        self.assertEqual(5, write_script.call_count)

    def test_identical_provider_download_is_not_counted_as_distinct_footage(self):
        footage.reset()
        with tempfile.TemporaryDirectory() as tmp:
            used = set()
            first = ("image", "pexels-p1", "https://example.test/a.jpg", "Pexels", "ancient bronze gear")
            second = ("image", "pixabay-p2", "https://example.test/b.jpg", "Pixabay", "ancient bronze gear")

            def download(_url, dest, headers=None):
                Path(dest).write_bytes(b"same exact media bytes")
                return True

            with patch("src.footage._download", side_effect=download):
                result = footage._try([first], "ancient bronze gear", used, tmp, 1)
                duplicate = footage._try([second], "ancient bronze gear", used, tmp, 1)
            self.assertIsNotNone(result)
            self.assertIsNone(duplicate)
            self.assertEqual({"pexels-p1", "pixabay-p2"}, used)
            self.assertFalse((Path(tmp) / "pixabay-p2.jpg").exists())


if __name__ == "__main__":
    unittest.main()
