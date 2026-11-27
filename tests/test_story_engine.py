import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import edit, script, story


SAMPLE_PATH = Path(__file__).resolve().parent.parent / "samples" / "antikythera_story_blueprint.json"


def sample_blueprint():
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


class StoryBlueprintTests(unittest.TestCase):
    def test_research_sample_has_valid_three_act_structure_and_linked_evidence(self):
        blueprint = sample_blueprint()
        issues = story.validate_blueprint(blueprint, require_evidence=True)
        self.assertEqual([], issues, "\n".join(issues))
        self.assertEqual(3, len(blueprint["acts"]))
        self.assertEqual(18, len(blueprint["scenes"]))
        self.assertGreaterEqual(story.quality_score(blueprint, require_evidence=True)["score"], 76)

    def test_sample_scene_targets_form_a_documentary_length_plan(self):
        blueprint = sample_blueprint()
        targets = [scene["target_duration_seconds"] for scene in blueprint["scenes"]]
        self.assertEqual([], story.validate_timing(blueprint, targets, max_total=660))
        self.assertGreaterEqual(sum(targets), 450)
        self.assertLessEqual(sum(targets), 660)

    def test_actual_hook_audio_over_five_seconds_is_rejected(self):
        blueprint = sample_blueprint()
        durations = [scene["target_duration_seconds"] for scene in blueprint["scenes"]]
        durations[0] = 5.01
        self.assertTrue(any("actual voiceover" in issue
                            for issue in story.validate_timing(blueprint, durations)))

    def test_progression_intervals_use_measured_audio_not_word_estimates(self):
        blueprint = sample_blueprint()
        durations = [scene["target_duration_seconds"] for scene in blueprint["scenes"]]
        durations[4] = 46.0
        self.assertTrue(any("scene 5 progression interval" in issue
                            for issue in story.validate_timing(blueprint, durations)))

    def test_open_loop_without_later_payoff_is_rejected(self):
        blueprint = sample_blueprint()
        blueprint["scenes"][7]["pays_off"] = []
        self.assertTrue(any("no matching payoff" in issue
                            for issue in story.validate_blueprint(blueprint)))

    def test_scene_requires_causal_progression_and_visual_anchor(self):
        blueprint = sample_blueprint()
        blueprint["scenes"][3]["causal_link"] = ""
        blueprint["scenes"][4]["visual_anchor"] = "unrelated"
        issues = story.validate_blueprint(blueprint)
        self.assertTrue(any("cause/effect" in issue for issue in issues))
        self.assertTrue(any("visual anchor" in issue for issue in issues))

    def test_visual_anchor_repair_uses_only_narration_and_existing_visual_phrase(self):
        blueprint = {"scenes": [{
            "narration": "Researchers found bronze gears beneath the shipwreck.",
            "visual_anchor": "ancient temple",
            "visuals": ["bronze gears shipwreck", "underwater divers"],
            "visual_plan": {"description": "Underwater archaeological survey", "footage_keywords": ["divers"]},
        }]}
        repaired = story.align_visual_anchors(blueprint)["scenes"][0]
        self.assertTrue(story._tokens(repaired["visual_anchor"]) <= story._tokens(repaired["narration"]))
        self.assertTrue(story._tokens(repaired["visual_anchor"]) <= story._tokens(
            repaired["visual_plan"]["description"] + " " + " ".join(repaired["visual_plan"]["footage_keywords"])))

    def test_visual_anchor_fallback_is_labeled_illustrative(self):
        blueprint = {"scenes": [{
            "narration": "Researchers found bronze gears beneath the shipwreck.",
            "visual_anchor": "unrelated mountain", "visuals": ["mountain landscape"],
            "visual_plan": {"description": "Mountain landscape", "footage_keywords": ["mountain"]},
        }]}
        repaired = story.align_visual_anchors(blueprint)["scenes"][0]
        self.assertIn("illustrative, narration-derived visual target", repaired["visual_plan"]["description"])
        self.assertTrue(story._tokens(repaired["visual_anchor"]) <= story._tokens(repaired["narration"]))

    def test_act_labels_are_repaired_only_from_a_complete_outline(self):
        blueprint = sample_blueprint()
        for scene in blueprint["scenes"]:
            scene["act"] = 3
        repaired = story.normalize_act_labels(blueprint)
        self.assertEqual([1] * 5 + [2] * 9 + [3] * 4,
                         [scene["act"] for scene in repaired["scenes"]])
        self.assertFalse(any("act labels" in issue for issue in story.validate_blueprint(repaired)))

        incomplete = sample_blueprint()
        incomplete["acts"][0]["scene_numbers"].pop()
        untouched = story.normalize_act_labels(incomplete)
        self.assertEqual(incomplete["scenes"], untouched["scenes"])

    def test_six_word_cold_open_is_not_mistaken_for_a_short_progression_scene(self):
        blueprint = sample_blueprint()
        blueprint["scenes"][0]["narration"] = "This hidden machine changed ancient history."
        issues = story.validate_blueprint(blueprint)
        self.assertFalse(any("scene 1 narration is too short" in issue for issue in issues))

    def test_loop_metadata_follows_explicit_payoff_marker_without_fabricating_one(self):
        blueprint = sample_blueprint()
        loop = blueprint["open_loops"][0]
        payoff_scene = next(scene for scene in blueprint["scenes"] if loop["id"] in scene["pays_off"])
        loop["payoff_scene"] = payoff_scene["number"] + 1
        repaired = story.reconcile_loop_references(blueprint)
        self.assertEqual(payoff_scene["number"], repaired["open_loops"][0]["payoff_scene"])
        self.assertEqual([], [issue for issue in story.validate_blueprint(repaired)
                              if issue.startswith("retention:")])
        for scene in repaired["scenes"]:
            scene["pays_off"] = [item for item in scene["pays_off"] if item != loop["id"]]
        missing = story.reconcile_loop_references(repaired)
        self.assertTrue(any("no matching payoff" in issue for issue in story.validate_blueprint(missing)))

    def test_missing_hook_overlay_is_derived_from_spoken_opening(self):
        draft = {"scenes": [{"narration": "A hidden bronze gear changed ancient history."}] * 10}
        with patch("src.script.cfg", return_value={"video": {"words_min": 10, "words_max": 300}}):
            script.valid(draft)
        self.assertEqual("HIDDEN BRONZE GEAR CHANGED ANCIENT", draft["hook_text"])

    def test_script_prompt_includes_hook_schema_field(self):
        with patch("src.script.cfg", return_value={
                "video": {"words_min": 1150, "words_max": 1400},
                "channel": {"tone": "curious"}}), \
             patch("src.script.load_insights", return_value={"lessons": []}), \
             patch("src.script.gemini", return_value={}) as call:
            script.write_script({"topic": "Test topic", "angle": "A real question"})
        self.assertIn('"hook_text":""', call.call_args.args[0])

    def test_repeated_narration_is_flagged(self):
        blueprint = sample_blueprint()
        blueprint["scenes"][2]["narration"] = blueprint["scenes"][1]["narration"]
        self.assertTrue(any("repeat substantially similar" in issue
                            for issue in story.validate_blueprint(blueprint)))

    def test_story_promise_title_and_thumbnail_must_align(self):
        blueprint = sample_blueprint()
        valid = {"title": "The Ancient Machine That Could Predict Eclipses",
                 "thumb_texts": ["PREDICTS ECLIPSES", "ANCIENT GREEK GEARS"]}
        self.assertEqual([], story.validate_packaging(blueprint, valid))
        invalid = {"title": "A Mystery Beneath the Sea",
                   "thumb_texts": ["LOST AT SEA", "ANCIENT SHIP"]}
        self.assertTrue(story.validate_packaging(blueprint, invalid))

    def test_phase_two_evidence_rows_are_mapped_to_scene_ids(self):
        blueprint = sample_blueprint()
        rows = []
        scene_ids = [1, 3, 6, 8, 10, 13, 15, 17]
        for index, scene in enumerate(scene_ids):
            rows.append({"scene": scene, "claim": f"Supported documentary claim {index} explains a fact.",
                         "evidence": "A cited source supports this specific historical claim.",
                         "source": "Research source",
                         "source_url": f"https://example.org/evidence/{index}",
                         "confidence": "high"})
        attached = story.attach_evidence(blueprint, {"claims": rows})
        self.assertEqual("CLAIM_001", attached["scenes"][0]["evidence_ids"][0])
        self.assertEqual("CLAIM_008", attached["scenes"][16]["evidence_ids"][0])
        self.assertEqual(8, len(attached["research_dossier"]))

    def test_reveal_overlay_uses_scene_animation_and_transition_plan(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "captions.ass"
            scenes = [
                {"title": "Hook", "narration": "A machine predicted eclipses."},
                {"title": "The limit", "narration": "The evidence is incomplete.",
                 "information_reveal": "The full gear count remains uncertain.",
                 "visual_plan": {"animation": "Animated text callout", "transition": "Flash cut"}},
            ]
            edit.make_ass(scenes, [4.0, 25.0], path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("THE FULL GEAR COUNT REMAINS UNCERTAIN", text)
            self.assertIn("150,500", text)

    def test_story_camera_pan_renders_with_ffmpeg(self):
        import shutil
        from PIL import Image
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg and FFprobe required")
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "pan.mp4"
            Image.new("RGB", (640, 480), "teal").save(source)
            edit.render_shot({"path": str(source), "kind": "image"}, 0.75, output, 2,
                             camera_movement="pan left")
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(output),
            ], check=True, capture_output=True, text=True)
            video = next(stream for stream in json.loads(probe.stdout)["streams"]
                         if stream.get("codec_type") == "video")
            self.assertEqual((1920, 1080), (video["width"], video["height"]))
            self.assertEqual("30/1", video["avg_frame_rate"])

    def test_fact_check_rewrite_keeps_scene_storyboard(self):
        blueprint = sample_blueprint()
        checked = {"scenes": [{"number": i + 1, "narration": "Fact checked narration replaces draft wording."}
                              for i in range(len(blueprint["scenes"]))]}
        merged = story.preserve_planning(blueprint, checked)
        self.assertEqual(blueprint["scenes"][0]["visual_plan"], merged["scenes"][0]["visual_plan"])
        self.assertEqual(blueprint["title_promise"], merged["title_promise"])


if __name__ == "__main__":
    unittest.main()
