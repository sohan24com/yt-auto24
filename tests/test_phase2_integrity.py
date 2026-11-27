import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from src import edit, evidence, pipeline, quality, script, youtube


class EvidenceTests(unittest.TestCase):
    def test_claim_evidence_requires_https_and_links_each_story_section(self):
        claims = [
            {"scene": i, "claim": f"Documented event detail number {i}",
             "evidence": f"A source explains the documented event detail for scene {i}.",
             "source": "Public archive", "source_url": f"https://example.org/source/{i}",
             "sources": [
                 {"source": "Public archive", "url": f"https://example.org/source/{i}",
                  "evidence": f"A source explains the documented event detail for scene {i}."},
                 {"source": "University research", "url": f"https://research.example.net/paper/{i}",
                  "evidence": f"Independent research supports the documented event detail for scene {i}."},
             ],
             "confidence": "medium"}
            for i in range(1, 9)
        ]
        grounded = [{"url": source["url"]} for claim in claims for source in claim["sources"]]
        rows, error = evidence.normalize_evidence({"claims": claims, "_grounding_sources": grounded}, 8)
        self.assertIsNone(error)
        self.assertEqual(8, len(rows))

    def test_missing_source_links_are_not_verified(self):
        rows, error = evidence.normalize_evidence(
            {"_grounding_sources": [], "claims": [{"scene": 1, "claim": "This is a material claim",
                         "evidence": "Some supporting explanation is present here.",
                         "source": "Unknown", "source_url": "http://example.org"}]}, 8)
        self.assertEqual([], rows)
        self.assertTrue(error)
        self.assertEqual("not_verified", evidence.evidence_record(rows, error)["status"])

    def test_fact_check_fallback_does_not_claim_success(self):
        scenes = [{"title": f"Scene {i}", "narration": f"Some factual narration {i}"} for i in range(8)]
        with patch.object(script, "gemini", side_effect=RuntimeError("provider unavailable")):
            result = script.fact_check({"scenes": scenes, "sources": []})
        self.assertEqual("not_verified", result["_fact_check"]["status"])
        self.assertIn("not search-verified", script._fact_check_note(result["_fact_check"]))

    def test_fact_check_accepts_structured_linked_evidence(self):
        scenes = [{"title": f"Scene {i}", "narration": f"Some factual narration {i}"} for i in range(8)]
        claims = [
            {"scene": i, "claim": f"Documented event detail number {i}",
             "evidence": f"A source explains the documented event detail for scene {i}.",
             "source": "Public archive", "source_url": f"https://example.org/source/{i}",
             "sources": [
                 {"source": "Public archive", "url": f"https://example.org/source/{i}",
                  "evidence": f"A source explains the documented event detail for scene {i}."},
                 {"source": "University research", "url": f"https://research.example.net/paper/{i}",
                  "evidence": f"Independent research supports the documented event detail for scene {i}."},
             ],
             "confidence": "high"}
            for i in range(1, 9)
        ]
        grounded = [{"url": source["url"]} for claim in claims for source in claim["sources"]]
        result = {"scenes": scenes, "claims": claims, "changes": [], "_grounding_sources": grounded}
        with patch.object(script, "gemini", return_value=result):
            checked = script.fact_check({"scenes": scenes, "sources": []})
        self.assertEqual("search_linked", checked["_fact_check"]["status"])
        self.assertEqual(8, len(checked["_fact_check"]["claims"]))

    def test_single_or_ungrounded_source_cannot_be_marked_verified(self):
        claim = {"scene": 1, "claim": "A well-defined material claim exists here.",
                 "sources": [{"source": "Archive", "url": "https://archive.example.org/record/1",
                              "evidence": "This source explains a supported historical fact."}]}
        rows, reason = evidence.normalize_evidence(
            {"claims": [claim], "_grounding_sources": [{"url": "https://archive.example.org/record/1"}]}, 1)
        self.assertEqual([], rows)
        self.assertIn("two independent", reason)

    def test_grounded_sources_require_two_domains_and_record_conflicts(self):
        claim = {"scene": 1, "claim": "A well-defined material claim exists here.", "conflicts": ["Dates differ by one year."],
                 "sources": [
                     {"source": "Archive", "url": "https://archive.example.org/record/1",
                      "evidence": "This source describes the event as occurring in year 1901."},
                     {"source": "Research", "url": "https://research.example.net/paper/1",
                      "evidence": "Independent research dates the documented event to year 1902."},
                 ]}
        result = {"claims": [claim],
                  "_grounding_sources": [{"url": row["url"]} for row in claim["sources"]]}
        rows, reason = evidence.normalize_evidence(result, 1)
        self.assertIsNone(reason)
        record = evidence.evidence_record(rows)
        self.assertEqual("search_linked_conflict", record["status"])
        self.assertEqual(1, record["conflict_count"])


class QualityTests(unittest.TestCase):
    def test_ass_subtitles_reject_out_of_range_timestamps(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "subs.ass"
            path.write_text("Dialogue: 0,0:00:00.00,0:00:03.00,Main,,0,0,0,,Hello\n" * 8,
                            encoding="utf-8")
            self.assertIn("subtitle event outside video duration", quality.validate_ass(path, 1.5))

    def test_ass_subtitles_must_cover_the_narration_window(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "subs.ass"
            path.write_text("\n".join(
                ["Dialogue: 0,0:00:04.00,0:00:05.00,Main,,0,0,0,,Late caption"] * 8),
                encoding="utf-8")
            self.assertIn("narration subtitles start too late", quality.validate_ass(path, 10))

    def test_ass_text_cannot_inject_override_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "captions.ass"
            edit.make_ass([{"narration": r"Safe text {\\p1} malicious"}], [4.0], path,
                          hook_text=r"Title {\\an7}")
            content = path.read_text(encoding="utf-8")
            self.assertNotIn(r"{\\p1}", content)
            self.assertNotIn(r"{\\an7}", content)

    def test_scene_qc_flags_repeated_visuals_and_narration(self):
        scenes = [{"narration": "The same narration advances the evidence."},
                  {"narration": "The same narration advances the evidence."}]
        assets = [[{"asset_id": "same", "path": "one.png"}],
                  [{"asset_id": "same", "path": "one.png"}]]
        issues = quality.validate_story_media(scenes, [4, 4], [[4], [4]], assets)
        self.assertIn("scene 2 repeats the previous narration", issues)
        self.assertIn("scene 2 repeats a visual used in the previous scene", issues)

    def test_scene_qc_rejects_misaligned_shot_timing(self):
        issues = quality.validate_story_media(
            [{"narration": "One supported claim."}], [5], [[3]], [[{"asset_id": "a"}]])
        self.assertIn("scene 1 shot pacing does not match its narration duration", issues)

    def test_thumbnail_dimensions_are_checked(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "thumb.jpg"
            Image.new("RGB", (1280, 720), "navy").save(path, quality=90)
            self.assertEqual([], quality.validate_thumbnail(path, min_bytes=0))
            Image.new("RGB", (640, 360), "navy").save(path, quality=90)
            self.assertIn("wrong thumbnail dimensions: 640x360", quality.validate_thumbnail(path, min_bytes=0))

    def test_asset_manifest_keeps_license_fields(self):
        rows = evidence.manifest_rows([{"asset_id": "a1", "source": "Pexels",
                                        "source_url": "https://pexels.com/video/1",
                                        "license": "Pexels License",
                                        "license_url": "https://www.pexels.com/license/",
                                        "license_status": "provider_terms"}])
        self.assertEqual("provider_terms", rows[0]["license_status"])
        self.assertEqual("https://www.pexels.com/license/", rows[0]["license_url"])


class UploadIdempotencyTests(unittest.TestCase):
    def test_existing_upload_is_found_by_private_tag(self):
        tag = "yt-auto24-id-20261125-slot-1-long"
        service = Mock()
        service.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads-id"}}}]}
        service.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": [{"snippet": {"publishedAt": "2026-11-25T12:00:00Z"},
                       "contentDetails": {"videoId": "video-123"}}]}
        service.videos.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "video-123", "snippet": {"tags": [tag]}}]}
        with patch.object(youtube, "yt", return_value=service):
            self.assertEqual("video-123", youtube.find_upload_by_tag(tag))

    def test_upload_appends_idempotency_tag_to_metadata(self):
        tag = "yt-auto24-id-20261125-slot-1-long"
        request = Mock()
        request.next_chunk.return_value = (None, {"id": "video-new"})
        service = Mock()
        service.videos.return_value.insert.return_value = request
        with patch.object(youtube, "cfg", return_value={"youtube": {
                 "privacy": "private", "category_id": "27", "synthetic_disclosure": False}}), \
             patch.object(youtube, "yt", return_value=service), \
             patch.object(youtube, "MediaFileUpload", return_value=object()):
            self.assertEqual("video-new", youtube.upload("video.mp4", "Title", "Description",
                                                          ["documentary"], idempotency_tag=tag))
        payload = service.videos.return_value.insert.call_args.kwargs["body"]
        self.assertIn(tag, payload["snippet"]["tags"])


class FfmpegIntegrationTests(unittest.TestCase):
    def test_full_decode_probe_and_audio_scan_accept_valid_render(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg and FFprobe required")
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "valid.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(video),
            ], check=True, capture_output=True)
            self.assertEqual([], quality.validate_video(video, expected_duration=2,
                                                         min_bytes=0, expected_fps=30, deep=True))


if __name__ == "__main__":
    unittest.main()
