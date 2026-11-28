import atexit, json, os, re, time, datetime as dt, tempfile
from pathlib import Path
import requests, yaml

ROOT = Path(__file__).resolve().parent.parent
MEM = ROOT / "memory"
(MEM / "videos").mkdir(parents=True, exist_ok=True)


def cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def env(name, required=True):
    v = os.environ.get(name, "")
    if required and not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def now():
    return dt.datetime.now(dt.timezone.utc)


# ---------- memory (one json file per video => no merge conflicts) ----------
def _save_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=1, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_videos():
    videos = []
    for path in sorted((MEM / "videos").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("video_id"):
                videos.append(record)
            else:
                print(f"ignoring invalid video memory record: {path.name}")
        except (OSError, ValueError, TypeError) as exc:
            print(f"ignoring unreadable video memory record {path.name}: {type(exc).__name__}")
    return videos


def save_video(rec):
    if not rec.get("video_id"):
        raise ValueError("video memory record requires video_id")
    _save_json_atomic(MEM / "videos" / f"{rec['video_id']}.json", rec)


def load_insights():
    p = MEM / "insights.json"
    if not p.exists():
        return {"weights": None, "lessons": []}
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"weights": None, "lessons": []}
    except (OSError, ValueError, TypeError) as exc:
        print(f"ignoring unreadable analytics insights: {type(exc).__name__}")
        return {"weights": None, "lessons": []}


def save_insights(d):
    _save_json_atomic(MEM / "insights.json", d)


# ---------- gemini ----------
def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no json found")
    s = min(starts)
    e = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[s:e + 1])


def _provider_error_hint(response):
    """Expose machine-readable provider error fields; never log bodies or secrets."""
    try:
        payload = response.json()
    except Exception:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        errors = payload.get("errors") if isinstance(payload, dict) else None
        error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    parts = []
    for name in ("code", "type", "param"):
        value = error.get(name) if isinstance(error, dict) else None
        if value is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", str(value)):
            parts.append(f"{name}={value}")
    return f" ({', '.join(parts)})" if parts else ""


def _provider_error_message(response, secrets=()):
    """Return a short, sanitized provider message without exposing credentials."""
    try:
        payload = response.json()
    except Exception:
        return "provider error message unavailable"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        errors = payload.get("errors") if isinstance(payload, dict) else None
        error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message.strip():
        return "provider error message unavailable"
    message = redact_secrets(message)
    for secret in sorted({str(value).strip() for value in secrets if value}, key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"\s+", " ", message).strip()
    return message[:500] or "provider error message unavailable"


# Keep this allowlist limited to models whose standard text API has a documented free tier.
# Model discovery only decides which supported allowlisted IDs to try; it never enables paid IDs.
GEMINI_MODEL_PREFERENCES = {
    "general": ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"),
    "grounding": ("gemini-2.5-flash", "gemini-2.5-flash-lite"),
}
GEMINI_FALLBACK_MODELS = GEMINI_MODEL_PREFERENCES["general"]
GEMINI_BLOCKED_MODELS = ("gemini-2-flash", "gemini-2-flash-lite")

# Per-mode selected order and the one-time ListModels catalog for this process.
_MODELS = {}
_MODEL_CATALOG = None
_MODEL_DISCOVERY_FAILED = False
_GEMINI_MODEL_COUNTS = {}
_GEMINI_MODEL_COUNTS_LOGGED = False


def _model_is_allowed(name):
    lowered = name.lower()
    if "pro" in lowered:
        return False
    return not any(lowered == blocked or lowered.startswith(blocked + "-")
                   for blocked in GEMINI_BLOCKED_MODELS)


def _list_models(keys):
    """Fetch every ListModels page using the first credential that completes discovery."""
    global _MODEL_CATALOG, _MODEL_DISCOVERY_FAILED
    if _MODEL_CATALOG is not None:
        return _MODEL_CATALOG

    for key_index, key in keys:
        catalog = {}
        page_token = None
        seen_tokens = set()
        try:
            while True:
                params = {"key": key, "pageSize": 1000}
                if page_token:
                    params["pageToken"] = page_token
                response = requests.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params=params, timeout=30)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("invalid ListModels response")
                for model in payload.get("models", []):
                    if not isinstance(model, dict):
                        continue
                    name = str(model.get("name", "")).removeprefix("models/")
                    methods = model.get("supportedGenerationMethods", [])
                    if name and isinstance(methods, list) and "generateContent" in methods:
                        catalog[name] = methods
                next_token = payload.get("nextPageToken")
                if not next_token:
                    _MODEL_CATALOG = catalog
                    _MODEL_DISCOVERY_FAILED = False
                    return _MODEL_CATALOG
                if next_token in seen_tokens:
                    raise ValueError("repeated ListModels page token")
                seen_tokens.add(next_token)
                page_token = next_token
        except Exception as exc:
            print(f"Gemini ListModels failed with key {key_index}/5: {type(exc).__name__}")

    _MODEL_CATALOG = {}
    _MODEL_DISCOVERY_FAILED = True
    return _MODEL_CATALOG


def _select_preferred_models(catalog, preferred):
    selected = []
    for requested in preferred:
        if not _model_is_allowed(requested):
            print(f"model skipped (not in ListModels): {requested}")
            continue
        match = requested if requested in catalog else None
        if match is None:
            latest = requested + "-latest"
            preview = requested + "-preview"
            dated_previews = sorted(name for name in catalog
                                    if name.startswith(preview + "-"))
            match = latest if latest in catalog else preview if preview in catalog else (
                dated_previews[0] if dated_previews else None)
            if match:
                print(f"model mapped: {requested} -> {match}")
        if match is None:
            print(f"model skipped (not in ListModels): {requested}")
            continue
        if not _model_is_allowed(match):
            continue
        if match not in selected:
            selected.append(match)
    return selected


def _model_list(keys, grounding=False):
    """Return the cached preference order verified against the ListModels catalog."""
    global _MODELS
    mode = "grounding" if grounding else "general"
    if not isinstance(_MODELS, dict):
        _MODELS = {}
    if mode in _MODELS:
        return _MODELS[mode]

    if isinstance(keys, str):
        keys = [(1, keys)]
    _list_models(keys)
    preferred = GEMINI_MODEL_PREFERENCES[mode]
    if not _MODEL_DISCOVERY_FAILED:
        models = _select_preferred_models(_MODEL_CATALOG, preferred)
    else:
        print("WARNING: ListModels failed with all configured keys; using the "
              f"unverified {mode} Gemini model preference order.")
        models = [name for name in preferred if _model_is_allowed(name)]
    _MODELS[mode] = models
    for model in models:
        _GEMINI_MODEL_COUNTS.setdefault(model, {"success": 0, "404": 0, "429": 0})
    print(f"Gemini {mode} models to try: {models}")
    return models


def _log_gemini_model_counts():
    global _GEMINI_MODEL_COUNTS_LOGGED
    if _GEMINI_MODEL_COUNTS_LOGGED:
        return
    _GEMINI_MODEL_COUNTS_LOGGED = True
    if not _GEMINI_MODEL_COUNTS:
        print("Gemini model outcomes: no model attempts")
        return
    details = "; ".join(
        f"{model}: success={counts['success']}, 404={counts['404']}, 429={counts['429']}"
        for model, counts in _GEMINI_MODEL_COUNTS.items())
    print("Gemini model outcomes: " + details)


atexit.register(_log_gemini_model_counts)


class GeminiUnavailableError(RuntimeError):
    """Raised after bounded attempts across the configured Gemini keys and models."""


# Gemini is available at the start of each run; failures may pause it later.
_GEMINI_PAUSED_REASON = ""
_GEMINI_KEY_COUNT_LOGGED = False
_GEMINI_KEY_COUNT_LOGGED = False


def _gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9,
           tries=None, request_timeout=90, total_timeout=300, max_output_tokens=32000):
    global _GEMINI_PAUSED_REASON, _GEMINI_KEY_COUNT_LOGGED
    if _GEMINI_PAUSED_REASON:
        raise GeminiUnavailableError(
            f"Gemini paused for this run after {_GEMINI_PAUSED_REASON}"
        )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max(128, min(32000, int(max_output_tokens)))},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if grounding:
        body["tools"] = [{"google_search": {}}]
    elif json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"

    key_names = ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
                 "GEMINI_API_KEY_4", "GEMINI_API_KEY_5")
    keys, seen = [], set()
    for key_index, name in enumerate(key_names, start=1):
        key = env(name, False).strip()
        if key and key not in seen:
            keys.append((key_index, key))
            seen.add(key)
    if not _GEMINI_KEY_COUNT_LOGGED:
        print(f"Gemini API keys found: {len(keys)} of {len(key_names)}")
        _GEMINI_KEY_COUNT_LOGGED = True
    if not keys:
        raise GeminiUnavailableError("No Gemini API keys are configured")

    models = _model_list(keys, grounding=grounding)
    if not models:
        raise GeminiUnavailableError("No Gemini models are available to try")

    deadline = time.monotonic() + max(1.0, float(total_timeout))
    attempts = 0
    last = "no usable response"
    status_counts = {}
    permission_denied = False

    for model in models:
        overloaded = False
        for key_index, key in keys:
            if tries is not None and attempts >= tries:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempts += 1
            gc = dict(body["generationConfig"])
            if not model.startswith("gemini-3"):
                gc["temperature"] = temperature
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = requests.post(
                    url, params={"key": key},
                    json=dict(body, generationConfig=gc),
                    timeout=min(float(request_timeout), remaining),
                )
                if r.status_code == 200:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                    result = extract_json(text) if json_out else text
                    _GEMINI_MODEL_COUNTS.setdefault(
                        model, {"success": 0, "404": 0, "429": 0})["success"] += 1
                    return result

                status = int(r.status_code)
                status_counts[status] = status_counts.get(status, 0) + 1
                if status in (404, 429):
                    _GEMINI_MODEL_COUNTS.setdefault(
                        model, {"success": 0, "404": 0, "429": 0})[str(status)] += 1
                kinds = {
                    400: "request format or model configuration",
                    401: "API key authentication",
                    403: "project permission or free-tier access",
                    404: "model or endpoint configuration",
                    429: "quota or rate limit",
                }
                kind = kinds.get(status, "temporary service failure" if status >= 500 else "HTTP error")
                last = f"{model}: HTTP {status} ({kind})"
                if status == 429:
                    message = _provider_error_message(r, (secret for _, secret in keys))
                    print(f"Gemini rate limited: key {key_index}/5, model {model}, "
                          f"HTTP 429, provider error: {message}")
                else:
                    print(f"Gemini candidate unavailable: HTTP {status} ({kind})")

                if status == 503 or status in (500, 502, 504):
                    # Capacity errors are model-wide; move to another model instead of
                    # asking every account to hit the same overloaded endpoint.
                    overloaded = True
                    break
                if status in (400, 404):
                    # Request/model issues are not repaired by rotating credentials.
                    break
                if status == 403:
                    # Permission/free-tier failures are configuration errors, not rate limits.
                    permission_denied = True
                    break
                if status == 429:
                    # Try the other explicitly configured Gemini credentials.
                    # These may belong to separate Google projects; stay Gemini-only.
                    continue

                if status == 401:
                    # A distinct configured key can fix an invalid credential.
                    continue
                if status >= 400:
                    break
            except requests.Timeout:
                last = f"{model}: request timed out"
                continue
            except Exception as ex:
                last = f"{model}: {type(ex).__name__}"
                continue

        if permission_denied:
            break
        if tries is not None and attempts >= tries:
            break
        if _GEMINI_PAUSED_REASON:
            break
        if deadline - time.monotonic() <= 0:
            break
        if overloaded:
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))

    summary = ", ".join(f"HTTP {code} x{count}" for code, count in sorted(status_counts.items()))
    if not summary:
        summary = f"{attempts} request attempt(s)"
    if status_counts.get(429):
        _GEMINI_PAUSED_REASON = "quota or rate limit"
    raise GeminiUnavailableError(
        f"Gemini unavailable after {attempts} bounded attempt(s) ({summary}); last response: {last}"
    )


GROQ_MODELS = (
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
)

GROQ_GROUNDED_MODELS = (
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
)


class GroqUnavailableError(RuntimeError):
    """Raised after bounded attempts across configured Groq API keys and models."""


def _groq(prompt, system=None, json_out=True, temperature=0.9, grounding=False,
          request_timeout=20, total_timeout=90, max_completion_tokens=4096):
    keys = list(dict.fromkeys(k.strip() for k in (
        env("GROQ_API_KEY", False), env("GROQ_API_KEY_2", False),
    ) if k and k.strip()))
    if not keys:
        raise GroqUnavailableError("No Groq API keys are configured")

    if grounding:
        # Use Groq's documented Responses API for server-side browser search.
        # The Chat Completions browser-search path returned output_parse_failed
        # for grounded JSON on recent production runs.
        deadline = time.monotonic() + max(1.0, float(total_timeout))
        last = "no usable response"
        for model in GROQ_GROUNDED_MODELS:
            for key in keys:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                instructions = (system or "") + (
                    "\\nUse browser search. Cite only URLs actually returned by search. "
                    "Return the requested JSON object only; never invent a URL or source."
                )
                payload = {
                    "model": model,
                    "instructions": instructions.strip(),
                    "input": prompt,
                    "tools": [{"type": "browser_search"}],
                    "tool_choice": "required",
                    "max_output_tokens": max(128, min(4096, int(max_completion_tokens))),
                }
                try:
                    response = requests.post(
                        "https://api.groq.com/openai/v1/responses",
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                        json=payload,
                        timeout=min(float(request_timeout), remaining),
                    )
                    if response.status_code != 200:
                        hint = _provider_error_hint(response)
                        last = f"{model}: HTTP {response.status_code}{hint}"
                        print(f"Groq browser-search candidate unavailable: {model} HTTP {response.status_code}{hint}")
                        if response.status_code in (401, 403):
                            continue
                        break
                    data = response.json()
                    content = data.get("output_text") or ""
                    if not content:
                        parts = []
                        for item in data.get("output", []):
                            if item.get("type") == "message":
                                for part in item.get("content", []):
                                    if part.get("type") == "output_text":
                                        parts.append(part.get("text", ""))
                        content = "\\n".join(parts)
                    if content.strip():
                        return extract_json(content) if json_out else content
                    last = f"{model}: empty response"
                except requests.Timeout:
                    last = f"{model}: request timed out"
                    continue
                except Exception as exc:
                    last = f"{model}: {type(exc).__name__}"
                    continue
        raise GroqUnavailableError(f"Groq browser-search Responses API failed: {last}")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    deadline = time.monotonic() + max(1.0, float(total_timeout))
    attempts = 0
    status_counts = {}
    last = "no usable response"

    models = GROQ_GROUNDED_MODELS if grounding else GROQ_MODELS
    for model in models:
        for key in keys:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempts += 1
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_completion_tokens": max(128, min(4096, int(max_completion_tokens))),
            }
            if grounding:
                payload["tools"] = [{"type": "browser_search"}]
                payload["tool_choice"] = "required"
            elif json_out:
                payload["response_format"] = {"type": "json_object"}
            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=min(float(request_timeout), remaining),
                )
                if response.status_code == 200:
                    content = response.json()["choices"][0]["message"].get("content") or ""
                    if not content.strip():
                        last = f"{model}: empty response"
                        continue
                    return extract_json(content) if json_out else content

                status = int(response.status_code)
                status_counts[status] = status_counts.get(status, 0) + 1
                hint = _provider_error_hint(response)
                last = f"{model}: HTTP {status}{hint}"
                print(f"Groq candidate unavailable: {model} HTTP {status}{hint}")
                if status in (401, 403):
                    # Rotate a rejected credential; do not log or expose its value.
                    continue
                # A 429/capacity error moves to another model without replaying the
                # same organization's shared quota through another key.
                break
            except requests.Timeout:
                last = f"{model}: request timed out"
                continue
            except Exception as ex:
                last = f"{model}: {type(ex).__name__}"
                continue

        if deadline - time.monotonic() <= 0:
            break

    summary = ", ".join(f"HTTP {code} x{count}" for code, count in sorted(status_counts.items()))
    if not summary:
        summary = f"{attempts} request attempt(s)"
    raise GroqUnavailableError(
        f"Groq unavailable after {attempts} bounded attempt(s) ({summary}); last response: {last}"
    )



class CloudflareUnavailableError(RuntimeError):
    """Raised when the optional Cloudflare Workers AI fallback cannot answer."""


def _cloudflare(prompt, system=None, json_out=True, temperature=0.9,
                request_timeout=30):
    """Call one bounded Workers AI text model; never log credentials or response bodies."""
    token = env("CLOUDFLARE_API_TOKEN", False).strip()
    account_id = env("CLOUDFLARE_ACCOUNT_ID", False).strip()
    if not token or not account_id:
        raise CloudflareUnavailableError("Cloudflare credentials are not configured")

    messages = []
    if system:
        instruction = system
        if json_out:
            instruction += "\nReturn valid JSON only, with no Markdown fences or extra text."
        messages.append({"role": "system", "content": instruction})
    user_prompt = prompt
    if json_out and not system:
        user_prompt += "\nReturn valid JSON only, with no Markdown fences or extra text."
    messages.append({"role": "user", "content": user_prompt})

    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           "/ai/v1/chat/completions")
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"model": "@cf/google/gemma-4-26b-a4b-it",
                  "messages": messages, "temperature": temperature,
                  "max_tokens": 4096},
            timeout=float(request_timeout),
        )
        if response.status_code != 200:
            hint = _provider_error_hint(response)
            print(f"Cloudflare candidate unavailable: HTTP {response.status_code}{hint}")
            raise CloudflareUnavailableError(
                f"Cloudflare Workers AI returned HTTP {response.status_code}{hint}"
            )
        content = (response.json().get("choices", [{}])[0]
                   .get("message", {}).get("content") or "")
        if not content.strip():
            raise CloudflareUnavailableError("Cloudflare returned an empty response")
        return extract_json(content) if json_out else content
    except CloudflareUnavailableError:
        raise
    except requests.Timeout as exc:
        raise CloudflareUnavailableError("Cloudflare request timed out") from exc
    except Exception as exc:
        raise CloudflareUnavailableError(
            f"Cloudflare request failed: {type(exc).__name__}"
        ) from exc


class OpenRouterUnavailableError(RuntimeError):
    """Raised when the optional free OpenRouter fallback cannot answer."""


def _openrouter(prompt, system=None, json_out=True, temperature=0.9,
                request_timeout=30):
    """Use OpenRouter's free-model router only; do not send calls to paid models."""
    key = env("OPENROUTER_API_KEY", False).strip()
    if not key:
        raise OpenRouterUnavailableError("OpenRouter credentials are not configured")

    messages = []
    if system:
        instruction = system
        if json_out:
            instruction += "\\nReturn valid JSON only, with no Markdown fences or extra text."
        messages.append({"role": "system", "content": instruction})
    user_prompt = prompt
    if json_out and not system:
        user_prompt += "\\nReturn valid JSON only, with no Markdown fences or extra text."
    messages.append({"role": "user", "content": user_prompt})

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": "openrouter/free", "messages": messages,
                  "temperature": temperature, "max_tokens": 4096},
            timeout=float(request_timeout),
        )
        if response.status_code != 200:
            hint = _provider_error_hint(response)
            print(f"OpenRouter candidate unavailable: HTTP {response.status_code}{hint}")
            raise OpenRouterUnavailableError(
                f"OpenRouter free router returned HTTP {response.status_code}{hint}"
            )
        content = (response.json().get("choices", [{}])[0]
                   .get("message", {}).get("content") or "")
        if not content.strip():
            raise OpenRouterUnavailableError("OpenRouter returned an empty response")
        return extract_json(content) if json_out else content
    except OpenRouterUnavailableError:
        raise
    except requests.Timeout as exc:
        raise OpenRouterUnavailableError("OpenRouter request timed out") from exc
    except Exception as exc:
        raise OpenRouterUnavailableError(
            f"OpenRouter request failed: {type(exc).__name__}"
        ) from exc

def gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9,
           tries=None, request_timeout=90, total_timeout=300,
           max_output_tokens=32000, max_completion_tokens=4096):
    """Use only configured Gemini API keys and Gemini models for text generation."""
    return _gemini(
        prompt, system=system, grounding=grounding, json_out=json_out,
        temperature=temperature, tries=tries,
        request_timeout=request_timeout, total_timeout=total_timeout,
        max_output_tokens=max_output_tokens,
    )


# ---------- notifications ----------
def redact_secrets(value):
    """Avoid leaking configured credentials through build diagnostics."""
    text = str(value)
    secrets = sorted({v.strip() for k, v in os.environ.items()
                      if v and any(mark in k.upper() for mark in ("KEY", "TOKEN", "SECRET"))},
                     key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)([?&](?:key|api_key|access_token)=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(/bot)[^/\s]+(/sendMessage)", r"\1[REDACTED]\2", text)
    return text


def notify(msg):
    tok, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    msg = redact_secrets(msg)
    print("NOTIFY:", msg)
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": msg[:3500]}, timeout=20)
        except Exception:
            pass


