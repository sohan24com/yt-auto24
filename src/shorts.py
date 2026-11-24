import re
import subprocess


def _moment_score(scene, index, last_index):
    text = str(scene.get("narration", "")).lower()
    emotion = str(scene.get("emotion", "")).lower()
    if index == last_index or len(text.split()) < 35:
        return -1
    score = 0
    for marker in ("?", "but ", "yet ", "until ", "instead ", "vanished", "discovered", "survived",
                   "mystery", "secret", "impossible", "hidden", "why ", "how "):
        score += text.count(marker)
    score += 2 * any(k in emotion for k in ("suspense", "urgent", "wonder", "tense", "mystery"))
    score += min(len(re.findall(r"\b\d{2,}\b", text)), 3)
    if any(k in text for k in ("the answer was", "we finally know", "the mystery was solved")):
        score -= 8
    return score


def choose_moment(scenes, starts, durs, max_seconds):
    """Pick a high-curiosity non-final scene from anywhere in the documentary."""
    if not scenes or len(scenes) != len(starts) or len(scenes) != len(durs):
        raise ValueError("scene timing does not match the documentary")
    last_start = starts[-1]
    candidates = [( _moment_score(scene, i, len(scenes) - 1), i)
                  for i, scene in enumerate(scenes[:-1])]
    score, index = max(candidates, default=(-1, 0))
    if score < 0:
        index = max(0, len(scenes) // 2 - 1)
    start = float(starts[index])
    scene_end = float(starts[index] + durs[index])
    # Center on the selected scene and keep the ending out of the clip.
    clip_start = max(start, start + (float(durs[index]) - max_seconds) / 2)
    clip_end = min(clip_start + max_seconds, last_start)
    if clip_end - clip_start < 15:
        clip_start = max(0.0, min(start, last_start - max_seconds))
        clip_end = min(clip_start + max_seconds, last_start)
    duration = max(1.0, clip_end - clip_start)
    return clip_start, duration, index


def make_short(final, start, secs, out):
    """Create a 9:16 excerpt with a clear full-documentary end card."""
    start, secs = max(0.0, float(start)), max(1.0, float(secs))
    fc = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          "boxblur=30:5[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
          f"drawtext=font='DejaVu Sans':text='WATCH THE FULL DOCUMENTARY':fontsize=42:fontcolor=white:"
          f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-210:enable='gte(t,{max(0,secs-5):.2f})'[v]")
    r = subprocess.run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{secs:.3f}", "-i", str(final),
                        "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                        "-af", f"afade=t=out:st={max(secs-2,0):.3f}:d=1.5",
                        "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-1200:])
