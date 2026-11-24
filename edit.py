import random, re, subprocess
from pathlib import Path

FPS = 30
GRADE = "eq=contrast=1.08:saturation=1.12:brightness=-0.02,vignette=PI/5"
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr[-1500:])


def plan_shots(d):
    """Split a scene into 3.5-6 s shots (pattern interrupts)."""
    out, rem = [], d
    while rem > 0.05:
        l = random.uniform(3.5, 6.0)
        if rem - l < 2.5:
            l = rem
        out.append(round(l, 3))
        rem -= l
    return out


def render_shot(asset, d, out, n):
    p = str(asset["path"])
    if asset["kind"] == "video":
        vf = f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps={FPS},{GRADE}"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])
    else:
        frames = int(d * FPS) + 2
        step = 0.35 / frames
        if n % 2:
            z = f"min(zoom+{step:.6f},1.35)"
        else:
            z = f"if(eq(on,1),1.35,max(zoom-{step:.6f},1.0))"
        vf = (f"scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={FPS},{GRADE}")
        run(["ffmpeg", "-y", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])


def ass_time(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(scenes, durs, path):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,DejaVu Sans,62,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,120,120,90,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines, t0 = [], 0.0
    for s, d in zip(scenes, durs):
        words = re.sub(r"\s+", " ", s["narration"]).strip().split(" ")
        chunks, cur = [], []
        for w in words:
            cur.append(w)
            if len(cur) >= 5 or w.endswith((".", "?", "!", ",", ";", ":")) and len(cur) >= 3:
                chunks.append(" ".join(cur))
                cur = []
        if cur:
            chunks.append(" ".join(cur))
        speech = d - 0.35
        total = sum(len(c) for c in chunks) or 1
        t = t0
        for c in chunks:
            dur = speech * len(c) / total
            txt = c.replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + dur)},Main,,0,0,0,,{txt}")
            t += dur
        t0 += d
    Path(path).write_text(head + "\n".join(lines) + "\n")


def make_music(total, out, wd):
    """Use a track from assets/music if present, otherwise synthesize a licence-free ambient bed."""
    tracks = list((Path(__file__).resolve().parent.parent / "assets" / "music").glob("*.mp3"))
    if tracks:
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(random.choice(tracks)), "-t", f"{total + 5}",
             "-ar", "44100", "-ac", "2", str(out)])
        return
    expr = ("0.10*sin(2*PI*55*t)+0.07*sin(2*PI*82.4*t)+0.05*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*0.07*t))"
            "+0.04*sin(2*PI*164.8*t)*(0.5+0.5*sin(2*PI*0.05*t))+0.025*sin(2*PI*220*t)*(0.5+0.5*sin(2*PI*0.031*t))")
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={total + 5}",
         "-af", "lowpass=f=900,aecho=0.8:0.7:500|900:0.3|0.2,aformat=channel_layouts=stereo", str(out)])


def make_whoosh(out):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anoisesrc=d=0.9:c=pink:r=44100:a=0.5",
         "-af", "highpass=f=300,lowpass=f=4500,afade=t=in:d=0.4,afade=t=out:st=0.4:d=0.5,aformat=channel_layouts=stereo",
         str(out)])


def mix_audio(voice, music, whoosh, starts, total, out):
    n = len(starts)
    f = [f"[1:a]volume=0.55,afade=t=in:d=3,afade=t=out:st={max(total - 4, 1):.2f}:d=4[m]",
         "[0:a]asplit=2[v1][v2]",
         "[m][v1]sidechaincompress=threshold=0.02:ratio=10:attack=30:release=600[duck]"]
    labels = ["[duck]", "[v2]"]
    if n:
        f.append(f"[2:a]asplit={n}" + "".join(f"[w{i}]" for i in range(n)))
        for i, st in enumerate(starts):
            ms = max(int(st * 1000) - 250, 0)
            f.append(f"[w{i}]adelay={ms}|{ms},volume=0.35[s{i}]")
            labels.append(f"[s{i}]")
    f.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
             f"loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    cmd = ["ffmpeg", "-y", "-i", str(voice), "-i", str(music)]
    if n:
        cmd += ["-i", str(whoosh)]
    cmd += ["-filter_complex", ";".join(f), "-map", "[a]", "-t", f"{total:.2f}", "-ar", "44100", str(out)]
    run(cmd)


def assemble(scenes, durs, plans, assets_by_scene, voice, wd, out):
    """Render every shot, concat, burn subtitles, mix sound design, mux."""
    wd = Path(wd)
    shots, n = [], 0
    for plan, assets in zip(plans, assets_by_scene):
        for l, a in zip(plan, assets):
            o = wd / f"shot{n:04d}.mp4"
            render_shot(a, l, o, n)
            shots.append(o)
            n += 1
    (wd / "shots.txt").write_text("".join(f"file '{s.name}'\n" for s in shots))
    total = sum(durs)
    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d
    make_ass(scenes, durs, wd / "subs.ass")
    make_music(total, wd / "music.wav", wd)
    make_whoosh(wd / "whoosh.wav")
    mix_audio(voice, wd / "music.wav", wd / "whoosh.wav", starts[1:], total, wd / "mix.wav")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "shots.txt", "-i", "mix.wav",
         "-vf", "subtitles=subs.ass,fade=t=in:st=0:d=0.8", "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)], cwd=wd)
    return starts

