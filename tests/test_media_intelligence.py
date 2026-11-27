import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.media.cache import SearchCache
from src.media.downloader import download
from src.media.models import MediaAsset
from src.media.policy import classify_license
from src.media.providers import CoverrProvider, LocProvider, NasaProvider, PexelsProvider
from src.media.search import SearchRequest, build_queries, score_asset, search, search_audio, select_diverse
from src import footage


class RightsPolicyTests(unittest.TestCase):
    def test_explicit_license_categories_are_conservative(self):
        self.assertEqual("allowed_attribution", classify_license(
            "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")[0])
        self.assertEqual("review_required", classify_license(
            "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/")[0])
        self.assertEqual("noncommercial_only", classify_license(
            "CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/")[0])
        self.assertEqual("no_derivatives", classify_license(
            "CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0/")[0])
        self.assertEqual("review_required", classify_license("", "", "rights unknown")[0])

    def test_public_domain_mark_is_not_automatic_clearance(self):
        self.assertEqual("review_required", classify_license(
            "Public Domain Mark", "https://creativecommons.org/publicdomain/mark/1.0/")[0])

    def test_review_only_asset_is_not_publish_ready(self):
        asset = MediaAsset("x", "x", "archive", "video", "https://example.test/item",
                           direct_url="https://example.test/video.mp4", license_url="https://example.test/rights",
                           license_status="review_required")
        self.assertFalse(asset.publish_ready)
        self.assertEqual([], select_diverse([asset]))


class SearchEngineTests(unittest.TestCase):
    def test_malformed_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "search.json").write_text("[]", encoding="utf-8")
            self.assertIsNone(SearchCache(directory=temp).get("p", "image", "q"))

    def test_query_expansion_stays_specific_to_the_topic(self):
        req = SearchRequest(topic="Deep Ocean Mystery", required_visual="deep ocean trench ROV",
                            scene_description="A camera descends to the seabed")
        queries = build_queries(req)
        self.assertIn("deep sea trench underwater ROV", queries)
        self.assertLessEqual(len(queries), 3)
        self.assertFalse(any("random" in query for query in queries))

    def test_relevance_and_scene_match_outweigh_resolution(self):
        req = SearchRequest(topic="deep ocean", required_visual="deep ocean trench marine life")
        relevant = MediaAsset("r", "deep ocean trench marine life", "source", "video",
                              "https://example.test/r", "https://example.test/r.mp4",
                              license_status="provider_terms", license_url="https://example.test/license",
                              width=1280, height=720)
        irrelevant = MediaAsset("i", "downtown skyline traffic", "source", "video",
                                "https://example.test/i", "https://example.test/i.mp4",
                                license_status="provider_terms", license_url="https://example.test/license",
                                width=4096, height=2160)
        self.assertGreater(score_asset(relevant, req).quality_score,
                           score_asset(irrelevant, req).quality_score)

    def test_duplicate_and_source_diversity_limits(self):
        assets = [MediaAsset(str(i), f"title {i}", "Pexels" if i < 4 else "Commons", "video",
                             f"https://example.test/{i}", f"https://example.test/{i}.mp4",
                             license_url="https://example.test/license", license_status="provider_terms",
                             quality_score=100-i) for i in range(6)]
        chosen = select_diverse(assets, limit=5, max_per_source=2)
        self.assertEqual(4, len(chosen))
        self.assertEqual(2, sum(asset.source == "Pexels" for asset in chosen))

    def test_same_direct_media_url_is_not_selected_twice(self):
        first = MediaAsset("first", "clip one", "Pexels", "video", "https://example.test/1",
                           "https://cdn.example.test/shared.mp4", license_url="https://example.test/license",
                           license_status="provider_terms", quality_score=90)
        duplicate = MediaAsset("second", "clip two", "Pixabay", "video", "https://example.test/2",
                               "https://cdn.example.test/shared.mp4", license_url="https://example.test/license",
                               license_status="provider_terms", quality_score=80)
        self.assertEqual(["first"], [asset.asset_id for asset in select_diverse([first, duplicate])])

    def test_api_failure_falls_through_to_another_adapter(self):
        class Failing:
            cacheable = False
            last_status = "http_429"
            def search(self, *args):
                return []

        class Working:
            cacheable = False
            last_status = "ok"
            def search(self, *args):
                return [MediaAsset("good", "deep sea trench", "Openverse", "image",
                                   "https://example.test/good", "https://example.test/good.jpg",
                                   license_name="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                                   license_status="allowed_public_domain")]

        request = SearchRequest(required_visual="deep sea trench", media_types=("image",),
                                providers=("failing", "working"), limit_per_source=1)
        with patch("src.media.search.PROVIDERS", {"failing": Failing(), "working": Working()}), \
             tempfile.TemporaryDirectory() as temp:
            results = search(request, SearchCache(directory=temp))
        self.assertEqual(["good"], [asset.asset_id for asset in results])
        self.assertTrue(any(row["source"] == "working" for row in request.context["source_diagnostics"]))

    def test_successful_search_cache_avoids_another_provider_call(self):
        class Working:
            cacheable = True
            last_status = "ok"
            def __init__(self):
                self.calls = 0
            def search(self, *args):
                self.calls += 1
                return [MediaAsset("one", "forest river", "Openverse", "image",
                                   "https://example.test/one", "https://example.test/one.jpg",
                                   license_name="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                                   license_status="allowed_public_domain")]

        provider = Working()
        req = SearchRequest(required_visual="forest river", media_types=("image",), providers=("working",))
        with patch("src.media.search.PROVIDERS", {"working": provider}), tempfile.TemporaryDirectory() as temp:
            cache = SearchCache(directory=temp, ttl_seconds=86400)
            first = search(req, cache)
            second = search(req, cache)
            cached_file = Path(temp, "search.json").read_text(encoding="utf-8")
        self.assertEqual(1, provider.calls)
        self.assertEqual(first[0].asset_id, second[0].asset_id)
        self.assertNotIn("API_KEY", cached_file)

    def test_failed_api_responses_are_not_cached(self):
        class Forbidden:
            cacheable = True
            last_status = "http_403"
            def search(self, *args):
                return []

        req = SearchRequest(required_visual="deep sea trench", media_types=("image",), providers=("denied",))
        with patch("src.media.search.PROVIDERS", {"denied": Forbidden()}), tempfile.TemporaryDirectory() as temp:
            search(req, SearchCache(directory=temp))
            self.assertFalse(Path(temp, "search.json").exists())

    def test_audio_search_returns_only_reuse_eligible_results(self):
        class AudioProvider:
            cacheable = False
            last_status = "ok"
            def search(self, query, media_type, limit):
                self.assert_audio = media_type
                return [
                    MediaAsset("cc0-audio", "underwater ambience", "Openverse", "audio",
                               "https://example.test/cc0", "https://example.test/cc0.mp3",
                               license_name="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                               license_status="allowed_public_domain"),
                    MediaAsset("nc-audio", "underwater music", "Openverse", "audio",
                               "https://example.test/nc", "https://example.test/nc.mp3",
                               license_name="CC BY-NC", license_url="https://creativecommons.org/licenses/by-nc/4.0/",
                               license_status="noncommercial_only"),
                ]

        provider = AudioProvider()
        req = SearchRequest(required_visual="underwater ambience", providers=("audio",))
        with patch("src.media.search.PROVIDERS", {"openverse": provider}), \
             tempfile.TemporaryDirectory() as temp:
            rows = search_audio(req, providers=("openverse",), limit=4)
        self.assertEqual(["audio"], [provider.assert_audio])
        self.assertEqual(["cc0-audio"], [asset.asset_id for asset in rows])


class AdapterTests(unittest.TestCase):
    def test_nasa_item_without_verified_rights_is_not_auto_cleared(self):
        response = SimpleNamespace(status_code=200, url="https://images-api.nasa.gov/search",
            raise_for_status=lambda: None,
            json=lambda: {"collection": {"items": [{"data": [{"nasa_id": "N1",
                "title": "Earth", "description": "Earth from orbit", "center": "NASA"}],
                "href": "https://127.0.0.1/private.json"}]}})
        with patch("src.media.providers.requests.get", return_value=response) as get:
            rows = NasaProvider().search("Earth", "image", 1)
        self.assertEqual(1, len(rows))
        self.assertEqual("review_required", rows[0].license_status)
        self.assertEqual("", rows[0].direct_url)
        self.assertEqual(1, get.call_count)

    def test_coverr_is_disabled_cleanly_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = CoverrProvider()
            self.assertEqual([], provider.search("forest", "video", 4))
        self.assertEqual("disabled_no_key", provider.last_status)

    def test_loc_film_poster_is_not_mislabeled_as_a_downloadable_video(self):
        response = SimpleNamespace(status_code=200, url="https://www.loc.gov/search/",
            raise_for_status=lambda: None,
            json=lambda: {"results": [{"id": "film-1", "url": "https://www.loc.gov/item/film-1/",
                "title": "Historical film", "online_format": ["video"],
                "image_url": ["https://tile.loc.gov/storage-services/poster.jpg"], "rights": "Unknown"}]})
        with patch("src.media.providers.requests.get", return_value=response):
            rows = LocProvider().search("historical film", "video", 4)
        self.assertEqual(1, len(rows))
        self.assertEqual("video", rows[0].media_type)
        self.assertEqual("", rows[0].direct_url)

    def test_pexels_video_adapter_uses_official_endpoint_and_private_header(self):
        response = SimpleNamespace(
            status_code=200, url="https://api.pexels.com/v1/videos/search",
            raise_for_status=lambda: None,
            json=lambda: {"videos": [{"id": 3, "url": "https://www.pexels.com/video/3/",
                "duration": 12, "video_files": [{"file_type": "video/mp4", "width": 1920,
                                                   "height": 1080, "link": "https://cdn.pexels.test/a.mp4"}]}]},
        )
        with patch.dict("os.environ", {"PEXELS_API_KEY": "never-print-this"}), \
             patch("src.media.providers.requests.get", return_value=response) as get:
            rows = PexelsProvider().search("underwater trench", "video", 5)
        self.assertEqual("https://api.pexels.com/v1/videos/search", get.call_args.args[0])
        self.assertEqual("never-print-this", get.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual("provider_terms", rows[0].license_status)
        self.assertTrue(rows[0].publish_ready)

    def test_coverr_adapter_filters_premium_and_ai_content(self):
        response = SimpleNamespace(
            status_code=200, url="https://api.coverr.co/videos", raise_for_status=lambda: None,
            json=lambda: {"hits": [{"id": "free1", "title": "Volcanic landscape", "is_premium": False,
                                      "is_ai_generated": False, "max_width": 1920, "max_height": 1080,
                                      "urls": {"mp4_download": "https://storage.coverr.co/videos/free1?token=secret"}},
                                    {"id": "paid", "is_premium": True,
                                     "urls": {"mp4_download": "https://storage.coverr.co/videos/paid"}}]},
        )
        with patch.dict("os.environ", {"COVERR_API_KEY": "coverr-secret"}), \
             patch("src.media.providers.requests.get", return_value=response) as get:
            rows = CoverrProvider().search("volcano", "video", 4)
        self.assertEqual(1, len(rows))
        self.assertEqual("free1", rows[0].asset_id.removeprefix("coverr-"))
        self.assertEqual("coverr-secret", get.call_args.kwargs["headers"]["x-api-key"])
        self.assertEqual("https://coverr.co/license", rows[0].license_url)

    def test_signed_coverr_url_is_not_written_to_selected_asset_record(self):
        candidate = ("video", "coverr-free1", "https://storage.coverr.co/v?token=private",
                     "Coverr", "underwater ocean", {"asset_page": "https://coverr.co/videos/free1",
                     "license": "Coverr License", "license_url": "https://coverr.co/license",
                     "license_status": "provider_terms", "attribution_required": True,
                     "credit": "Ocean by creator on Coverr"})
        with tempfile.TemporaryDirectory() as temp, patch("src.footage._download", return_value=True):
            footage.reset()
            selected = footage._try([candidate], "underwater ocean", set(), temp, 1)
        self.assertEqual("", selected["direct_url"])
        self.assertNotIn("private", str(selected))


class DownloadIntegrityTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, content, url="https://cdn.example.test/file.mp4", status_code=200, headers=None):
            self.content = content
            self.url = url
            self.status_code = status_code
            self.headers = {"Content-Length": str(len(content)), **(headers or {})}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def raise_for_status(self):
            return None
        def close(self):
            return None
        def iter_content(self, chunk_size):
            yield self.content

    def test_valid_media_is_atomic_and_checksummed(self):
        payload = b"\x00\x00\x00\x18ftypmp42" + b"media-bytes"
        dns = [(None, None, None, None, ("8.8.8.8", 443))]
        with tempfile.TemporaryDirectory() as temp, \
             patch("src.media.downloader.socket.getaddrinfo", return_value=dns), \
             patch("src.media.downloader.requests.get", return_value=self.FakeResponse(payload)):
            target = Path(temp, "clip.mp4")
            record = download("https://cdn.example.test/file.mp4", target,
                              cache_directory=Path(temp, "cache"))
            self.assertTrue(target.is_file())
            self.assertEqual(len(payload), record["size_bytes"])
            self.assertEqual(64, len(record["sha256"]))

    def test_bad_signature_and_http_url_are_rejected_without_files(self):
        dns = [(None, None, None, None, ("8.8.8.8", 443))]
        with tempfile.TemporaryDirectory() as temp, \
             patch("src.media.downloader.socket.getaddrinfo", return_value=dns), \
             patch("src.media.downloader.requests.get", return_value=self.FakeResponse(b"not an mp4")):
            target = Path(temp, "bad.mp4")
            self.assertIsNone(download("https://cdn.example.test/bad.mp4", target))
            self.assertIsNone(download("http://example.test/bad.mp4", Path(temp, "insecure.mp4")))
        self.assertFalse(target.exists())
        self.assertFalse(Path(temp, "insecure.mp4").exists())

    def test_private_redirect_is_rejected_before_follow_up_request(self):
        redirect = self.FakeResponse(b"", status_code=302,
                                      headers={"Location": "https://127.0.0.1/private.mp4"})
        with tempfile.TemporaryDirectory() as temp, \
             patch("src.media.downloader.socket.getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]), \
             patch("src.media.downloader.requests.get", return_value=redirect) as get:
            target = Path(temp, "blocked.mp4")
            self.assertIsNone(download("https://cdn.example.test/file.mp4", target))
        self.assertEqual(1, get.call_count)

    def test_hostname_resolving_to_private_address_is_rejected(self):
        dns = [(None, None, None, None, ("10.0.0.8", 443))]
        with tempfile.TemporaryDirectory() as temp, \
             patch("src.media.downloader.socket.getaddrinfo", return_value=dns), \
             patch("src.media.downloader.requests.get") as get:
            self.assertIsNone(download("https://media.example.test/clip.mp4", Path(temp, "clip.mp4")))
            get.assert_not_called()

    def test_verified_asset_is_reused_without_a_second_network_request(self):
        payload = bytes([0, 0, 0, 24]) + b"ftypmp42media-bytes"
        with tempfile.TemporaryDirectory() as temp, \
             patch("src.media.downloader.socket.getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]), \
             patch("src.media.downloader.requests.get", return_value=self.FakeResponse(payload)) as get:
            cache = Path(temp, "cache")
            first = download("https://cdn.example.test/file.mp4", Path(temp, "first.mp4"),
                             cache_key="asset-1", cache_directory=cache,
                             cache_max_total_bytes=1 << 20)
            second = download("https://cdn.example.test/rotated-url.mp4", Path(temp, "second.mp4"),
                              cache_key="asset-1", cache_directory=cache,
                              cache_max_total_bytes=1 << 20)
            self.assertTrue(Path(temp, "second.mp4").is_file())
        self.assertEqual(1, get.call_count)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])


class AttributionTests(unittest.TestCase):
    def test_media_credits_include_asset_creator_license_and_original_link(self):
        from src.media.search import research_credits
        rows = research_credits([{
            "asset_id": "commons-1", "title": "Ocean map", "creator": "A. Cartographer",
            "source": "Wikimedia Commons", "source_url": "https://commons.wikimedia.org/wiki/File:Map",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "license_status": "allowed_attribution", "credit": "Ocean map by A. Cartographer",
        }])
        self.assertEqual("A. Cartographer", rows[0]["creator"])
        self.assertIn("creativecommons.org/licenses/by/4.0", rows[0]["license_url"])
        self.assertTrue(rows[0]["original_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
