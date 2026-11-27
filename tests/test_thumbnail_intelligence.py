import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from src import analytics, thumbnail


class ThumbnailIntelligenceTests(unittest.TestCase):
    def _frames(self, root, count=4):
        rows = []
        for n in range(count):
            im = Image.new("RGB", (960, 540), (18 + n * 28, 48 + n * 12, 82 + n * 18))
            draw = ImageDraw.Draw(im)
            draw.ellipse((120 + n * 90, 100, 390 + n * 50, 390), fill=(220, 175 - n * 12, 72 + n * 15))
            draw.rectangle((600, 100 + n * 18, 850, 370), fill=(150, 205, 220))
            path = root / f"frame{n}.jpg"
            im.save(path, quality=90)
            rows.append({"path": path, "scene_title": f"Evidence scene {n}",
                         "narration": "The hidden evidence changed this history.", "emotion": "mystery"})
        return rows

    def test_generates_and_ranks_distinct_concepts_exports_and_mobile_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = thumbnail.generate_thumbnail_package(
                self._frames(root), [], {"topic": "Hidden history", "angle": "The evidence"},
                {"title": "The Hidden History", "thumb_texts": ["HIDDEN EVIDENCE", "LOST HISTORY"]}, root)
            report = package["report"]
            self.assertGreaterEqual(report["candidate_count"], 8)
            self.assertLessEqual(report["candidate_count"], 10)
            self.assertEqual(3, len(package["exports"]))
            self.assertEqual(3, len(set(x["concept"] for x in report["candidates"] if x["id"] in report["selected"])))
            for path in package["exports"]:
                with Image.open(path) as im:
                    self.assertEqual((1280, 720), im.size)
                    self.assertEqual("JPEG", im.format)
                self.assertGreater(path.stat().st_size, 1000)
            self.assertTrue(all(Path(x["qc"]["mobile_preview"]).is_file() for x in report["candidates"]))
            self.assertIn("CTR", report["score_notice"])
            saved = json.loads((root / "thumbnail_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["selected"], saved["selected"])

    def test_no_text_candidate_and_backward_compatible_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = self._frames(root, 1)
            report = thumbnail.generate_thumbnail_package(frames, [], {"topic": "Rain forest"},
                {"title": "A Rain Forest Story", "thumb_texts": []}, root)["report"]
            self.assertTrue(any(not candidate["text"] for candidate in report["candidates"]))
            legacy = thumbnail.make_thumbs([frames[0]["path"]], ["RAIN FOREST"], root)
            self.assertEqual(3, len(legacy))
            self.assertTrue(all(p.is_file() for p in legacy))

    def test_judge_penalizes_title_repetition_and_marks_unanchored_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame = self._frames(root, 1)[0]
            package = thumbnail.generate_thumbnail_package([frame], [], {"topic": "Ancient cities"},
                {"title": "Lost city discovered", "thumb_texts": ["LOST CITY"]}, root)
            by_text = next(row for row in package["report"]["candidates"] if row["text"])
            no_text = next(row for row in package["report"]["candidates"] if not row["text"])
            self.assertLess(by_text["dimensions"]["title_compatibility"], no_text["dimensions"]["title_compatibility"])
            self.assertIn("factual_anchor", by_text["qc"])

    def test_near_duplicate_source_frames_are_reported_and_quiet_text_side_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = self._frames(root, 2)
            frames[1]["path"].write_bytes(frames[0]["path"].read_bytes())
            report = thumbnail.generate_thumbnail_package(frames, [], {"topic": "Evidence"},
                {"title": "Evidence", "thumb_texts": []}, root)["report"]
            self.assertEqual(1, len(report["duplicate_source_frames"]))
            image = Image.new("RGB", (1280, 720), (220, 220, 220))
            ImageDraw.Draw(image).rectangle((0, 0, 640, 720), fill=(8, 8, 8))
            self.assertEqual("left", thumbnail._preferred_text_side(image))

    def test_analytics_selects_next_ranked_export_and_supports_legacy_video_records(self):
        record = {"active_thumbnail_drive_id": "drive-a", "thumb_ids": ["drive-a", "drive-b", "drive-c"],
                  "thumbnail_options": [{"drive_id": "drive-a", "concept": "একক দৃশ্য", "score": 82},
                                        {"drive_id": "drive-b", "concept": "দুই দৃশ্য", "score": 80},
                                        {"drive_id": "drive-c", "concept": "লেখাহীন", "score": 78}]}
        self.assertEqual("drive-b", analytics.next_thumbnail_option(record)["drive_id"])
        self.assertEqual("drive-b", analytics.next_thumbnail_option({"thumb_ids": ["drive-a", "drive-b"]})["drive_id"])


if __name__ == "__main__":
    unittest.main()
