import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import open_learning as learning


class OpenLearningTests(unittest.TestCase):
    def test_wikiversity_skill_requires_explicit_open_license_and_records_attribution(self):
        def api_get(url, params=None, timeout=12):
            if url.endswith("api.php"):
                return {"query": {"search": [{"title": "Documentary storytelling"}]}}
            return {"title": "Documentary storytelling", "html_url": "https://en.wikiversity.org/wiki/Documentary_storytelling",
                    "latest": {"id": 42},
                    "license": {"url": "//creativecommons.org/licenses/by-sa/3.0/",
                                "title": "Creative Commons Attribution-Share Alike 3.0"},
                    "html": "<p>A narrative hook introduces a question. Storytelling and pacing guide sequence.</p>"}

        skills = learning._wikiversity_skills(api_get=api_get)
        self.assertTrue(skills)
        skill = next(item for item in skills if item["id"] == "hook")
        self.assertEqual("candidate", skill["status"])
        self.assertEqual("CC BY-SA 3.0", skill["source"]["license"])
        self.assertEqual("https://creativecommons.org/licenses/by-sa/3.0/", skill["source"]["license_url"])
        self.assertEqual(42, skill["source"]["revision_id"])
        self.assertNotIn("narrative hook introduces", json.dumps(skill).lower())

        def unknown_license(url, params=None, timeout=12):
            if url.endswith("api.php"):
                return {"query": {"search": [{"title": "Unlicensed material"}]}}
            return {"title": "Unlicensed material", "html_url": "https://en.wikiversity.org/wiki/Unlicensed",
                    "license": {"url": "https://example.org/terms", "title": "Unknown"},
                    "html": "A documentary narrative hook and editing sequence."}

        self.assertEqual([], learning._wikiversity_skills(api_get=unknown_license))

    def test_openalex_skill_uses_only_allowlisted_license_and_research_abstract(self):
        words = "Documentary storytelling uses narrative sequence and editing. " \
                "Sound design and pacing help communicate research findings.".split()
        index = {word: [i] for i, word in enumerate(words)}
        def api_get(url, params=None, timeout=12):
            self.assertEqual("https://api.openalex.org/works", url)
            return {"results": [
                {"title": "Open documentary education study", "doi": "https://doi.org/10.1/example",
                 "publication_year": 2024, "best_oa_location": {"license": "cc-by", "landing_page_url": "https://uni.example/paper"},
                 "authorships": [{"institutions": [{"display_name": "Example University"}]}],
                 "abstract_inverted_index": index},
                {"title": "No reusable copy", "doi": "https://doi.org/10.1/no",
                 "best_oa_location": {"license": "cc-by-nc-nd"}, "abstract_inverted_index": index},
            ]}
        skills = learning._openalex_skills(api_get=api_get)
        self.assertTrue(skills)
        self.assertTrue(all(item["source"]["license"] == "CC BY" for item in skills))
        self.assertIn("Example University", skills[0]["source"]["institutions"])

    def test_unknown_rights_stay_review_only_for_papers_and_archive_items(self):
        def europe_api(url, params=None, timeout=12):
            return {"resultList": {"result": [{"title": "An open access article", "pmcid": "PMC1",
                                                  "isOpenAccess": "Y", "license": ""}]}}
        paper = learning._europe_pmc_search("test topic", api_get=europe_api)[0]
        self.assertEqual("unknown", paper["license"])
        self.assertEqual("rights_review_required", paper["status"])

        def loc_api(url, params=None, timeout=12):
            return {"results": [{"title": "Archive item", "id": "https://www.loc.gov/item/1/"}]}
        archive = learning._loc_search("test topic", api_get=loc_api)[0]
        self.assertEqual("item_rights_review_required", archive["status"])
        self.assertEqual("unknown", archive["license"])

    def test_nasa_results_are_visual_leads_not_automatic_reuse_clearance(self):
        def api_get(url, params=None, timeout=12):
            return {"collection": {"items": [{"data": [{"nasa_id": "N1", "title": "Earth view",
                                                           "center": "NASA", "date_created": "2020"}]}]}}
        item = learning._nasa_search("Earth", api_get=api_get)[0]
        self.assertEqual("item_rights_review_required", item["status"])
        self.assertIn("verify item-level", item["license"])
        self.assertEqual("https://images.nasa.gov/details/N1", item["url"])

    def test_discovery_is_cached_and_provider_errors_do_not_create_fake_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(learning, "CACHE", Path(temp) / "cache.json"):
                calls = []
                def api_get(url, params=None, timeout=12):
                    calls.append(url)
                    if "wikipedia.org/w/api.php" in url:
                        return {"query": {"pages": [{"title": "Test article", "fullurl": "https://en.wikipedia.org/wiki/Test"}]}}
                    raise RuntimeError("offline")
                first = learning.discover_topic_sources("ancient ocean", api_get=api_get)
                call_count = len(calls)
                second = learning.discover_topic_sources("ancient ocean", api_get=api_get)
                self.assertEqual(first, second)
                self.assertEqual(call_count, len(calls))
                leads = [row for row in first if row.get("url")]
                self.assertEqual(["Wikimedia"], [row["source"] for row in leads])
                self.assertEqual("research_lead_not_fact_checked", leads[0]["status"])
                self.assertEqual(4, sum(row.get("status") == "temporarily_unavailable" for row in first))

    def test_skill_refresh_versions_archives_previous_and_can_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            library_path = Path(temp) / "skills.json"
            with patch.object(learning, "LIBRARY", library_path):
                revision = {"value": 1}
                def api_get(url, params=None, timeout=12):
                    if url.endswith("api.php"):
                        return {"query": {"search": [{"title": "Narrative lesson"}]}}
                    return {"title": "Narrative lesson", "html_url": "https://en.wikiversity.org/wiki/Narrative_lesson",
                            "latest": {"id": revision["value"]},
                            "license": {"url": "https://creativecommons.org/licenses/by-sa/4.0/",
                                        "title": "CC BY-SA 4.0"},
                            "html": "<p>Narrative story structure moves a question through scenes.</p>"}
                first = learning.refresh_skill_library(force=True, api_get=api_get)
                old_version = first["skills"]["story_structure"]["version"]
                revision["value"] = 2
                second = learning.refresh_skill_library(force=True, api_get=api_get)
                self.assertNotEqual(old_version, second["skills"]["story_structure"]["version"])
                self.assertTrue(second["history"])
                self.assertTrue(learning.rollback_skill("story_structure"))
                rolled_back = json.loads(library_path.read_text(encoding="utf-8"))
                self.assertEqual(old_version, rolled_back["skills"]["story_structure"]["version"])

    def test_prompt_skills_are_source_attributed_and_filter_disabled(self):
        library = {"skills": {
            "hook": {"id": "hook", "version": "v2", "category": "hook", "principle": "Ask a specific question.",
                     "confidence": 0.4, "status": "candidate", "source": {"title": "Lesson", "url": "https://example.org/lesson",
                                                                                 "license": "CC BY-SA 4.0"}},
            "disabled": {"id": "disabled", "status": "disabled", "principle": "Do not show", "source": {"url": "https://example.org"}},
        }}
        items = learning.prompt_skills(library)
        self.assertEqual(["hook"], [item["id"] for item in items])
        self.assertEqual("CC BY-SA 4.0", items[0]["license"])

    def test_topic_source_integration_does_not_claim_metric_or_verified_fact(self):
        from src import topic
        with patch("src.topic.cfg", return_value={"channel": {"niche": "science"}, "category_mix": {"science": 1}}), \
             patch("src.topic.pick_category", return_value="science"), \
             patch("src.topic.load_videos", return_value=[]), \
             patch("src.topic.load_insights", return_value={"lessons": ["Channel lesson"], "weights": None}), \
             patch("src.topic.OL.refresh_skill_library", return_value={"skills": {}}), \
             patch("src.topic.OL.prompt_skills", return_value=[{"id": "story_structure", "version": "v1",
                    "principle": "Move the question forward.", "confidence": 0.4, "status": "candidate",
                    "license": "CC BY-SA 4.0", "source_title": "Lesson", "source_url": "https://example.org/lesson"}]), \
             patch("src.topic.OL.discover_topic_sources", return_value=[{"source": "LOC", "title": "Archive lead",
                    "url": "https://www.loc.gov/item/1/", "status": "item_rights_review_required"}]), \
             patch("src.topic.gemini", return_value={"ideas": [{"topic": "A sourceable question", "angle": "Why?",
                                                                  "evidence_plan": "Check primary records"}]}) as generate:
            result = topic.choose_topic()
        prompt = generate.call_args.args[0]
        self.assertIn("Channel lesson", prompt)
        self.assertIn("Move the question forward", prompt)
        self.assertIn("Do not invent search-volume", prompt)
        self.assertEqual("item_rights_review_required", result["open_research_sources"][0]["status"])
        self.assertEqual("story_structure", result["learning_skills"][0]["id"])

    def test_script_prompt_combines_channel_lessons_and_source_cited_global_skills(self):
        from src import script
        topic = {"topic": "Test history", "angle": "A documented question", "category": "history",
                 "learning_skills": [{"id": "editing", "version": "e1", "status": "candidate",
                    "confidence": 0.4, "principle": "Use a cut to clarify evidence progression.",
                    "source_title": "Open lesson", "source_url": "https://example.org/lesson",
                    "license": "CC BY-SA 4.0"}],
                 "open_research_sources": [{"source": "LOC", "title": "Catalog record",
                    "url": "https://www.loc.gov/item/1/", "license": "unknown",
                    "status": "item_rights_review_required"}]}
        with patch("src.script.cfg", return_value={"channel": {"tone": "calm"},
                    "video": {"words_min": 1000, "words_max": 1200}}), \
             patch("src.script.load_insights", return_value={"lessons": ["Own channel insight"]}), \
             patch("src.script.gemini", return_value={"scenes": []}) as generate, \
             patch("src.story.quality_score", return_value={"score": 1}):
            script.write_script(topic)
        prompt = generate.call_args.args[0]
        self.assertIn("Own channel insight", prompt)
        self.assertIn("Use a cut to clarify evidence progression", prompt)
        self.assertIn("https://example.org/lesson", prompt)
        self.assertIn("leads only", prompt)
        self.assertIn("rights: unknown", prompt)

    def test_retry_after_source_rate_limit_is_not_scheduled_or_bypassed(self):
        class Response:
            status_code = 429
            headers = {"Retry-After": "30"}
            def raise_for_status(self):
                raise AssertionError("429 must pause before generic raise")
        with patch("src.open_learning.requests.get", return_value=Response()) as request:
            with self.assertRaisesRegex(learning.SourceRateLimitedError, "rate limited"):
                learning._get("https://api.openalex.org/works")
        request.assert_called_once()

    def test_source_429_is_paused_across_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(learning, "CACHE", Path(temp) / "cache.json"):
                calls = []
                def limited(url, params=None, timeout=12):
                    calls.append(url)
                    raise learning.SourceRateLimitedError(3600)
                first = learning.discover_topic_sources("rate limited topic", api_get=limited)
                self.assertTrue(first)
                self.assertTrue(all(row.get("status") == "rate_limited_paused" for row in first))
                first_count = len(calls)
                self.assertGreater(first_count, 0)
                self.assertEqual([], learning.discover_topic_sources("rate limited topic", api_get=limited))
                self.assertEqual(first_count, len(calls))


if __name__ == "__main__":
    unittest.main()
