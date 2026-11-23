import json
import subprocess
import tempfile
from pathlib import Path


def _probe_duration(video_path):
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(r.stdout or "{}")
    return float((data.get("format") or {}).get("duration") or 0.0)


def _pick_highlights(total_dur, target_secs):
    """Pick 3 high-retention segments across the full documentary: hook, mid-twist, and late reveal tease."""
    total = max(6.0, float(total_dur))
    target = max(4.0, min(float(target_secs), total))

    # Allocate weights: ~35% opening hook, ~35% mid-video climax/twist, ~30% late reveal tease
    s1_len = max(1.8, round(target * 0.35, 2))
    s2_len = max(1.8, round(target * 0.35, 2))
    s3_len = max(1.5, round(target - s1_len - s2_len, 2))

    s1_start = 0.0
    s2_start = max(s1_len + 0.5, min(total * 0.42, total - s2_len - s3_len - 1.0))
    s3_start = max(s2_start + s2_len + 0.5, min(total * 0.76, total - s3_len - 0.5))

    segments = [
        (s1_start, min(s1_len, max(1.0, total - s1_start))),
        (s2_start, min(s2_len, max(1.0, total - s2_start))),
        (s3_start, min(s3_len, max(1.0, total - s3_start))),
    ]
    return [(st, ln) for st, ln in segments if ln >= 0.8]


def _render_segment(src_path, start, dur, out_seg, idx):
    # Subtle dynamic motion + blurred 9:16 background + sharp centered 16:9 foreground
    zoom_expr = "min(zoom+0.0008,1.08)" if idx % 2 == 0 else "if(eq(on,1),1.06,max(zoom-0.0007,1.0))"
    fc = (
        f"[0:v]trim=start={start:.2f}:duration={dur:.2f},setpts=PTS-STARTPTS,split=2[a][b];"
        f"[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=28:5[bg];"
        f"[b]scale=1280:720:force_original_aspect_ratio=decrease,"
        f"zoompan=z='{zoom_expr}':d={max(1, int(dur * 30))}:s=1280x720:fps=30,"
        f"scale=1080:-2:flags=lanczos[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v];"
        f"[0:a]atrim=start={start:.2f}:duration={dur:.2f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d=0.12,afade=t=out:st={max(0.0, dur - 0.15):.2f}:d=0.15[aout]"
    )
    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{max(0.0, start - 1.5):.2f}" if start > 2.0 else "0.0",
            "-i", str(src_path),
            "-filter_complex", fc,
            "-map", "[v]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "160k",
            "-avoid_negative_ts", "make_zero",
            str(out_seg),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])


def make_short(final, secs, out):
    """9:16 highlight Short combining hook, mid-video twist, and late tease from the full video."""
    src_path = Path(final)
    if not src_path.exists():
        raise FileNotFoundError(f"source video not found: {src_path}")

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".part.mp4")
    tmp_path.unlink(missing_ok=True)

    total_dur = _probe_duration(src_path)
    segments = _pick_highlights(total_dur, secs)
    if not segments:
        raise RuntimeError("could not determine highlight segments for short")

    with tempfile.TemporaryDirectory(prefix="short_segs_") as seg_wd:
        seg_dir = Path(seg_wd)
        seg_files = []
        for idx, (st, ln) in enumerate(segments):
            seg_file = seg_dir / f"seg_{idx:02d}.mp4"
            _render_segment(src_path, st, ln, seg_file, idx)
            if seg_file.exists() and seg_file.stat().st_size > 0:
                seg_files.append(seg_file)

        if not seg_files:
            raise RuntimeError("no short segments rendered")

        concat_txt = seg_dir / "concat.txt"
        concat_txt.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in seg_files),
            encoding="utf-8",
        )

        final_dur = sum(ln for _, ln in segments)
        fade_dur = min(1.0, max(0.25, final_dur * 0.08))
        fade_start = max(0.0, final_dur - fade_dur - 0.1)

        r = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_txt),
                "-c:v", "copy",
                "-af", f"afade=t=out:st={fade_start:.2f}:d={fade_dur:.2f}",
                "-c:a", "aac",
                "-b:a", "160k",
                "-movflags", "+faststart",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(r.stderr[-800:])

    if not tmp_path.exists() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("short output video is empty")

    tmp_path.replace(out_path)
