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


_MODELS = None


def _model_list(key):
    """Configured model first, then every Flash model the API currently offers (newest first)."""
    global _MODELS
    if _MODELS:
        return _MODELS
    c = cfg()
    found = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 1000}, timeout=30)
        for m in r.json().get("models", []):
            name = m["name"].split("/")[-1]
            mm = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?(-preview)?", name)
            if mm and "generateContent" in m.get("supportedGenerationMethods", []):
                found.append((-float(mm.group(1)), bool(mm.group(2)), bool(mm.group(3)), name))
        found.sort()
    except Exception as e:
        print("model discovery failed:", e)
    order = [c["gemini_model"]] + [f[3] for f in found] + list(c.get("gemini_fallbacks", [])) + ["gemini-flash-latest"]
    _MODELS = list(dict.fromkeys(order))
    print("Gemini models to try:", _MODELS[:6])
    return _MODELS


def gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9, tries=16):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 32000},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if grounding:
        body["tools"] = [{"google_search": {}}]   # cannot be combined with JSON mime type
    elif json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"
    keys = [k for k in (env("GEMINI_API_KEY", False), env("GEMINI_API_KEY_2", False),
                        env("GEMINI_API_KEY_3", False)) if k]
    if not keys:
        raise RuntimeError("No GEMINI_API_KEY set")
    models = _model_list(keys[0])
    last, mi = "", 0
    for n in range(tries):
        model = models[mi % len(models)]
        gc = dict(body["generationConfig"])
        if not model.startswith("gemini-3"):      # Gemini 3 works best at its default temperature
            gc["temperature"] = temperature
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, params={"key": keys[n % len(keys)]}, json=dict(body, generationConfig=gc), timeout=300)
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                return extract_json(text) if json_out else text
            last = f"{model}: {r.status_code} {r.text[:200]}"
            mi += 1                                  # any API error -> try the next model (and next key)
            print("Gemini error, switching:", last[:120])
            if r.status_code in (404, 400):
                continue                             # model gone/incompatible: no waiting needed
        except Exception as ex:  # parse errors, timeouts: retry same model
            last = repr(ex)
        time.sleep(min(6 * (n + 1), 45))
    raise RuntimeError(f"Gemini failed: {last}")


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
