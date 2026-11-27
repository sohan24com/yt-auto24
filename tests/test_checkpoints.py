import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import edit
from src.checkpoints import StageCheckpoints


class StageCheckpointTests(unittest.TestCase):
    def test_checkpoint_survives_restart_and_reuses_valid_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "research.json").write_text('{"source":"fixture"}', encoding="utf-8")
            (root / "script.json").write_text('{"scenes":2}', encoding="utf-8")
            first = StageCheckpoints(root)
            first.complete("research", ["research.json"])
            first.complete("script", ["script.json"])

            resumed = StageCheckpoints(root)
            self.assertTrue(resumed.is_complete("research"))
            self.assertTrue(resumed.is_complete("script"))
            self.assertEqual("evidence_validation", resumed.next_stage())

    def test_changed_or_missing_artifact_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "voice.wav"
            artifact.write_bytes(b"first render")
            checkpoints = StageCheckpoints(root)
            checkpoints.complete("narration", [artifact])
            artifact.write_bytes(b"changed render")
            self.assertFalse(checkpoints.is_complete("narration"))

    def test_invalidation_clears_downstream_state_without_deleting_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            checkpoints = StageCheckpoints(root)
            for stage in ("research", "script", "evidence_validation", "storyboard", "narration", "footage"):
                artifact = root / f"{stage}.txt"
                artifact.write_text(stage, encoding="utf-8")
                files.append(artifact)
                checkpoints.complete(stage, [artifact])
            checkpoints.invalidate_from("footage")
            self.assertTrue(checkpoints.is_complete("research"))
            self.assertTrue(checkpoints.is_complete("script"))
            self.assertEqual("footage", checkpoints.next_stage())
            self.assertTrue(all(path.exists() for path in files))

    def test_checkpoint_rejects_artifact_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            artifact = Path(outside) / "secret.txt"
            artifact.write_text("no", encoding="utf-8")
            with self.assertRaises(ValueError):
                StageCheckpoints(tmp).complete("research", [artifact])

    def test_corrupt_checkpoint_manifest_fails_closed_and_can_be_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "topic.json"
            artifact.write_text('{"topic":"real provider output"}', encoding="utf-8")
            checkpoints = StageCheckpoints(root)
            checkpoints.complete("research", [artifact], stage_fingerprint="input-v1")
            checkpoints.path.write_text("{truncated", encoding="utf-8")
            resumed = StageCheckpoints(root)
            self.assertFalse(resumed.is_complete("research"))
            self.assertEqual("research", resumed.next_stage())
            resumed.complete("research", [artifact], stage_fingerprint="input-v2")
            self.assertTrue(resumed.is_complete("research", "input-v2"))

    def test_input_fingerprint_mismatch_invalidates_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "draft.json"
            artifact.write_text("{}", encoding="utf-8")
            checkpoints = StageCheckpoints(root)
            checkpoints.complete("script", [artifact], stage_fingerprint="old-input")
            self.assertFalse(checkpoints.is_complete("script", "new-input"))

    def test_interrupted_edit_reuses_completed_hash_verified_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "source.png"
            image.write_bytes(b"original source")
            voice = root / "voice.wav"
            voice.write_bytes(b"voice")
            calls = []

            def render(asset, duration, output, index, **kwargs):
                calls.append(index)
                if len(calls) == 2:
                    raise RuntimeError("interrupted while rendering second shot")
                Path(output).write_bytes(f"shot-{index}".encode())

            scenes = [{"narration": "test words", "visual_plan": {}},
                      {"narration": "more test words", "visual_plan": {}}]
            args = (scenes, [1.0, 1.0], [[1.0], [1.0]],
                    [[{"path": str(image), "kind": "image"}],
                     [{"path": str(image), "kind": "image"}]], voice, root, root / "final.mp4")
            with (patch("src.edit.render_shot", side_effect=render),
                  patch("src.edit.make_ass"), patch("src.edit.make_music"),
                  patch("src.edit.make_whoosh"), patch("src.edit.make_impact"),
                  patch("src.edit.mix_audio")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    edit.prepare_edit(*args, progress_fingerprint="same-input")
                # Allow the next render to finish. The first shot should be verified and reused.
                def resume_render(asset, duration, output, index, **kwargs):
                    calls.append(index)
                    Path(output).write_bytes(f"shot-{index}".encode())
                with patch("src.edit.render_shot", side_effect=resume_render):
                    edit.prepare_edit(*args, progress_fingerprint="same-input")
            self.assertEqual([0, 1, 1], calls)
            self.assertTrue((root / "shot0000.mp4").is_file())
            self.assertTrue((root / "shot0001.mp4").is_file())

    def test_final_render_is_promoted_atomically_only_after_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "final.mp4"
            def fake_ffmpeg(command, cwd=None):
                Path(command[-1]).write_bytes(b"complete mp4")
            with patch("src.edit.run", side_effect=fake_ffmpeg):
                edit.render_prepared(root, output, 4.0)
            self.assertEqual(b"complete mp4", output.read_bytes())
            self.assertFalse((root / "final.partial.mp4").exists())


if __name__ == "__main__":
    unittest.main()
