import json, os, re, time, datetime as dt
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
def load_videos():
    return [json.loads(p.read_text()) for p in sorted((MEM / "videos").glob("*.json"))]


def save_video(rec):
    (MEM / "videos" / f"{rec['video_id']}.json").write_text(json.dumps(rec, indent=1))


def load_insights():
    p = MEM / "insights.json"
    return json.loads(p.read_text()) if p.exists() else {"weights": None, "lessons": []}


def save_insights(d):
    (MEM / "insights.json").write_text(json.dumps(d, indent=1))


# ---------- gemini ----------
def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no json found")
    s = min(starts)
    e = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[s:e + 1])


# Keep this allowlist limited to models whose standard text API has a documented free tier.
# Model discovery only decides which supported allowlisted IDs to try; it never enables paid IDs.
FREE_GEMINI_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
)

_MODELS = None


def _model_list(key):
    """Return only known free-tier text models, preferring the configured free model."""
    global _MODELS
    if _MODELS:
        return _MODELS
    c = cfg()
    available = set()
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 1000}, timeout=30)
        r.raise_for_status()
        for model in r.json().get("models", []):
            name = model["name"].split("/")[-1]
            if "generateContent" in model.get("supportedGenerationMethods", []):
                available.add(name)
    except Exception as e:
        print("model discovery failed:", type(e).__name__)

    configured = [c.get("gemini_model", "")] + list(c.get("gemini_fallbacks", []))
    order = [name for name in configured if name in FREE_GEMINI_MODELS]
    order.extend(name for name in FREE_GEMINI_MODELS
                 if not available or name in available)
    _MODELS = list(dict.fromkeys(order))
    print("Free Gemini text models to try:", _MODELS)
    return _MODELS
class GeminiUnavailableError(RuntimeError):
    """Raised after bounded attempts across the configured Gemini keys and models."""


def gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9,
           tries=None, request_timeout=90, total_timeout=300):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 32000},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if grounding:
        body["tools"] = [{"google_search": {}}]
    elif json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"

    keys = list(dict.fromkeys(k.strip() for k in (
        env("GEMINI_API_KEY", False), env("GEMINI_API_KEY_2", False),
        env("GEMINI_API_KEY_3", False), env("GEMINI_API_KEY_4", False),
        env("GEMINI_API_KEY_5", False),
    ) if k and k.strip()))
    if not keys:
        raise GeminiUnavailableError("No Gemini API keys are configured")

    models = _model_list(keys[0])
    if not models:
        raise GeminiUnavailableError("No Gemini models are available to try")

    deadline = time.monotonic() + max(1.0, float(total_timeout))
    attempts = 0
    last = "no usable response"
    status_counts = {}

    for model in models:
        overloaded = False
        for key in keys:
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
                    return extract_json(text) if json_out else text

                status = int(r.status_code)
                status_counts[status] = status_counts.get(status, 0) + 1
                last = f"{model}: HTTP {status}"
                print(f"Gemini candidate unavailable: {model} HTTP {status}")

                if status == 503 or status in (500, 502, 504):
                    # Capacity errors are model-wide; move to another model instead of
                    # asking every account to hit the same overloaded endpoint.
                    overloaded = True
                    break
                if status in (400, 404):
                    # Invalid request/model: another key will not repair this candidate.
                    break
                if status in (401, 403, 429):
                    # These may be key/project-specific, so rotate to the next distinct key.
                    continue
                if status >= 400:
                    break
            except requests.Timeout:
                last = f"{model}: request timed out"
                continue
            except Exception as ex:
                last = f"{model}: {type(ex).__name__}"
                continue

        if tries is not None and attempts >= tries:
            break
        if deadline - time.monotonic() <= 0:
            break
        if overloaded:
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))

    summary = ", ".join(f"HTTP {code} x{count}" for code, count in sorted(status_counts.items()))
    if not summary:
        summary = f"{attempts} request attempt(s)"
    raise GeminiUnavailableError(
        f"Gemini unavailable after {attempts} bounded attempt(s) ({summary}); last response: {last}"
    )


# ---------- notifications ----------
def notify(msg):
    tok, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    print("NOTIFY:", msg)
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": msg[:3500]}, timeout=20)
        except Exception:
            pass
