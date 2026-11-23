import csv
import json
import os
import re
import time
import datetime as dt
from pathlib import Path
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
MEM = ROOT / "memory"
(MEM / "videos").mkdir(parents=True, exist_ok=True)

_KEY_CURSOR = 0


def cfg():
    cfg_path = ROOT / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def now():
    return dt.datetime.now(dt.timezone.utc)


def _atomic_write_text(path, content):
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(out_path)


# ---------- memory (one json file per video => no merge conflicts) ----------
def load_videos():
    items = []
    for p in sorted((MEM / "videos").glob("*.json")):
        if p.name.endswith(".part"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                items.append(data)
        except Exception as ex:
            print("skip corrupt video json:", p.name, ex)
    return items


def save_video(rec):
    if not isinstance(rec, dict) or not rec.get("video_id"):
        raise ValueError("save_video requires a dict with 'video_id'")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(rec["video_id"]))
    _atomic_write_text(MEM / "videos" / f"{safe_id}.json", json.dumps(rec, indent=2, ensure_ascii=False))


def load_insights():
    p = MEM / "insights.json"
    if not p.exists():
        return {"weights": None, "lessons": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"weights": None, "lessons": []}
        data.setdefault("weights", None)
        data.setdefault("lessons", [])
        return data
    except Exception as ex:
        print("load_insights fallback:", ex)
        return {"weights": None, "lessons": []}


def save_insights(d):
    payload = d if isinstance(d, dict) else {"weights": None, "lessons": []}
    _atomic_write_text(MEM / "insights.json", json.dumps(payload, indent=2, ensure_ascii=False))


# ---------- gemini keys & rotation ----------
def _parse_key_tokens(raw):
    found = []
    for part in re.split(r"[,\n\r;\s]+", str(raw or "")):
        tok = part.strip().strip('"').strip("'")
        if tok.startswith("AIza") and len(tok) > 20:
            found.append(tok)
    return found


def _load_gemini_keys():
    keys = []
    seen = set()

    def _add(k):
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    for var_name in (
        "GEMINI_API_KEYS",
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
    ):
        for k in _parse_key_tokens(os.environ.get(var_name, "")):
            _add(k)

    candidates = [
        ROOT / "keys.csv",
        ROOT / "gemini_keys.csv",
        ROOT / "gemini_keys.txt",
        ROOT / "keys.txt",
        ROOT / "api_keys.json",
    ]
    for p in candidates:
        if not p.exists() or not p.is_file():
            continue
        try:
            if p.suffix.lower() == ".csv":
                with open(p, "r", encoding="utf-8", newline="") as f:
                    for row in csv.reader(f):
                        for cell in row:
                            for k in _parse_key_tokens(cell):
                                _add(k)
            elif p.suffix.lower() == ".json":
                raw_json = json.loads(p.read_text(encoding="utf-8"))
                for k in _parse_key_tokens(json.dumps(raw_json)):
                    _add(k)
            else:
                for k in _parse_key_tokens(p.read_text(encoding="utf-8")):
                    _add(k)
        except Exception as ex:
            print("skip key file:", p.name, ex)

    if not keys:
        raise RuntimeError("No Gemini API keys found in GEMINI_API_KEYS / GEMINI_API_KEY or key files")
    return keys


def _ordered_keys():
    global _KEY_CURSOR
    pool = _load_gemini_keys()
    start = _KEY_CURSOR % len(pool)
    _KEY_CURSOR = (_KEY_CURSOR + 1) % len(pool)
    return pool[start:] + pool[:start]


# ---------- gemini ----------
def extract_json(text):
    cleaned = re.sub(r"```(?:json)?|```", "", str(text or ""), flags=re.I).strip()
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if not starts:
        raise ValueError("no json found")
    s = min(starts)
    ends = [i for i in (cleaned.rfind("}"), cleaned.rfind("]")) if i != -1]
    if not ends:
        raise ValueError("no json closing bracket found")
    e = max(ends)
    return json.loads(cleaned[s:e + 1])


def gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9, tries=5):
    model = cfg().get("gemini_model") or "gemini-2.5-flash"
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

    key_pool = _ordered_keys()
    last = ""
    total_rounds = max(1, int(tries))

    for round_idx in range(total_rounds):
        for k_idx, api_key in enumerate(key_pool):
            try:
                r = requests.post(url, params={"key": api_key}, json=body, timeout=300)
                if r.status_code == 200:
                    payload = r.json()
                    candidates = payload.get("candidates") or []
                    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
                    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                    if not text.strip():
                        raise ValueError("empty response text from Gemini")
                    return extract_json(text) if json_out else text
                last = f"{r.status_code} {r.text[:200]}"
                if r.status_code in (400, 401, 403):
                    continue
            except Exception as ex:  # parse errors, timeouts, 429/5xx
                last = repr(ex)

            if k_idx < len(key_pool) - 1:
                time.sleep(2)

        if round_idx < total_rounds - 1:
            time.sleep(min(60, 12 * (round_idx + 1)))

    raise RuntimeError(f"Gemini failed: {last}")


# ---------- notifications ----------
def notify(msg):
    tok, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    text = str(msg or "")
    print("NOTIFY:", text)
    if tok and chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                data={"chat_id": chat, "text": text[:3500]},
                timeout=20,
            )
        except Exception:
            pass
