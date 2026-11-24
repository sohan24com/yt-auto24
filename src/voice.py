"""Narration with emotion.
Engine 1: Gemini TTS - style is steered by a plain-English direction ("Say in a hushed, suspenseful tone: ...").
Engine 2 (fallback, free, always works): edge-tts with rate/pitch/volume changed per scene emotion.
All scenes of one video use ONE engine so the voice never changes mid-video."""
import asyncio, base64, re, subprocess, time, wave
from pathlib import Path
import requests
from .common import cfg, env

PAUSE = 0.35


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _clean(t):
    return re.sub(r"[*_#`]", "", t).strip()


# ---------------- emotion -> edge-tts prosody ----------------
def prosody(emotion, base_rate):
    """(rate, pitch, volume) for edge-tts from a free-text emotion like 'hushed, suspenseful'."""
    e = (emotion or "").lower()
    base = int(str(base_rate).replace("%", "").replace("+", "") or 0)
    rate, pitch, vol = base, 0, 0
    table = [(("whisper", "hush", "secret", "suspens", "myster", "eerie", "ominous"), (-10, -3, -8)),
             (("somber", "sad", "grief", "tragic", "mourn", "heavy", "solemn"), (-12, -5, -4)),
             (("awe", "wonder", "marvel", "reveal", "epic", "triumph"), (-4, +2, +2)),
             (("urgent", "tense", "panic", "danger", "frantic", "dread"), (+6, +1, +4)),
             (("excit", "thrill", "shock", "astonish"), (+4, +3, +4)),
             (("warm", "reflect", "gentle", "tender", "hope"), (-6, 0, -2))]
    for keys, (dr, dp, dv) in table:
        if any(k in e for k in keys):
            rate, pitch, vol = base + dr, dp, dv
            break
    return f"{rate:+d}%", f"{pitch:+d}Hz", f"{vol:+d}%"


def _edge(text, out, emotion):
    import edge_tts
    v = cfg()["video"]
    rate, pitch, vol = prosody(emotion, v["rate"])
    for i in range(4):
        try:
            asyncio.run(edge_tts.Communicate(_clean(text), v["voice"], rate=rate, pitch=pitch, volume=vol).save(str(out)))
            if Path(out).stat().st_size > 2000:
                return
        except Exception as e:
            print("edge-tts retry", i, e)
        time.sleep(5 * (i + 1))
    raise RuntimeError("edge-tts failed")


# ---------------- Gemini TTS ----------------
def _find_audio(obj):
    """Longest base64 string stored under a 'data' key anywhere in the JSON response."""
    best = ""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "data" and isinstance(v, str) and len(v) > len(best):
                best = v
            else:
                sub = _find_audio(v)
                if len(sub) > len(best):
                    best = sub
    elif isinstance(obj, list):
        for v in obj:
            sub = _find_audio(v)
            if len(sub) > len(best):
                best = sub
    return best


def _write_audio(b64, out):
    raw = base64.b64decode(b64)
    if raw[:4] == b"RIFF":
        Path(out).write_bytes(raw)
        return
    with wave.open(str(out), "wb") as wf:        # raw 16-bit mono PCM at 24 kHz (Gemini TTS default)
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)


def _gemini(text, out, emotion, keys):
    v = cfg()["video"]
    direction = emotion or "calm and cinematic"
    prompt = f"Say in a {direction} documentary narrator voice, with natural pauses and expressive emphasis:\n\n{_clean(text)}"
    for attempt in range(3):
        key = keys[attempt % len(keys)]
        try:   # 1) Interactions API (current docs)
            r = requests.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              json={"model": v["gemini_tts_model"], "input": prompt,
                                    "response_format": {"type": "audio"},
                                    "generation_config": {"speech_config": [{"voice": v["gemini_voice"]}]}},
                              timeout=180)
            if r.status_code == 200 and _find_audio(r.json()):
                _write_audio(_find_audio(r.json()), out)
                return True
            print("gemini tts (interactions):", r.status_code, r.text[:140])
        except Exception as e:
            print("gemini tts (interactions) error:", repr(e)[:140])
        try:   # 2) classic generateContent TTS
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{v['gemini_tts_fallback_model']}:generateContent"
            r = requests.post(url, params={"key": key}, timeout=180, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v["gemini_voice"]}}}}})
            if r.status_code == 200 and _find_audio(r.json()):
                _write_audio(_find_audio(r.json()), out)
                return True
            print("gemini tts (generateContent):", r.status_code, r.text[:140])
        except Exception as e:
            print("gemini tts (generateContent) error:", repr(e)[:140])
        time.sleep(6 * (attempt + 1))
    return False


def _to_wav(src, wav):
    subprocess.run(["ffmpeg", "-y", "-i", str(src), "-ar", "44100", "-ac", "2",
                    "-af", f"apad=pad_dur={PAUSE}", str(wav)], check=True, capture_output=True)


def build_voice(scenes, wd):
    """Returns (voice.wav, [scene durations incl. pause], engine name)."""
    wd = Path(wd)
    keys = [k for k in (env("GEMINI_API_KEY", False), env("GEMINI_API_KEY_2", False), env("GEMINI_API_KEY_3", False)) if k]
    engines = (["gemini"] if cfg()["video"].get("gemini_tts") and keys else []) + ["edge"]
    for engine in engines:
        try:
            wavs, durs = [], []
            for i, s in enumerate(scenes):
                src, wav = wd / f"v{i:02d}.src", wd / f"v{i:02d}.wav"
                if engine == "gemini":
                    if not _gemini(s["narration"], src, s.get("emotion"), keys):
                        raise RuntimeError("gemini tts failed")
                else:
                    _edge(s["narration"], src, s.get("emotion"))
                _to_wav(src, wav)
                wavs.append(wav)
                durs.append(probe(wav))
            lst = wd / "voices.txt"
            lst.write_text("".join(f"file '{w.name}'\n" for w in wavs))
            out = wd / "voice.wav"
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                           check=True, capture_output=True)
            print(f"voice engine: {engine}")
            return out, durs, engine
        except Exception as e:
            print(f"voice engine '{engine}' failed ({e}); trying next")
    raise RuntimeError("no voice engine worked")
