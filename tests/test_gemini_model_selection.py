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
        common._GEMINI_MODEL_COUNTS_LOGGED = False
        common._GEMINI_TOTAL_CALLS = 0
        common._GEMINI_DISABLED_MODELS.clear()
        common._GEMINI_DISABLED_PAIRS.clear()
        common._GEMINI_429_KEYS_BY_MODEL.clear()
        common._GEMINI_KEY_COUNT_LOGGED = False
        common._GEMINI_PAUSED_REASON = ""

    def tearDown(self):
        common._MODELS = {}
        common._MODEL_CATALOG = None
        common._MODEL_DISCOVERY_FAILED = False
        common._GEMINI_MODEL_COUNTS.clear()
        common._GEMINI_MODEL_COUNTS_LOGGED = False
        common._GEMINI_TOTAL_CALLS = 0
        common._GEMINI_DISABLED_MODELS.clear()
        common._GEMINI_DISABLED_PAIRS.clear()
        common._GEMINI_429_KEYS_BY_MODEL.clear()

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
            {"name": "models/gemini-3.6-omni", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        ], "page-two")
        second_page = self._page([
            {"name": "models/gemini-3.5-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.7-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
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
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3.7-flash",
            "gemini-3.8-flash",
            "gemini-flash-latest",
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
                         {key: common._GEMINI_MODEL_COUNTS["model-404"][key]
                          for key in ("success", "404", "429")})
        self.assertEqual({"success": 0, "404": 0, "429": 1},
                         {key: common._GEMINI_MODEL_COUNTS["model-429"][key]
                          for key in ("success", "404", "429")})
        self.assertEqual({"success": 1, "404": 0, "429": 0},
                         {key: common._GEMINI_MODEL_COUNTS["model-ok"][key]
                          for key in ("success", "404", "429")})

    @staticmethod
    def _generation_response(status, message="", payload=None):
        if payload is None:
            payload = {"error": {"message": message}}
        if status == 200 and payload is None:
            payload = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
        return SimpleNamespace(status_code=status, json=lambda: payload)

    @staticmethod
    def _keys(count):
        values = {"GEMINI_API_KEY": "key-1"}
        values.update({f"GEMINI_API_KEY_{i}": f"key-{i}" for i in range(2, count + 1)})
        return values

    def test_limit_zero_or_404_skips_the_model_for_the_run(self):
        keys = self._keys(2)
        responses = [
            self._generation_response(429, "limit: 0"),
            self._generation_response(200, payload={
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
            }),
        ]
        with patch.object(common, "env", side_effect=lambda name, required=False: keys.get(name, "")), \
             patch.object(common, "_model_list", return_value=["model-zero", "model-ok"]), \
             patch.object(common.requests, "post", side_effect=responses) as post:
            self.assertEqual({"ok": True}, common._gemini("prompt"))
        self.assertEqual(2, post.call_count)
        self.assertIn("model-zero", common._GEMINI_DISABLED_MODELS)
        self.assertIn("/model-ok:", post.call_args.args[0])

        common._GEMINI_DISABLED_MODELS.clear()
        common._GEMINI_TOTAL_CALLS = 0
        common._GEMINI_MODEL_COUNTS.clear()
        responses = [
            self._generation_response(404, "not found"),
            self._generation_response(200, payload={
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
            }),
        ]
        with patch.object(common, "env", side_effect=lambda name, required=False: keys.get(name, "")), \
             patch.object(common, "_model_list", return_value=["model-404", "model-after-404"]), \
             patch.object(common.requests, "post", side_effect=responses) as post:
            self.assertEqual({"ok": True}, common._gemini("prompt"))
        self.assertEqual(2, post.call_count)
        self.assertIn("model-404", common._GEMINI_DISABLED_MODELS)

    def test_per_day_disables_pair_and_per_minute_waits_then_rotates(self):
        keys = self._keys(2)
        success = self._generation_response(200, payload={
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
        })
        per_day = self._generation_response(429, "quota per day")
        with patch.object(common, "env", side_effect=lambda name, required=False: keys.get(name, "")), \
             patch.object(common, "_model_list", return_value=["model-day"]), \
             patch.object(common.requests, "post", side_effect=[per_day, success, success]) as post:
            self.assertEqual({"ok": True}, common._gemini("first"))
            self.assertIn((1, "model-day"), common._GEMINI_DISABLED_PAIRS)
            self.assertEqual({"ok": True}, common._gemini("second"))
        self.assertEqual(3, post.call_count)
        self.assertEqual(["key-1", "key-2", "key-2"], [
            call.kwargs["params"]["key"] for call in post.call_args_list
        ])

        common._GEMINI_DISABLED_PAIRS.clear()
        common._GEMINI_DISABLED_MODELS.clear()
        common._GEMINI_429_KEYS_BY_MODEL.clear()
        common._GEMINI_TOTAL_CALLS = 0
        common._GEMINI_MODEL_COUNTS.clear()
        minute = self._generation_response(429, payload={
            "error": {"message": "quota per minute", "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "2s"}
            ]}
        })
        with patch.object(common, "env", side_effect=lambda name, required=False: keys.get(name, "")), \
             patch.object(common, "_model_list", return_value=["model-minute"]), \
             patch.object(common.requests, "post", side_effect=[minute, success]) as post, \
             patch.object(common.time, "sleep") as sleep:
            self.assertEqual({"ok": True}, common._gemini("minute"))
        self.assertEqual([2.0], [call.args[0] for call in sleep.call_args_list])
        self.assertEqual(["key-1", "key-2"], [
            call.kwargs["params"]["key"] for call in post.call_args_list
        ])

    def test_all_keys_429_disables_model_and_503_retries_twice(self):
        keys = self._keys(5)
        quota = self._generation_response(429, "quota exceeded")
        success = self._generation_response(200, payload={
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
        })
        with patch.object(common, "env", side_effect=lambda name, required=False: keys.get(name, "")), \
             patch.object(common, "_model_list", return_value=["model-quota", "model-after-quota"]), \
             patch.object(common.requests, "post", side_effect=[quota] * 5 + [success]) as post:
            self.assertEqual({"ok": True}, common._gemini("quota"))
        self.assertEqual(6, post.call_count)
        self.assertIn("model-quota", common._GEMINI_DISABLED_MODELS)

        common._GEMINI_DISABLED_MODELS.clear()
        common._GEMINI_429_KEYS_BY_MODEL.clear()
        common._GEMINI_TOTAL_CALLS = 0
        common._GEMINI_MODEL_COUNTS.clear()
        overload = self._generation_response(503, "busy")
        with patch.object(common, "env", side_effect=lambda name, required=False: keys.get(name, "")), \
             patch.object(common, "_model_list", return_value=["model-busy", "model-ready"]), \
             patch.object(common.requests, "post", side_effect=[overload] * 3 + [success]) as post, \
             patch.object(common.random, "uniform", return_value=3.0), \
             patch.object(common.time, "sleep") as sleep:
            self.assertEqual({"ok": True}, common._gemini("busy"))
        self.assertEqual(4, post.call_count)
        self.assertEqual([3.0, 3.0], [call.args[0] for call in sleep.call_args_list])
        self.assertEqual(3, common._GEMINI_MODEL_COUNTS["model-busy"]["503"])
        self.assertEqual(1, common._GEMINI_MODEL_COUNTS["model-ready"]["success"])

    def test_all_gemini_failures_fall_back_to_groq_then_openrouter(self):
        with patch.object(common, "_gemini", side_effect=common.GeminiUnavailableError("quota")), \
             patch.object(common, "_groq", side_effect=common.GroqUnavailableError("no Groq key")) as groq, \
             patch.object(common, "_openrouter", return_value={"topic": "fallback"}) as openrouter:
            result = common.gemini("topic prompt")
        self.assertEqual({"topic": "fallback"}, result)
        groq.assert_called_once()
        openrouter.assert_called_once()


if __name__ == "__main__":
    unittest.main()

