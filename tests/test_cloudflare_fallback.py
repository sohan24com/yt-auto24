import unittest
from unittest.mock import patch

from src import common


class CloudflareFallbackTests(unittest.TestCase):
    def test_cloudflare_uses_workers_ai_and_parses_json(self):
        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}]
        }
        env_values = {
            "CLOUDFLARE_API_TOKEN": "test-token",
            "CLOUDFLARE_ACCOUNT_ID": "test-account",
        }
        with patch.object(common, "env", side_effect=lambda name, required=True: env_values.get(name, "")), \
             patch.object(common.requests, "post", return_value=response) as post:
            result = common._cloudflare("Return JSON", json_out=True)
        self.assertEqual(result, {"ok": True})
        args, kwargs = post.call_args
        self.assertIn("/accounts/test-account/ai/v1/chat/completions", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(kwargs["json"]["model"], "@cf/meta/llama-3.1-8b-instruct")

    def test_cloudflare_is_skipped_without_both_secrets(self):
        with patch.object(common, "env", return_value=""):
            with self.assertRaises(common.CloudflareUnavailableError):
                common._cloudflare("prompt")

    def test_cloudflare_follows_gemini_and_groq_for_ordinary_text(self):
        with patch.object(common, "_gemini", side_effect=common.GeminiUnavailableError("gemini quota")), \
             patch.object(common, "_groq", side_effect=common.GroqUnavailableError("groq quota")), \
             patch.object(common, "_cloudflare", return_value={"script": "ok"}) as cf:
            result = common.gemini("prompt")
        self.assertEqual(result, {"script": "ok"})
        cf.assert_called_once()

    def test_grounded_calls_do_not_use_ungrounded_fallbacks(self):
        with patch.object(common, "_gemini", side_effect=common.GeminiUnavailableError("gemini quota")), \
             patch.object(common, "_groq") as groq, \
             patch.object(common, "_cloudflare") as cf:
            with self.assertRaises(common.GeminiUnavailableError):
                common.gemini("verify claim", grounding=True)
        groq.assert_not_called()
        cf.assert_not_called()

    def test_openrouter_uses_free_router_and_parses_json(self):
        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}]
        }
        with patch.object(common, "env", return_value="test-openrouter-key"), \
             patch.object(common.requests, "post", return_value=response) as post:
            result = common._openrouter("Return JSON", json_out=True)
        self.assertEqual(result, {"ok": True})
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "openrouter/free")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-openrouter-key")

    def test_openrouter_follows_unavailable_providers(self):
        with patch.object(common, "_gemini", side_effect=common.GeminiUnavailableError("gemini quota")), \
             patch.object(common, "_groq", side_effect=common.GroqUnavailableError("groq quota")), \
             patch.object(common, "_cloudflare", side_effect=common.CloudflareUnavailableError("cloudflare quota")), \
             patch.object(common, "_openrouter", return_value={"script": "ok"}) as router:
            result = common.gemini("prompt")
        self.assertEqual(result, {"script": "ok"})
        router.assert_called_once()


if __name__ == "__main__":
    unittest.main()
