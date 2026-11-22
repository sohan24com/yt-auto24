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


def gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9, tries=5):
    model = cfg()["gemini_model"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 32000},
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
    last = ""
    for i in range(tries):
        try:
            r = requests.post(url, params={"key": keys[i % len(keys)]}, json=body, timeout=300)
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
                return extract_json(text) if json_out else text
            last = f"{r.status_code} {r.text[:200]}"
        except Exception as ex:  # parse errors, timeouts
            last = repr(ex)
        time.sleep(12 * (i + 1))
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
