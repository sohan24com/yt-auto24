import json
import unittest
from pathlib import Path

from src import story


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

    def test_fact_check_rewrite_keeps_scene_storyboard(self):
        blueprint = sample_blueprint()
        checked = {"scenes": [{"number": i + 1, "narration": "Fact checked narration replaces draft wording."}
                              for i in range(len(blueprint["scenes"]))]}
        merged = story.preserve_planning(blueprint, checked)
        self.assertEqual(blueprint["scenes"][0]["visual_plan"], merged["scenes"][0]["visual_plan"])
        self.assertEqual(blueprint["title_promise"], merged["title_promise"])


if __name__ == "__main__":
    unittest.main()
