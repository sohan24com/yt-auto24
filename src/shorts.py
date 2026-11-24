import subprocess
from pathlib import Path


def make_short(final, secs, out):
    """9:16 Short from the opening hook: sharp 16:9 centre over a blurred, zoomed copy."""
    fc = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          "boxblur=30:5[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    r = subprocess.run(["ffmpeg", "-y", "-t", str(secs), "-i", str(final), "-filter_complex", fc,
                        "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-af", "afade=t=out:st=%d:d=1.5" % (secs - 2),
                        "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])
