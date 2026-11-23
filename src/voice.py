import asyncio
import hashlib
import re
import subprocess
import time
from pathlib import Path
import edge_tts
from .common import cfg

PAUSE = 0.35
MAX_CHUNK_CHARS = 750


def probe(path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _clean(t):
    txt = re.sub(r"[*_#`]+", " ", str(t or ""))
    # Expand common documentary abbreviations/symbols for natural TTS delivery
    txt = re.sub(r"\bvs\.?\b", "versus", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\be\.g\.?\b", "for example", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bi\.e\.?\b", "that is", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s*[—–]\s*", ", ", txt)
    txt = re.sub(r"\s{2,}", " ", txt)
    return txt.strip()


def _split_chunks(text, max_chars=MAX_CHUNK_CHARS):
    """Split long scene narration along sentence/clause boundaries so edge-tts stays reliable."""
    clean = _clean(text)
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks = []
    cur = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > max_chars:
            # Fallback clause split for extra-long sentences
            clauses = re.split(r"(?<=[,;:])\s+", sent)
            for cl in clauses:
                cl = cl.strip()
                if not cl:
                    continue
                if len(f"{cur} {cl}".strip()) <= max_chars:
                    cur = f"{cur} {cl}".strip()
                else:
                    if cur:
                        chunks.append(cur)
                    cur = cl[:max_chars].strip()
            continue

        if len(f"{cur} {sent}".strip()) <= max_chars:
            cur = f"{cur} {sent}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = sent

    if cur:
        chunks.append(cur)
    return chunks or [clean]


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
    return asyncio.run(coro)


def _format_rate(raw_rate):
    if raw_rate is None:
        return "+0%"
    s = str(raw_rate).strip()
    if not s:
        return "+0%"
    if s.endswith("%"):
        return s if s.startswith(("+", "-")) else f"+{s}"
    try:
        val = float(s)
        pct = int(round((val - 1.0) * 100)) if 0.3 <= val <= 2.5 else int(round(val))
        return f"+{pct}%" if pct >= 0 else f"{pct}%"
    except ValueError:
        return "+0%"


def _synth_single_mp3(text, out_mp3, voice, rate):
    out_path = Path(out_mp3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")

    for i in range(5):
        try:
            tmp_path.unlink(missing_ok=True)
            _run_async(edge_tts.Communicate(text, voice, rate=rate).save(str(tmp_path)))
            if tmp_path.exists() and tmp_path.stat().st_size > 2000 and probe(tmp_path) > 0.2:
                tmp_path.replace(out_path)
                return
        except Exception as e:
            print("tts retry", i, e)
        tmp_path.unlink(missing_ok=True)
        time.sleep(min(25, 3 * (2 ** i)))
    raise RuntimeError("edge-tts failed")


def synth(text, out, rate_override=None):
    v = cfg()["video"]
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = _split_chunks(text)
    if not chunks:
        raise ValueError("empty narration text for TTS")

    voice = v.get("voice", "en-US-ChristopherNeural")
    rate = _format_rate(rate_override if rate_override is not None else v.get("rate", "+0%"))

    if len(chunks) == 1:
        _synth_single_mp3(chunks[0], out_path, voice, rate)
        return

    part_dir = out_path.parent / f"{out_path.stem}_parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_files = []
    for idx, ch in enumerate(chunks):
        p_mp3 = part_dir / f"part_{idx:02d}.mp3"
        _synth_single_mp3(ch, p_mp3, voice, rate)
        part_files.append(p_mp3)

    concat_list = part_dir / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in part_files),
        encoding="utf-8",
    )
    tmp_merged = out_path.with_suffix(out_path.suffix + ".merged.part")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame",
            "-q:a", "2",
            str(tmp_merged),
        ],
        check=True,
        capture_output=True,
    )
    if not tmp_merged.exists() or tmp_merged.stat().st_size < 2000 or probe(tmp_merged) <= 0.2:
        tmp_merged.unlink(missing_ok=True)
        raise RuntimeError("failed to merge TTS chunks")
    tmp_merged.replace(out_path)


def _scene_fingerprint(text, voice, rate, pause_dur):
    payload = f"{voice}|{rate}|{pause_dur:.3f}|{_clean(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_voice(scenes, wd):
    """Returns (voice.wav path, [scene durations incl. pause])."""
    wd = Path(wd)
    wd.mkdir(parents=True, exist_ok=True)
    v = cfg()["video"]
    base_rate = _format_rate(v.get("rate", "+0%"))
    voice = v.get("voice", "en-US-ChristopherNeural")
    pause_dur = float(v.get("scene_pause", PAUSE))

    wavs, durs = [], []
    for i, s in enumerate(scenes or []):
        narration = (s or {}).get("narration", "")
        mp3 = wd / f"v{i:02d}.mp3"
        wav = wd / f"v{i:02d}.wav"
        fp_file = wd / f"v{i:02d}.fp"

        # Slightly tighter pacing on Hook (scene 0) for stronger opening momentum
        scene_rate = base_rate
        if i == 0 and base_rate == "+0%":
            scene_rate = "+4%"

        fp = _scene_fingerprint(narration, voice, scene_rate, pause_dur)
        reusable = (
            fp_file.exists()
            and fp_file.read_text(encoding="utf-8").strip() == fp
            and wav.exists()
            and wav.stat().st_size > 2000
        )

        if reusable:
            try:
                dur = probe(wav)
                if dur > 0.2:
                    wavs.append(wav)
                    durs.append(dur)
                    continue
            except Exception:
                pass

        synth(narration, mp3, rate_override=scene_rate)
        tmp_wav = wav.with_suffix(".part.wav")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(mp3),
                "-ar", "44100",
                "-ac", "2",
                "-af", f"aresample=44100,apad=pad_dur={pause_dur}",
                str(tmp_wav),
            ],
            check=True,
            capture_output=True,
        )
        tmp_wav.replace(wav)
        fp_file.write_text(fp, encoding="utf-8")
        wavs.append(wav)
        durs.append(probe(wav))

    if not wavs:
        raise RuntimeError("no scenes to synthesize voice")

    lst = wd / "voices.txt"
    lst.write_text("".join(f"file '{w.name}'\n" for w in wavs), encoding="utf-8")
    out = wd / "voice.wav"
    tmp_out = out.with_suffix(".part.wav")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(lst),
            "-c", "copy",
            str(tmp_out),
        ],
        check=True,
        capture_output=True,
    )
    tmp_out.replace(out)
    return out, durs
