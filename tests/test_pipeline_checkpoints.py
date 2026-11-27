import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import pipeline
from src.checkpoints import BuildLock, StageCheckpoints, atomic_json


class PipelineCheckpointIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = {name: 0 for name in ("topic", "script", "evidence", "voice", "footage", "edit", "render")}
        self._stack = []
        self.script_data = {
            "topic": "A test topic", "angle": "Evidence question", "category": "history",
            "setting": "test setting", "wiki_terms": ["test"], "hook_text": "TEST TOPIC HERE",
            "sources": [], "story_schema_version": 1,
            "scenes": [{"number": 1, "title": "The clue", "narration": "A short test narration.",
                        "emotion": "neutral", "visual_plan": {}, "visuals": ["test visual"]}],
        }
        self._install_mocks()

    def tearDown(self):
        for stack in reversed(self._stack):
            stack.__exit__(None, None, None)
        self.temp.cleanup()

    def _install_mocks(self):
        from contextlib import ExitStack
        stack = ExitStack()
        self._stack.append(stack)

        def topic():
            self.calls["topic"] += 1
            return {"topic": "A test topic", "angle": "Evidence question", "category": "history"}

        def write_script(*args, **kwargs):
            self.calls["script"] += 1
            return json.loads(json.dumps(self.script_data))

        def fact_check(draft):
            self.calls["evidence"] += 1
            return {"_fact_check": {"status": "search_linked"}}

        def attach_evidence(script, record):
            result = dict(script)
            result["_fact_check"] = record
            return result

        def voice(scenes, wd):
            self.calls["voice"] += 1
            path = Path(wd) / "voice.wav"
            path.write_bytes(f"voice-{self.calls['voice']}".encode())
            return path, [4.0], "local-test"

        def gather(*args, **kwargs):
            self.calls["footage"] += 1
            path = Path(args[5]) / "asset.png"
            path.write_bytes(b"test image bytes")
            return {"path": str(path), "kind": "image", "reused": False,
                    "source": "Original test graphic", "asset_id": "test-asset",
                    "source_url": "local://test-asset", "license": "Original generated graphic",
                    "license_url": "", "license_status": "original_generated",
                    "match_words": {"test", "visual"}, "visual_role": "general"}

        def prepare(*args, **kwargs):
            self.calls["edit"] += 1
            wd = Path(args[5])
            files = {"shot0000.mp4": b"shot", "shots.txt": b"shot list", "subs.ass": b"subtitles",
                     "music.wav": b"music", "whoosh.wav": b"whoosh", "impact.wav": b"impact",
                     "mix.wav": b"mix", "edit-progress.json": b"progress"}
            for name, data in files.items():
                (wd / name).write_bytes(data)
            atomic_json(wd / "edit-stage.json", {"starts": [0.0], "total_duration": 4.0})
            return [0.0]
        self.prepare_impl = prepare

        def render(wd, final, total):
            self.calls["render"] += 1
            Path(final).write_bytes(f"render-{self.calls['render']}".encode())

        replacements = {
            "src.pipeline.T.choose_topic": topic,
            "src.pipeline.S.write_script": write_script,
            "src.pipeline.S.fact_check": fact_check,
            "src.pipeline.S.valid": lambda script: [],
            "src.pipeline.STORY.preserve_planning": lambda draft, checked: {**draft, **checked},
            "src.pipeline.STORY.attach_evidence": attach_evidence,
            "src.pipeline.STORY.validate_blueprint": lambda *a, **k: [],
            "src.pipeline.STORY.quality_score": lambda *a, **k: {"score": 100},
            "src.pipeline.STORY.validate_timing": lambda *a, **k: [],
            "src.pipeline.V.build_voice": voice,
            "src.pipeline.E.plan_shots": lambda *a, **k: [4.0],
            "src.pipeline.S.beats": lambda *a, **k: [["test narration"]],
            "src.pipeline.S.shot_queries": lambda *a, **k: [[("test visual", "test image")]],
            "src.pipeline.F.wiki_pool": lambda *a, **k: [],
            "src.pipeline.F.gather_shot": gather,
            "src.pipeline.E.prepare_edit": prepare,
            "src.pipeline.E.render_prepared": render,
            "src.pipeline.gate": lambda *a, **k: [],
            "src.pipeline.cfg": lambda: {"video": {"min_seconds": 1, "max_seconds": 100,
                                                      "voice": "offline", "gemini_tts": False},
                                         "story": {}, "publishing": {"require_search_linked_fact_check": True}},
            "src.pipeline.load_videos": lambda: [],
            "src.pipeline.load_insights": lambda: {"weights": None, "lessons": []},
        }
        for target, value in replacements.items():
            stack.enter_context(patch(target, side_effect=value))

    def run_build(self):
        return pipeline.build_once(self.root)

    def test_successful_pipeline_stages_resume_after_restart(self):
        self.run_build()
        result = pipeline.build_once(self.root)  # a fresh call simulates a process restart
        self.assertEqual([], result[-1])
        self.assertEqual({name: 1 for name in self.calls}, self.calls)
        checkpoints = StageCheckpoints(self.root)
        self.assertIsNone(checkpoints.next_stage())
        for stage in ("research", "script", "evidence_validation", "storyboard", "narration",
                      "footage", "editing", "render", "quality_control"):
            self.assertTrue(checkpoints.is_complete(stage), stage)

    def test_configuration_or_code_input_change_invalidates_downstream(self):
        with patch("src.pipeline._build_input_fingerprint", side_effect=["base-a", "base-b"]):
            self.run_build()
            self.run_build()
        self.assertEqual(2, self.calls["topic"])
        self.assertEqual(2, self.calls["script"])
        self.assertEqual(2, self.calls["render"])

    def test_corrupt_stage_artifact_rebuilds_from_that_stage(self):
        self.run_build()
        (self.root / "script-draft.json").write_text("corrupted", encoding="utf-8")
        self.run_build()
        self.assertEqual(1, self.calls["topic"])
        self.assertEqual(2, self.calls["script"])
        self.assertEqual(2, self.calls["evidence"])

    def test_interrupted_edit_reuses_all_completed_upstream_stages(self):
        attempts = 0
        def interrupted(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("crash")
            return self.prepare_impl(*args, **kwargs)
        with patch("src.pipeline.E.prepare_edit", side_effect=interrupted) as edit_mock:
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.run_build()
            self.run_build()
            self.assertEqual(2, edit_mock.call_count)
        self.assertEqual(1, self.calls["topic"])
        self.assertEqual(1, self.calls["script"])
        self.assertEqual(1, self.calls["voice"])
        self.assertEqual(1, self.calls["footage"])
        self.assertEqual(1, self.calls["render"])

    def test_failed_evidence_is_not_cached_as_success_and_retries(self):
        statuses = iter(("not_verified", "search_linked"))
        def fact_check(draft):
            self.calls["evidence"] += 1
            return {"_fact_check": {"status": next(statuses)}}
        with patch("src.pipeline.S.fact_check", side_effect=fact_check):
            with self.assertRaisesRegex(RuntimeError, "search-linked claim evidence"):
                self.run_build()
            self.run_build()
        self.assertEqual(2, self.calls["evidence"])
        self.assertTrue(StageCheckpoints(self.root).is_complete("evidence_validation"))

    def test_missing_research_provider_fails_closed_without_fake_topic(self):
        with patch("src.pipeline.T.choose_topic", side_effect=RuntimeError("provider unavailable")):
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                self.run_build()
        self.assertFalse(StageCheckpoints(self.root).is_complete("research"))
        self.assertFalse((self.root / "script-draft.json").exists())

    def test_invalid_script_retry_receives_budget_and_previous_draft(self):
        with patch("src.pipeline.S.valid", side_effect=[["word count 700"], []]), \
             patch("src.pipeline.cfg", return_value={"video": {"min_seconds": 1, "max_seconds": 100,
                       "words_min": 1000, "words_max": 1400, "voice": "offline", "gemini_tts": False},
                       "story": {}, "publishing": {"require_search_linked_fact_check": True}}), \
             patch("src.pipeline.S.write_script", side_effect=lambda *a, **k: json.loads(
                 json.dumps(self.script_data))) as writer:
            self.run_build()
        self.assertEqual(2, writer.call_count)
        retry = writer.call_args_list[1]
        self.assertEqual(self.script_data, retry.kwargs["previous_draft"])
        feedback = retry.kwargs["validation_feedback"]
        self.assertTrue(any("hard minimum" in item and "hard maximum" in item for item in feedback))


class BuildLockTests(unittest.TestCase):
    def test_active_duplicate_build_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with BuildLock(tmp):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    BuildLock(tmp).__enter__()


if __name__ == "__main__":
    unittest.main()
