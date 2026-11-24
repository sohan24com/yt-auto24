"""Pre-upload media, subtitle, audio and thumbnail validation using FFmpeg/FFprobe."""
import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from PIL import Image


def _seconds(value):
    try:
        h, m, s = str(value).strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, TypeError):
        raise ValueError("invalid ASS timestamp")


def validate_ass(path, duration, min_events=8):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 100:
        return ["subtitle file missing or empty"]
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 3)
        if len(parts) < 3:
            return ["malformed subtitle event"]
        try:
            start, end = _seconds(parts[1]), _seconds(parts[2])
        except ValueError:
            return ["invalid subtitle timestamp"]
        if start < 0 or end <= start or end > duration + 1.0:
            return ["subtitle event outside video duration"]
        events.append((start, end))
    if len(events) < min_events:
        return ["subtitle events missing"]
    return []


def validate_thumbnail(path, expected_size=(1280, 720), min_bytes=10_000):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < min_bytes:
        return ["thumbnail missing or undersized"]
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.size != expected_size:
                return [f"wrong thumbnail dimensions: {image.width}x{image.height}"]
    except Exception:
        return ["thumbnail is not a readable image"]
    return []


def validate_video(path, expected_duration=None, portrait=False, min_bytes=1 << 20,
                   expected_fps=30, deep=True):
    """Validate container/streams, timing, FPS and (optionally) decode/audio/black/freeze health."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < min_bytes:
        return ["missing or undersized output file"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,duration,avg_frame_rate,sample_rate,channels",
         "-of", "json", str(path)], capture_output=True, text=True)
    if probe.returncode:
        return ["ffprobe could not read output"]
    try:
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video")
        audio = next(s for s in streams if s.get("codec_type") == "audio")
        duration = float(info["format"]["duration"])
        fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
        width, height = int(video["width"]), int(video["height"])
    except (ValueError, KeyError, StopIteration, TypeError, ZeroDivisionError):
        return ["output is missing readable audio/video metadata"]
    issues = []
    size = (1080, 1920) if portrait else (1920, 1080)
    if (width, height) != size:
        issues.append(f"wrong resolution: {width}x{height}")
    if abs(fps - expected_fps) > 0.05:
        issues.append(f"wrong frame rate: {fps:g} fps")
    if expected_duration is not None and abs(duration - expected_duration) > 1.5:
        issues.append(f"output duration {duration:.2f}s differs from expected {expected_duration:.2f}s")
    try:
        vd = float(video.get("duration", duration))
        ad = float(audio.get("duration", duration))
    except (TypeError, ValueError):
        vd = ad = duration
    if abs(vd - ad) > 1.0:
        issues.append(f"audio/video duration mismatch: {ad:.2f}s vs {vd:.2f}s")
    if not audio.get("sample_rate") or not audio.get("channels"):
        issues.append("audio stream has no sample rate or channels")
    if not deep:
        return issues

    # One decode pass runs full-file decoding, black/freeze detection and audio peak analysis.
    scan = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
         "-vf", "blackdetect=d=3:pix_th=0.08,freezedetect=n=-60dB:d=8",
         "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
        capture_output=True, text=True, timeout=900)
    if scan.returncode:
        issues.append("full-file FFmpeg decode failed")
        return issues
    log = scan.stderr
    for match in re.finditer(r"black_start:([0-9.]+) black_end:([0-9.]+) black_duration:([0-9.]+)", log):
        start, end, black_duration = map(float, match.groups())
        if black_duration >= 3.0 and start > 0.5 and end < duration - 0.5:
            issues.append(f"long black section at {start:.1f}s ({black_duration:.1f}s)")
    for match in re.finditer(r"freeze_start:([0-9.]+).*?freeze_duration:([0-9.]+)", log):
        start, freeze_duration = map(float, match.groups())
        if freeze_duration >= 8.0:
            issues.append(f"frozen section at {start:.1f}s ({freeze_duration:.1f}s)")
    peaks = re.findall(r"Peak level dB:\\s*(-?[0-9.]+|inf)", log, flags=re.I)
    if any(value.lower() != "inf" and float(value) >= -0.05 for value in peaks):
        issues.append("audio peak is at or above -0.05 dBFS (clipping risk)")
    if not peaks:
        issues.append("audio peak analysis did not produce a result")
    return issues
