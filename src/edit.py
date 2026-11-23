import random, re, subprocess
from pathlib import Path

FPS = 30
GRADE_PROFILES = {
    "hook": "eq=contrast=1.18:saturation=1.28:brightness=0.02,vignette=PI/4.2",
    "tension": "eq=contrast=1.15:saturation=0.92:brightness=-0.02,colorbalance=bs=0.06:bm=0.04,vignette=PI/4.3",
    "reveal": "eq=contrast=1.16:saturation=1.24:brightness=0.015,colorbalance=rs=0.05:gs=0.02,vignette=PI/4.6",
    "standard": "eq=contrast=1.12:saturation=1.18:brightness=0.005,vignette=PI/4.8",
}
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]

PAN_MODES = [
    ("zoom_in", "0.5 - 0.5/zoom", "0.5 - 0.5/zoom"),
    ("zoom_out", "0.5 - 0.5/zoom", "0.5 - 0.5/zoom"),
    ("pan_left_to_right", "on/max(1,d-1) * (1 - 1/zoom)", "0.45 - 0.45/zoom"),
    ("pan_right_to_left", "(1 - on/max(1,d-1)) * (1 - 1/zoom)", "0.55 - 0.55/zoom"),
    ("pan_top_to_bottom", "0.5 - 0.5/zoom", "on/max(1,d-1) * (1 - 1/zoom)"),
    ("pan_bottom_to_top", "0.5 - 0.5/zoom", "(1 - on/max(1,d-1)) * (1 - 1/zoom)"),
]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr[-1500:])


def _probe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return max(0.0, float(r.stdout.strip()))
    except Exception:
        return 0.0


def _scene_mood(narration, s_idx, total_scenes):
    if s_idx == 0:
        return "hook"
    txt = narration.lower()
    if any(k in txt for k in ("secret", "mystery", "disappear", "hidden", "unknown", "warning", "dark", "danger", "lost", "why")):
        return "tension"
    if s_idx == total_scenes - 1 or any(k in txt for k in ("truth", "answer", "finally", "discovered", "breakthrough", "proof", "future")):
        return "reveal"
    return "standard"


def plan_shots(d, is_hook=False):
    """Adaptive rhythm engine:
    - Opening hook (~6s): rapid 1.1-2.0s pattern interrupts
    - Body: escalating wave pacing (1.8-3.4s) synced to exact voice duration `d`"""
    out, rem = [], max(float(d), 0.1)
    elapsed = 0.0
    step_idx = 0
    while rem > 0.05:
        in_opening = is_hook and elapsed < 6.0
        if in_opening:
            lo, hi = (1.1, 2.0)
        else:
            # Wave pacing: alternates tighter punch shots with slightly broader establishing shots
            lo, hi = (1.8, 2.6) if (step_idx % 3 == 0) else (2.4, 3.5)
        l = random.uniform(lo, hi)
        min_tail = 0.9 if in_opening else 1.4
        if rem - l < min_tail:
            l = rem
        l = round(min(l, rem), 3)
        out.append(l)
        rem = round(rem - l, 3)
        elapsed += l
        step_idx += 1
    if out:
        diff = round(float(d) - sum(out), 3)
        out[-1] = max(0.2, round(out[-1] + diff, 3))
    return out


def _motion_expr(n, frames, is_opening=False):
    mode, x_norm, y_norm = PAN_MODES[n % len(PAN_MODES)]
    span = 0.30 if is_opening else 0.40
    step = span / max(1, frames)
    if mode == "zoom_out":
        z = f"if(eq(on,1),{1.0 + span:.2f},max(zoom-{step:.6f},1.0))"
    else:
        z = f"min(zoom+{step:.6f},{1.0 + span:.2f})"
    x_expr = f"iw*({x_norm})"
    y_expr = f"ih*({y_norm})"
    return z, x_expr, y_expr


def render_shot(asset, d, out, n, is_opening=False, mood="standard"):
    p = str(asset["path"])
    grade = GRADE_PROFILES["hook" if is_opening else mood]
    dur_str = f"{float(d):.3f}"
    frames = max(2, int(round(float(d) * FPS)) + 1)

    if asset["kind"] == "video":
        src_dur = _probe_duration(p)
        ss_args = []
        if src_dur > float(d) + 1.5:
            max_offset = max(0.0, src_dur - float(d) - 0.3)
            # Deterministic varied offset per shot index so reused clips show fresh segments
            offset = round(((n * 2.73) % max(0.1, max_offset)), 2)
            ss_args = ["-ss", f"{offset:.2f}"]

        z_span = 0.14 if is_opening else 0.10
        step = z_span / frames
        z = f"min(1+{z_span:.2f},1+{step:.6f}*on)" if (n % 2 == 0) else f"max(1.0,{1.0 + z_span:.2f}-{step:.6f}*on)"
        vf = (
            f"scale=2112:1188:force_original_aspect_ratio=increase,crop=2112:1188,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={FPS},"
            f"{grade},unsharp=5:5:0.75:5:5:0.0,format=yuv420p"
        )
        run(["ffmpeg", "-y", "-stream_loop", "-1", *ss_args, "-i", p, "-t", dur_str, "-vf", vf, *X264, str(out)])
    else:
        z, x_expr, y_expr = _motion_expr(n, frames, is_opening=is_opening)
        vf = (
            f"scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,"
            f"zoompan=z='{z}':x='{x_expr}':y='{y_expr}':d={frames}:s=1920x1080:fps={FPS},"
            f"{grade},unsharp=5:5:0.75:5:5:0.0,format=yuv420p"
        )
        run(["ffmpeg", "-y", "-loop", "1", "-i", p, "-t", dur_str, "-vf", vf, *X264, str(out)])


def ass_time(t):
    t = max(0.0, float(t))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _highlight_words(chunk, is_hook=False, mood="standard"):
    """Semantic keyword coloring: numbers/stats in bright yellow, high-impact words in cyan/gold."""
    tokens = chunk.split(" ")
    styled = []
    accent = r"&H0000E5FF&" if (is_hook or mood == "reveal") else r"&H00FFEA00&"
    for tok in tokens:
        clean = re.sub(r"[^A-Za-z0-9$%]", "", tok)
        if re.search(r"\d|\$|%", clean):
            styled.append(r"{\c&H0000E5FF&\b1}" + tok + r"{\r}")
        elif len(clean) >= 7:
            styled.append(r"{\c" + accent + r"\b1}" + tok + r"{\r}")
        else:
            styled.append(tok)
    return " ".join(styled)


def make_ass(scenes, durs, path):
    """Kinetic 2-3 word captions with active word pop-in and chapter title lower-thirds."""
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,DejaVu Sans,74,&H00FFFFFF,&H0000E5FF,&H00000000,&HB4000000,-1,0,0,0,104,104,0.8,0,1,6,3,2,90,90,96,1
Style: Hook,DejaVu Sans,84,&H00FFFFFF,&H0000E5FF,&H00000000,&HC8000000,-1,0,0,0,108,108,1.2,0,1,7,3,2,80,80,106,1
Style: Chapter,DejaVu Sans,42,&H0000E5FF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,102,102,1.5,0,1,4,2,7,80,80,64,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines, t0 = [], 0.0
    total_scenes = len(scenes)
    for s_idx, (s, d) in enumerate(zip(scenes, durs)):
        d = float(d)
        is_hook = (s_idx == 0)
        mood = _scene_mood(s.get("narration", ""), s_idx, total_scenes)
        style = "Hook" if is_hook else "Main"

        # Chapter badge in top-left for non-hook scenes
        title = re.sub(r"\s+", " ", str(s.get("title", ""))).strip()
        if s_idx > 0 and title and d >= 2.8:
            badge = title.replace("{", "(").replace("}", ")").upper()
            badge_anim = r"{\fad(180,250)\pos(80,64)}"
            lines.append(
                f"Dialogue: 1,{ass_time(t0 + 0.15)},{ass_time(min(t0 + 2.6, t0 + d - 0.1))},Chapter,,0,0,0,,{badge_anim}{badge}"
            )

        words = re.sub(r"\s+", " ", s["narration"]).strip().split(" ")
        chunks, cur = [], []
        max_words = 2 if is_hook else 3
        for w in words:
            if not w:
                continue
            cur.append(w)
            if len(cur) >= max_words or (w.endswith((".", "?", "!", ",", ";", ":")) and len(cur) >= 2):
                chunks.append(" ".join(cur))
                cur = []
        if cur:
            chunks.append(" ".join(cur))

        speech = max(0.2, d - 0.18)
        total = sum(len(c) for c in chunks) or 1
        t = t0
        for c in chunks:
            dur = max(0.26, speech * len(c) / total)
            end_t = min(t + dur, t0 + d)
            safe = c.replace("{", "(").replace("}", ")").upper()
            highlighted = _highlight_words(safe, is_hook=is_hook, mood=mood)
            anim = r"{\fscx116\fscy116\t(0,70,\fscx100\fscy100)}"
            if end_t > t:
                lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(end_t)},{style},,0,0,0,,{anim}{highlighted}")
            t = end_t
        t0 += d
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def make_music(total, out, wd):
    """Energetic cinematic bed from assets/music or multi-layered harmonic pulse."""
    tracks = list((Path(__file__).resolve().parent.parent / "assets" / "music").glob("*.mp3"))
    dur = float(total) + 5.0
    if tracks:
        run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(random.choice(tracks)),
            "-t", f"{dur:.2f}",
            "-af", "highpass=f=50,lowpass=f=11500,acompressor=threshold=-16dB:ratio=3.5:attack=15:release=200",
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(out)
        ])
        return
    expr = (
        "0.12*sin(2*PI*55*t)*(0.72+0.28*sin(2*PI*2.0*t))"
        "+0.08*sin(2*PI*82.4*t)+0.06*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*0.12*t))"
        "+0.045*sin(2*PI*164.8*t)*(0.5+0.5*sin(2*PI*0.08*t))"
        "+0.025*sin(2*PI*220*t)*(0.5+0.5*sin(2*PI*0.04*t))"
    )
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={dur:.2f}",
        "-af", "lowpass=f=1300,aecho=0.75:0.65:400|800:0.25|0.18,aformat=channel_layouts=stereo",
        "-c:a", "pcm_s16le", str(out)
    ])


def make_whoosh(out):
    """Multi-layered transition whoosh + sub-bass impact."""
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anoisesrc=d=0.45:c=pink:r=44100:a=0.55",
        "-f", "lavfi", "-i", "sine=f=62:d=0.35:r=44100",
        "-filter_complex",
        "[0:a]highpass=f=420,lowpass=f=5500,afade=t=in:d=0.09,afade=t=out:st=0.18:d=0.27[w];"
        "[1:a]volume=0.38,afade=t=in:d=0.02,afade=t=out:st=0.05:d=0.30[b];"
        "[w][b]amix=inputs=2:duration=longest:normalize=0,aformat=channel_layouts=stereo[out]",
        "-map", "[out]", "-c:a", "pcm_s16le", str(out)
    ])


def make_pulse(out):
    """Subtle high-tech tick/pulse for mid-scene pattern interrupts."""
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=f=980:d=0.14:r=44100",
        "-f", "lavfi", "-i", "anoisesrc=d=0.14:c=white:r=44100:a=0.22",
        "-filter_complex",
        "[0:a]volume=0.25,afade=t=in:d=0.01,afade=t=out:st=0.03:d=0.11[t];"
        "[1:a]highpass=f=1800,lowpass=f=6000,afade=t=in:d=0.01,afade=t=out:st=0.02:d=0.12[n];"
        "[t][n]amix=inputs=2:duration=longest:normalize=0,aformat=channel_layouts=stereo[out]",
        "-map", "[out]", "-c:a", "pcm_s16le", str(out)
    ])


def mix_audio(voice, music, whoosh, starts, total, out, pulse=None, pulse_times=None):
    total = float(total)
    n = len(starts)
    pulse_times = pulse_times or []
    m_cnt = len(pulse_times) if pulse else 0
    fade_out_st = max(total - 3.0, 0.5)

    f = [
        f"[1:a]volume=0.30,afade=t=in:st=0:d=0.7,afade=t=out:st={fade_out_st:.2f}:d=3.0[m]",
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo,highpass=f=75,"
        "acompressor=threshold=-18dB:ratio=3:attack=10:release=120,asplit=2[v1][v2]",
        "[m][v1]sidechaincompress=threshold=0.012:ratio=14:attack=15:release=480[duck]",
    ]
    labels = ["[duck]", "[v2]"]
    inp_idx = 2

    if n:
        f.append(f"[{inp_idx}:a]asplit={n}" + "".join(f"[w{i}]" for i in range(n)))
        for i, st in enumerate(starts):
            ms = max(int(float(st) * 1000) - 140, 0)
            f.append(f"[w{i}]adelay={ms}|{ms},volume=0.28[s{i}]")
            labels.append(f"[s{i}]")
        inp_idx += 1

    if m_cnt:
        f.append(f"[{inp_idx}:a]asplit={m_cnt}" + "".join(f"[p{i}]" for i in range(m_cnt)))
        for i, pt in enumerate(pulse_times):
            ms = max(int(float(pt) * 1000) - 50, 0)
            f.append(f"[p{i}]adelay={ms}|{ms},volume=0.16[ps{i}]")
            labels.append(f"[ps{i}]")

    f.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,"
             f"loudnorm=I=-13.5:TP=-1.0:LRA=9[a]")

    cmd = ["ffmpeg", "-y", "-i", str(voice), "-i", str(music)]
    if n:
        cmd += ["-i", str(whoosh)]
    if m_cnt:
        cmd += ["-i", str(pulse)]
    cmd += ["-filter_complex", ";".join(f), "-map", "[a]", "-t", f"{total:.2f}",
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(out)]
    run(cmd)


def assemble(scenes, durs, plans, assets_by_scene, voice, wd, out):
    """Next-gen retention assembly: mood-adaptive grading, random clip offsets,
    kinetic captions + chapter badges, dual-layer SFX, and broadcast loudness."""
    wd = Path(wd)
    shots, n = [], 0
    total_scenes = len(scenes)
    for s_idx, (scene, plan, assets) in enumerate(zip(scenes, plans, assets_by_scene)):
        scene_elapsed = 0.0
        mood = _scene_mood(scene.get("narration", ""), s_idx, total_scenes)
        for l, a in zip(plan, assets):
            o = wd / f"shot{n:04d}.mp4"
            is_opening = (s_idx == 0 and scene_elapsed < 6.0)
            render_shot(a, l, o, n, is_opening=is_opening, mood=mood)
            shots.append(o)
            n += 1
            scene_elapsed += float(l)

    (wd / "shots.txt").write_text("".join(f"file '{s.name}'\n" for s in shots), encoding="utf-8")
    total = sum(float(d) for d in durs)
    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += float(d)

    # Scene transitions get whoosh+impact; mid-scene 14s beats get subtle pulse
    scene_sfx = list(starts[1:])
    pulse_sfx = []
    beat = 14.0
    while beat < total - 3.0:
        if all(abs(beat - st) > 3.0 for st in scene_sfx):
            pulse_sfx.append(beat)
        beat += 14.0

    make_ass(scenes, durs, wd / "subs.ass")
    make_music(total, wd / "music.wav", wd)
    make_whoosh(wd / "whoosh.wav")
    make_pulse(wd / "pulse.wav")
    mix_audio(
        voice, wd / "music.wav", wd / "whoosh.wav",
        scene_sfx, total, wd / "mix.wav",
        pulse=wd / "pulse.wav", pulse_times=pulse_sfx,
    )
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "shots.txt", "-i", "mix.wav",
        "-vf", "subtitles=subs.ass,fade=t=in:st=0:d=0.35",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{total:.2f}", str(out)
    ], cwd=wd)
    return starts
