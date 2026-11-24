import random, re, subprocess
from pathlib import Path

FPS = 30
GRADE = "eq=contrast=1.1:saturation=1.1:brightness=-0.03:gamma_r=1.04:gamma_b=0.96,vignette=PI/5"   # warm highlights / cool shadows, cheap to render
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr[-1500:])


def plan_shots(d, fast=0.0):
    """Split a scene into shots. `fast` seconds at the start are cut quickly (1.4-2.4 s) = cold-open energy;
    the rest are 3.5-6 s pattern interrupts."""
    out, rem = [], d
    while rem > 0.05 and fast > 0.05:
        l = min(random.uniform(1.4, 2.4), fast, rem)
        if rem - l < 1.0:
            l = rem
        out.append(round(l, 3))
        rem -= l
        fast -= l
    while rem > 0.05:
        l = random.uniform(3.5, 6.0)
        if rem - l < 2.5:
            l = rem
        out.append(round(l, 3))
        rem -= l
    return out


def render_shot(asset, d, out, n, punch=False):
    p = str(asset["path"])
    if asset["kind"] == "video":
        if punch:   # fast push-in used in the opening seconds
            vf = (f"fps={FPS},scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
                  f"zoompan=z='min(zoom+0.004,1.28)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={FPS},{GRADE}")
        else:
            vf = f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps={FPS},{GRADE}"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])
    else:
        frames = int(d * FPS) + 2
        step = (0.5 if punch else 0.35) / frames
        if n % 2 or punch:
            z = f"min(zoom+{step:.6f},1.5)"
        else:
            z = f"if(eq(on,1),1.35,max(zoom-{step:.6f},1.0))"
        vf = (f"scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={FPS},{GRADE}")
        run(["ffmpeg", "-y", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])


def ass_time(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(scenes, durs, path, hook_text=""):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,DejaVu Sans,62,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,120,120,90,1
Style: Hook,DejaVu Sans,150,&H0000D6FF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,9,2,5,120,120,60,1
Style: Chapter,DejaVu Sans,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,6,0,1,3,1,7,110,110,80,1

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
    # kinetic hook title (first seconds) + chapter labels at each scene start
    if hook_text.strip():
        words = hook_text.upper().split()
        k = (len(words) + 1) // 2 if len(words) > 2 else len(words)
        txt = " ".join(words[:k]) + ("\\N" + " ".join(words[k:]) if words[k:] else "")
        lines.append(f"Dialogue: 1,{ass_time(0.25)},{ass_time(5.0)},Hook,,0,0,0,,"
                     "{\\fad(200,500)\\fscx60\\fscy60\\t(0,400,\\fscx100\\fscy100)}" + txt.replace("{", "(").replace("}", ")"))
    t0 = 0.0
    for i, (sc, d) in enumerate(zip(scenes, durs)):
        if i > 0:
            title = sc.get("title", "").upper().replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 1,{ass_time(t0 + 0.3)},{ass_time(t0 + 3.6)},Chapter,,0,0,0,,"
                         "{\\fad(300,500)}" + f"{i + 1:02d}  |  {title}")
        t0 += d
    Path(path).write_text(head + "\n".join(lines) + "\n")


CHORDS = [[110.0, 130.81, 164.81, 220.0, 261.63, 329.63],    # Am
          [87.31, 130.81, 174.61, 220.0, 261.63, 349.23],    # F
          [130.81, 164.81, 196.0, 261.63, 329.63, 392.0],    # C
          [98.0, 146.83, 196.0, 246.94, 293.66, 392.0]]      # G


def _chord_expr(freqs):
    t = []
    for i, f in enumerate(freqs):
        a = 0.11 / (1 + i * 0.25)
        t.append(f"{a:.3f}*sin(2*PI*{f}*t)*(0.65+0.35*sin(2*PI*{0.05 + 0.013 * i:.3f}*t+{i}))")
        t.append(f"{a * 0.6:.3f}*sin(2*PI*{f * 1.004:.3f}*t)")          # slight detune = warmth
    return "+".join(t)


def make_music(total, out, wd):
    """Real track from assets/music if present; otherwise a slow evolving chord pad that is audible
    on small speakers (energy 110-800 Hz, chords change every ~11 s)."""
    wd = Path(wd)
    tracks = list((Path(__file__).resolve().parent.parent / "assets" / "music").glob("*.mp3"))
    if tracks:
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(random.choice(tracks)), "-t", f"{total + 5}",
             "-ar", "44100", "-ac", "2", str(out)])
        return
    seg = 14
    order = [0, 1, 2, 3, 0]
    cmd = ["ffmpeg", "-y"]
    for c in order:
        cmd += ["-f", "lavfi", "-i", f"aevalsrc='{_chord_expr(CHORDS[c])}':s=44100:d={seg}"]
    chain, last = [], "[0:a]"
    for k in range(1, len(order)):
        lab = f"[x{k}]"
        chain.append(f"{last}[{k}:a]acrossfade=d=3{lab}")
        last = lab
    chain.append(f"{last}lowpass=f=2600,aecho=0.8:0.6:700|1300:0.3|0.2,aformat=channel_layouts=stereo,"
                 f"afade=t=in:d=0.5,afade=t=out:st=57:d=0.5[loop]")
    run(cmd + ["-filter_complex", ";".join(chain), "-map", "[loop]", str(wd / "loop.wav")])
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(wd / "loop.wav"), "-t", f"{total + 5}", str(out)])


def make_whoosh(out):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anoisesrc=d=0.9:c=pink:r=44100:a=0.5",
         "-af", "highpass=f=300,lowpass=f=4500,afade=t=in:d=0.4,afade=t=out:st=0.4:d=0.5,aformat=channel_layouts=stereo",
         str(out)])


def make_impact(out):
    """Low cinematic hit for second 0 (sub sweep + dark noise tail)."""
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "aevalsrc='0.9*sin(2*PI*(75-35*t)*t)*exp(-3*t)':s=44100:d=1.8",
         "-f", "lavfi", "-i", "anoisesrc=d=1.8:c=brown:a=0.5:r=44100",
         "-filter_complex", "[1:a]lowpass=f=900,afade=t=out:st=0:d=1.5[n];[0:a][n]amix=inputs=2:normalize=0,"
                            "aformat=channel_layouts=stereo[o]", "-map", "[o]", str(out)])


def mix_audio(voice, music, whoosh, impact, starts, total, out):
    """Voice at -16 LUFS, music bed ~14 LU under it (gentle ducking while talking), whooshes on scene cuts."""
    n = len(starts)
    f = ["[0:a]loudnorm=I=-16:TP=-1.5:LRA=7,aresample=44100,asplit=2[v1][v2]",
         f"[1:a]loudnorm=I=-30:TP=-3:LRA=7,aresample=44100,afade=t=in:d=3,afade=t=out:st={max(total - 4, 1):.2f}:d=4[m]",
         "[m][v1]sidechaincompress=threshold=0.05:ratio=2.5:attack=40:release=800[duck]"]
    labels = ["[duck]", "[v2]"]
    f.append("[3:a]volume=0.8,adelay=50|50[imp]")
    labels.append("[imp]")
    if n:
        f.append(f"[2:a]asplit={n}" + "".join(f"[w{i}]" for i in range(n)))
        for i, st in enumerate(starts):
            ms = max(int(st * 1000) - 250, 0)
            f.append(f"[w{i}]adelay={ms}|{ms},volume=0.5[s{i}]")
            labels.append(f"[s{i}]")
    f.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
             f"loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    cmd = ["ffmpeg", "-y", "-i", str(voice), "-i", str(music), "-i", str(whoosh), "-i", str(impact)]
    cmd += ["-filter_complex", ";".join(f), "-map", "[a]", "-t", f"{total:.2f}", "-ar", "44100", str(out)]
    run(cmd)


def assemble(scenes, durs, plans, assets_by_scene, voice, wd, out, hook_text=""):
    """Render every shot, concat, burn titles/subtitles, mix sound design, mux."""
    wd = Path(wd)
    shots, n, clock = [], 0, 0.0
    for plan, assets in zip(plans, assets_by_scene):
        for l, a in zip(plan, assets):
            o = wd / f"shot{n:04d}.mp4"
            render_shot(a, l, o, n, punch=clock < 10.0)      # push-in on every shot of the cold open
            shots.append(o)
            clock += l
            n += 1
    (wd / "shots.txt").write_text("".join(f"file '{s.name}'\n" for s in shots))
    total = sum(durs)
    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d
    make_ass(scenes, durs, wd / "subs.ass", hook_text)
    make_music(total, wd / "music.wav", wd)
    make_whoosh(wd / "whoosh.wav")
    make_impact(wd / "impact.wav")
    mix_audio(voice, wd / "music.wav", wd / "whoosh.wav", wd / "impact.wav", starts[1:], total, wd / "mix.wav")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "shots.txt", "-i", "mix.wav",
         "-vf", "subtitles=subs.ass", "-map", "0:v", "-map", "1:a",      # no fade-in: the video starts on a hit
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)], cwd=wd)
    return starts
