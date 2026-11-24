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


def _pan_position(movement, frames):
    move = str(movement or "").lower().replace("-", " ")
    progress = f"min(on/{max(1, frames)},1)"
    span = "(iw-iw/zoom)"
    x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if "pan left" in move:
        x = f"{span}*(1-{progress})"
    elif "pan right" in move:
        x = f"{span}*{progress}"
    elif "tilt up" in move:
        y = f"(ih-ih/zoom)*(1-{progress})"
    elif "tilt down" in move:
        y = f"(ih-ih/zoom)*{progress}"
    return x, y


def render_shot(asset, d, out, n, punch=False, camera_movement=""):
    p = str(asset["path"])
    frames = max(1, int(float(d) * FPS))
    x, y = _pan_position(camera_movement, frames)
    move = str(camera_movement or "").lower()
    zoom_out = any(term in move for term in ("zoom out", "pull back", "pull out"))
    if asset["kind"] == "video":
        if punch:   # fast push-in used in the opening seconds
            vf = (f"fps={FPS},scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
                  f"zoompan=z='min(zoom+0.004,1.28)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={FPS},{GRADE}")
        else:
            z = ("if(eq(on,1),1.06,max(zoom-0.00022,1.0))" if zoom_out else
                 "if(eq(on,1),1.0,min(zoom+0.00022,1.06))" if n % 2 == 0 else
                 "if(eq(on,1),1.06,max(zoom-0.00022,1.0))")
            vf = (f"scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,fps={FPS},"
                  f"zoompan=z='{z}':x='{x}':y='{y}':d=1:"
                  f"s=1920x1080:fps={FPS},{GRADE}")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])
    else:
        frame_count = int(float(d) * FPS) + 2
        step = (0.5 if punch else 0.35) / frame_count
        if zoom_out:
            z = f"max(zoom-{step:.6f},1.0)"
        elif n % 2 or punch:
            z = f"min(zoom+{step:.6f},1.5)"
        else:
            z = f"if(eq(on,1),1.35,max(zoom-{step:.6f},1.0))"
        x, y = _pan_position(camera_movement, frame_count)
        vf = (f"scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={frame_count}:s=1920x1080:fps={FPS},{GRADE}")
        run(["ffmpeg", "-y", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])

def ass_time(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(scenes, durs, path, hook_text="", shot_labels=None):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,DejaVu Sans,62,&H00FFFFFF,&H0000D6FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,120,120,90,1
Style: Hook,DejaVu Sans,150,&H0000D6FF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,9,2,5,120,120,60,1
Style: Chapter,DejaVu Sans,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,6,0,1,3,1,7,110,110,80,1
Style: Source,DejaVu Sans,28,&H00FFFFFF,&H000000FF,&H80000000,&H80000000,1,0,0,0,100,100,0,0,3,2,0,7,90,90,50,1

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
            words_in_caption = c.replace("{", "(").replace("}", ")").split()
            weights = [max(1, len(w.strip(",.!?;:")) ) for w in words_in_caption]
            remaining_cs = max(len(weights), int(round(dur * 100)))
            total_weight = sum(weights) or 1
            karaoke = []
            for wi, word in enumerate(words_in_caption):
                cs = remaining_cs if wi == len(words_in_caption) - 1 else max(
                    1, int(round(dur * 100 * weights[wi] / total_weight)))
                remaining_cs -= cs
                karaoke.append(f"{{\\k{cs}}}{word}")
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + dur)},Main,,0,0,0,,"
                         + " ".join(karaoke))
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
            plan = sc.get("visual_plan", {})
        animation = str(plan.get("animation", "")).lower() if isinstance(plan, dict) else ""
        reveal = str(sc.get("information_reveal", "")).strip()
        label = f"{i + 1:02d}  |  {title}"
        if reveal and any(word in animation for word in ("label", "text", "graphic", "diagram", "callout")):
            short_reveal = " ".join(reveal.split()[:6]).upper().replace("{", "(").replace("}", ")")
            label += "\\N" + short_reveal
        transition = str(plan.get("transition", "")).lower() if isinstance(plan, dict) else ""
        fade_in = "150" if "flash" in transition else "300"
        lines.append(f"Dialogue: 1,{ass_time(t0 + 0.3)},{ass_time(t0 + 3.6)},Chapter,,0,0,0,,"
                     + f"{{\\fad({fade_in},500)}}{label}")
        t0 += d
    for start, end, label in (shot_labels or []):
        safe = str(label).replace("{", "(").replace("}", ")")
        if safe:
            lines.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Source,,0,0,0,,{safe}")
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


def _music_mood(emotion):
    e = (emotion or "").lower()
    if any(k in e for k in ("suspense", "myster", "ominous", "hush", "eerie")):
        return "suspense"
    if any(k in e for k in ("urgent", "danger", "tense", "panic", "dread")):
        return "tension"
    if any(k in e for k in ("sad", "somber", "grief", "tragic", "tender", "hope", "warm")):
        return "emotional"
    if any(k in e for k in ("awe", "wonder", "discovery", "marvel", "triumph")):
        return "wonder"
    return "neutral"


MUSIC_CHORDS = {
    "suspense": [55.0, 82.41, 110.0, 130.81],
    "tension": [49.0, 73.42, 98.0, 116.54],
    "emotional": [65.41, 82.41, 98.0, 130.81],
    "wonder": [130.81, 164.81, 196.0, 261.63],
    "neutral": [110.0, 130.81, 164.81, 220.0],
}


def _scene_music_expr(mood):
    freqs = MUSIC_CHORDS[mood]
    base = _chord_expr(freqs)
    if mood == "tension":
        return f"0.7*({base})*(0.72+0.28*sin(2*PI*0.22*t))"
    if mood == "suspense":
        return f"0.65*({base})*(0.82+0.18*sin(2*PI*0.08*t))"
    return f"0.58*({base})"


def _music_track(mood, tracks):
    named = [p for p in tracks if mood in p.stem.lower()]
    if named:
        return random.choice(named)
    neutral = [p for p in tracks if any(k in p.stem.lower() for k in ("neutral", "ambient", "documentary"))]
    return random.choice(neutral) if mood == "neutral" and neutral else None


def make_music(total, out, wd, scenes=None, durs=None):
    """Build a per-scene emotion bed from tagged licensed tracks or generated open-source synth."""
    wd = Path(wd)
    scenes = scenes or []
    durs = durs or []
    if not scenes or len(scenes) != len(durs):
        scenes = [{"emotion": "neutral"}]
        durs = [total]
    tracks = list((Path(__file__).resolve().parent.parent / "assets" / "music").glob("*.mp3"))
    cmd, filters, inputs = ["ffmpeg", "-y"], [], []
    crossfade = min(0.8, max(0.0, min(durs) / 4 if durs else 0.0))
    for i, (scene, duration) in enumerate(zip(scenes, durs)):
        visual_plan = scene.get("visual_plan", {})
        requested_mood = visual_plan.get("music_mood", "") if isinstance(visual_plan, dict) else ""
        mood = _music_mood(requested_mood or scene.get("emotion"))
        track = _music_track(mood, tracks)
        segment = max(float(duration), 0.1) + crossfade
        if track:
            cmd += ["-stream_loop", "-1", "-i", str(track)]
        else:
            cmd += ["-f", "lavfi", "-i", f"aevalsrc='{_scene_music_expr(mood)}':s=44100:d={segment:.3f}"]
        filters.append(f"[{i}:a]atrim=duration={segment:.3f},asetpts=PTS-STARTPTS,volume=0.32,"
                       f"afade=t=in:st=0:d={min(0.35,segment/3):.3f}[s{i}]")
        inputs.append(f"[s{i}]")
    last = inputs[0]
    for i in range(1, len(inputs)):
        nxt = f"[x{i}]"
        filters.append(f"{last}{inputs[i]}acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{nxt}")
        last = nxt
    filters.append(f"{last}atrim=duration={total:.3f},afade=t=in:d=1,afade=t=out:st={max(total-3,0):.3f}:d=2[m]")
    run(cmd + ["-filter_complex", ";".join(filters), "-map", "[m]", "-ar", "44100", str(out)])

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


def _source_label(asset):
    source = asset.get("source", "")
    if "Generated context" in source:
        return "ILLUSTRATIVE • CONTEXT"
    if "Internet Archive" in source:
        return "ARCHIVAL CLIP • INTERNET ARCHIVE"
    if "Wikimedia" in source:
        return "REFERENCE IMAGE • WIKIMEDIA"
    if "NASA" in source:
        return "NASA SOURCE"
    return f"ILLUSTRATIVE • {source.upper()}" if source else "ILLUSTRATIVE"


def assemble(scenes, durs, plans, assets_by_scene, voice, wd, out, hook_text=""):
    """Render every shot, label sources, burn captions, and mix sound design."""
    wd = Path(wd)
    shots, n, clock = [], 0, 0.0
    shot_labels = []
    for scene, plan, assets in zip(scenes, plans, assets_by_scene):
        visual_plan = scene.get("visual_plan", {})
        movement = visual_plan.get("camera_movement", "") if isinstance(visual_plan, dict) else ""
        for l, a in zip(plan, assets):
            o = wd / f"shot{n:04d}.mp4"
            render_shot(a, l, o, n, punch=clock < 10.0, camera_movement=movement)      # push-in on every shot of the cold open
            shots.append(o)
            shot_labels.append((clock, clock + l, _source_label(a)))
            clock += l
            n += 1
    (wd / "shots.txt").write_text("".join(f"file '{s.name}'\n" for s in shots))
    total = sum(durs)
    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d
    make_ass(scenes, durs, wd / "subs.ass", hook_text, shot_labels=shot_labels)
    make_music(total, wd / "music.wav", wd, scenes=scenes, durs=durs)
    make_whoosh(wd / "whoosh.wav")
    make_impact(wd / "impact.wav")
    mix_audio(voice, wd / "music.wav", wd / "whoosh.wav", wd / "impact.wav", starts[1:], total, wd / "mix.wav", scenes=scenes)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "shots.txt", "-i", "mix.wav",
         "-vf", "subtitles=subs.ass", "-map", "0:v", "-map", "1:a",      # no fade-in: the video starts on a hit
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)], cwd=wd)
    return starts
