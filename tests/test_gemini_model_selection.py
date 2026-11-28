import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from src import common


class GeminiModelSelectionTests(unittest.TestCase):
    def setUp(self):
        common._MODELS = {}
        common._MODEL_CATALOG = None
        common._MODEL_DISCOVERY_FAILED = False
        common._GEMINI_MODEL_COUNTS.clear()

    def tearDown(self):
        common._MODELS = {}
        common._MODEL_CATALOG = None
        common._MODEL_DISCOVERY_FAILED = False
        common._GEMINI_MODEL_COUNTS.clear()

    @staticmethod
    def _page(models, next_token=None):
        payload = {"models": models}
        if next_token:
            payload["nextPageToken"] = next_token
        return SimpleNamespace(
            json=lambda: payload,
            raise_for_status=lambda: None,
        )

    def test_paginates_fails_over_maps_and_caches_per_call_mode(self):
        def denied():
            raise RuntimeError("permission denied")

        first_page = self._page([
            {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["countTokens"]},
            {"name": "models/gemini-3.1-flash-lite-latest", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        ], "page-two")
        second_page = self._page([
            {"name": "models/gemini-3.5-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        ])
        output = io.StringIO()
        with patch("src.common.requests.get", side_effect=[denied, first_page, second_page]) as get, \
             redirect_stdout(output):
            general = common._model_list([(1, "key-one"), (2, "key-two")])
            grounding = common._model_list([(1, "key-one"), (2, "key-two")], grounding=True)

        self.assertEqual([
            "gemini-3.5-flash-lite-preview",
            "gemini-3.1-flash-lite-latest",
            "gemini-2.5-flash-lite",
        ], general)
        self.assertEqual(["gemini-2.5-flash", "gemini-2.5-flash-lite"], grounding)
        self.assertEqual(3, get.call_count)
        self.assertEqual("key-one", get.call_args_list[0].kwargs["params"]["key"])
        self.assertEqual("key-two", get.call_args_list[1].kwargs["params"]["key"])
        self.assertEqual("page-two", get.call_args_list[2].kwargs["params"]["pageToken"])
        self.assertIn("model mapped: gemini-3.5-flash-lite -> gemini-3.5-flash-lite-preview", output.getvalue())
        self.assertIn("model mapped: gemini-3.1-flash-lite -> gemini-3.1-flash-lite-latest", output.getvalue())
        self.assertNotIn("gemini-2.5-pro", general + grounding)
        self.assertNotIn("gemini-2-flash", general + grounding)
        self.assertNotIn("gemini-2-flash-lite", general + grounding)

    def test_discovery_failure_uses_ordered_fallback_without_raising(self):
        output = io.StringIO()
        with patch("src.common.requests.get", side_effect=RuntimeError("offline")) as get, \
             redirect_stdout(output):
            general = common._model_list([(1, "key-one"), (3, "key-three")])
            grounding = common._model_list([(1, "key-one"), (3, "key-three")], grounding=True)

        self.assertEqual(list(common.GEMINI_MODEL_PREFERENCES["general"]), general)
        self.assertEqual(list(common.GEMINI_MODEL_PREFERENCES["grounding"]), grounding)
        self.assertEqual(2, get.call_count)
        self.assertIn("WARNING: ListModels failed with all configured keys", output.getvalue())

    def test_outcome_logger_reports_success_404_and_429_counts(self):
        common._GEMINI_MODEL_COUNTS["gemini-test"] = {"success": 2, "404": 1, "429": 3}
        output = io.StringIO()
        with redirect_stdout(output):
            common._log_gemini_model_counts()
        self.assertIn("gemini-test: success=2, 404=1, 429=3", output.getvalue())

    def test_generation_updates_per_model_outcome_counts(self):
        class Response:
            def __init__(self, status, payload):
                self.status_code = status
                self._payload = payload

            def json(self):
                return self._payload

        responses = [
            Response(404, {"error": {"message": "not found"}}),
            Response(429, {"error": {"message": "quota per minute"}}),
            Response(200, {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}),
        ]
        with patch.object(common, "env", side_effect=lambda name, required=False:
                          "test-key" if name == "GEMINI_API_KEY" else ""), \
             patch.object(common, "_model_list", return_value=["model-404", "model-429", "model-ok"]), \
             patch.object(common.requests, "post", side_effect=responses):
            result = common._gemini("prompt")

        self.assertEqual({"ok": True}, result)
        self.assertEqual({"success": 0, "404": 1, "429": 0},
                         common._GEMINI_MODEL_COUNTS["model-404"])
        self.assertEqual({"success": 0, "404": 0, "429": 1},
                         common._GEMINI_MODEL_COUNTS["model-429"])
        self.assertEqual({"success": 1, "404": 0, "429": 0},
                         common._GEMINI_MODEL_COUNTS["model-ok"])


if __name__ == "__main__":
    unittest.main()
