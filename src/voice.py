import asyncio, re, subprocess, time
from pathlib import Path
import edge_tts
from .common import cfg

PAUSE = 0.35


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _clean(t):
    return re.sub(r"[*_#`]", "", t).strip()


def synth(text, out):
    v = cfg()["video"]
    for i in range(4):
        try:
            asyncio.run(edge_tts.Communicate(_clean(text), v["voice"], rate=v["rate"]).save(str(out)))
            if Path(out).stat().st_size > 2000:
                return
        except Exception as e:
            print("tts retry", i, e)
        time.sleep(5 * (i + 1))
    raise RuntimeError("edge-tts failed")


def build_voice(scenes, wd):
    """Returns (voice.wav path, [scene durations incl. pause])."""
    wd = Path(wd)
    wavs, durs = [], []
    for i, s in enumerate(scenes):
        mp3, wav = wd / f"v{i:02d}.mp3", wd / f"v{i:02d}.wav"
        synth(s["narration"], mp3)
        subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "44100", "-ac", "2",
                        "-af", f"apad=pad_dur={PAUSE}", str(wav)], check=True, capture_output=True)
        wavs.append(wav)
        durs.append(probe(wav))
    lst = wd / "voices.txt"
    lst.write_text("".join(f"file '{w.name}'\n" for w in wavs))
    out = wd / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                   check=True, capture_output=True)
    return out, durs
