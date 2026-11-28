import json, re, subprocess
from pathlib import Path
from . import director
from . import audio_director
from .common import cfg

FPS = 30
GRADE = "eq=contrast=1.1:saturation=1.1:brightness=-0.03:gamma_r=1.04:gamma_b=0.96,vignette=PI/5"   # warm highlights / cool shadows, cheap to render
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr[-1500:])


def grade_for(emotion):
    """Restrained scene-aware grade variation with no added runtime dependencies."""
    e = (emotion or "").lower()
    if any(k in e for k in ("suspense", "mystery", "ominous", "eerie", "tense", "dread")):
        return "eq=contrast=1.14:saturation=0.88:brightness=-0.045:gamma_r=0.98:gamma_b=1.06,vignette=PI/5"
    if any(k in e for k in ("warm", "reflect", "tender", "hope", "emotional")):
        return "eq=contrast=1.08:saturation=1.12:brightness=0.015:gamma_r=1.08:gamma_b=0.94,vignette=PI/5"
    if any(k in e for k in ("awe", "wonder", "discovery", "triumph", "marvel")):
        return "eq=contrast=1.12:saturation=1.16:brightness=0.005:gamma_r=1.04:gamma_b=0.98,vignette=PI/5"
    return GRADE


def _shot_is_complete(path, duration):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 50_000:
        return False
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=nw=1:nk=1", str(path)],
                                capture_output=True, text=True, timeout=20)
        return result.returncode == 0 and abs(float(result.stdout.strip()) - float(duration)) < 0.35
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def plan_shots(d, fast=0.0, scene=None, scene_index=0, scene_count=10):
    """Plan reproducible shot rhythm from narration density, emotional direction and story position."""
    scene = scene or {}
    planned = director.plan_shots(d, scene, scene_index=scene_index,
                                  scene_count=scene_count,
                               cold_open_seconds=fast)
    return planned


def render_shot(asset, d, out, n, punch=False, emotion="", movement="push_in", camera_movement=None):
    p = str(asset["path"])
    if camera_movement:
        requested = str(camera_movement).lower().replace("-", "_").replace(" ", "_")
        movement = {"pan_left": "drift_left", "pan_right": "drift_right",
                    "slow_push_in": "push_in", "slow_pull_out": "pull_out",
                    "static": "hold"}.get(requested, requested)
    if movement not in director.MOVEMENTS:
        movement = "hold"
    grade = grade_for(emotion)
    if asset["kind"] == "video":
        # Preserve real camera movement in footage; do not add a synthetic zoom to every clip.
        vf = (f"scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
              f"scale=1920:1080,fps={FPS},{grade}")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])
    else:
        frames = int(d * FPS) + 2
        if punch:
            movement = "push_in"
        zoom = 1.08 if movement in ("pull_out", "drift_left", "drift_right") else 1.0
        if movement == "pull_out":
            z = f"max({zoom}-on*{(zoom-1.0)/frames:.8f},1.0)"
        elif movement == "push_in":
            z = f"min(1+on*{0.08/frames:.8f},1.08)"
        else:
            z = f"{zoom}"
        if movement == "drift_left":
            x = f"(iw-iw/zoom)*(1-on/{max(1,frames-1)})"
        elif movement == "drift_right":
            x = f"(iw-iw/zoom)*on/{max(1,frames-1)}"
        else:
            x = "iw/2-(iw/zoom/2)"
        vf = (f"scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
              f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={FPS},{grade}")
        run(["ffmpeg", "-y", "-i", p, "-t", f"{d}", "-vf", vf, *X264, str(out)])


def ass_time(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _caption_chunks(narration, max_chars=52, max_words=8):
    """Break narration at clauses into short phrases (without invented word sync)."""
    words = re.sub(r"\s+", " ", str(narration)).strip().split()
    chunks, current = [], []
    for word in words:
        candidate = " ".join(current + [word])
        if current and (len(candidate) > max_chars or len(current) >= max_words):
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
        if word.endswith((".", "?", "!", ";", ":")) and current:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def _caption_lines(text, width=40):
    """Wrap short phrases to two lines in the lower safe area."""
    rows, row = [], ""
    for word in text.split():
        candidate = (row + " " + word).strip()
        if len(candidate) > width and row:
            rows.append(row)
            row = word
        else:
            row = candidate
    if row:
        rows.append(row)
    if len(rows) > 2:
        midpoint = (len(rows) + 1) // 2
        rows = [" ".join(rows[:midpoint]), " ".join(rows[midpoint:])]
    return "\\N".join(rows)


def caption_schedule(narration, duration, start=0.0):
    """Estimate phrase-level cue times inside a scene; this is not forced alignment."""
    chunks = _caption_chunks(narration)
    if not chunks:
        return []
    speech = max(0.1, float(duration) - 0.35)
    while len(chunks) > 1:
        weights = [max(1, len(text.replace(" ", ""))) for text in chunks]
        required = sum(max(speech * weight / sum(weights), 0.82) for weight in weights)
        if required <= speech + 1e-6:
            break
        pair = min(range(len(chunks) - 1),
                   key=lambda i: len(chunks[i]) + len(chunks[i + 1]))
        chunks[pair:pair + 2] = [chunks[pair] + " " + chunks[pair + 1]]
    weights = [max(1, len(text.replace(" ", ""))) for text in chunks]
    total_weight = sum(weights)
    cues, cursor = [], float(start)
    for text, weight in zip(chunks, weights):
        allocated = speech * weight / total_weight
        cue_duration = min(speech, max(0.82, allocated))
        cues.append((text, cursor, cursor + cue_duration))
        cursor += cue_duration
    return cues


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
        for text, cue_start, cue_end in caption_schedule(s["narration"], d, t0):
            safe = text.replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{ass_time(cue_start)},{ass_time(cue_end)},Main,,0,0,0,,"
                         + _caption_lines(safe))
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
        reveal = str(sc.get("information_reveal", "")).strip()
        visual_plan = sc.get("visual_plan") if isinstance(sc.get("visual_plan"), dict) else {}
        if reveal and visual_plan.get("animation"):
            # Restrained callouts are reserved for explicit, story-grounded reveals.
            reveal = reveal.upper().replace("{", "(").replace("}", ")")
            reveal_start = min(t0 + 1.0, t0 + max(0.2, d * 0.45))
            reveal_end = min(t0 + d, reveal_start + 4.5)
            if reveal_end > reveal_start:
                lines.append(f"Dialogue: 2,{ass_time(reveal_start)},{ass_time(reveal_end)},Chapter,,0,0,0,,"
                             "{\\fad(150,500)\\fscx92\\fscy92\\t(0,250,\\fscx100\\fscy100)}" + reveal)
        t0 += d
    for start, end, label in (shot_labels or []):
        safe = str(label).replace("{", "(").replace("}", ")")
        if safe:
            lines.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Source,,0,0,0,,{safe}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


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
    return audio_director.music_profile({"emotion": emotion})


MUSIC_CHORDS = {
    "suspense": [55.0, 82.41, 110.0, 130.81],
    "tension": [49.0, 73.42, 98.0, 116.54],
    "emotional": [65.41, 82.41, 98.0, 130.81],
    "wonder": [130.81, 164.81, 196.0, 261.63],
    "historical": [73.42, 110.0, 146.83, 196.0],
    "nature": [82.41, 123.47, 164.81, 220.0],
    "science": [65.41, 98.0, 146.83, 196.0],
    "cinematic": [110.0, 130.81, 164.81, 220.0],
}


def _scene_music_expr(mood):
    freqs = MUSIC_CHORDS.get(mood, MUSIC_CHORDS["cinematic"])
    base = _chord_expr(freqs)
    if mood == "tension":
        return f"0.7*({base})*(0.72+0.28*sin(2*PI*0.22*t))"
    if mood == "suspense":
        return f"0.65*({base})*(0.82+0.18*sin(2*PI*0.08*t))"
    return f"0.58*({base})"


def make_music(total, out, wd, scenes=None, durs=None, audio_plan=None):
    """Build a gently crossfaded, per-scene score from selected licensed tracks or synth."""
    wd = Path(wd)
    scenes = scenes or []
    durs = durs or []
    if not scenes or len(scenes) != len(durs):
        scenes = [{"emotion": "neutral"}]
        durs = [total]
    cmd, filters, inputs = ["ffmpeg", "-y"], [], []
    crossfade = min(float(cfg().get("audio", {}).get("music_crossfade_seconds", 1.2)),
                    max(0.0, min(durs) / 4 if durs else 0.0))
    for i, (scene, duration) in enumerate(zip(scenes, durs)):
        row = audio_plan[i] if audio_plan and i < len(audio_plan) else {}
        mood = row.get("profile") or _music_mood(scene)
        asset = row.get("music")
        track = asset.get("path") if asset else None
        segment = max(float(duration), 0.1) + crossfade
        if track:
            cmd += ["-stream_loop", "-1", "-i", str(track)]
        else:
            cmd += ["-f", "lavfi", "-i", f"aevalsrc='{_scene_music_expr(mood)}':s=44100:d={segment:.3f}"]
        gain = float(row.get("music_gain", 0.24))
        filters.append(f"[{i}:a]aresample=44100,atrim=duration={segment:.3f},asetpts=PTS-STARTPTS,volume={gain:.4f},"
                       f"afade=t=in:st=0:d={min(0.35,segment/3):.3f}[s{i}]")
        inputs.append(f"[s{i}]")
    last = inputs[0]
    for i in range(1, len(inputs)):
        nxt = f"[x{i}]"
        filters.append(f"{last}{inputs[i]}acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{nxt}")
        last = nxt
    filters.append(f"{last}atrim=duration={total:.3f},afade=t=in:d=1,afade=t=out:st={max(total-3,0):.3f}:d=2[m]")
    run(cmd + ["-filter_complex", ";".join(filters), "-map", "[m]", "-ar", "44100", str(out)])


def make_ambience(total, out, wd, scenes, durs, starts, audio_plan):
    """Lay in sparse, low-level, license-checked environmental recordings when cues support them."""
    cues, cmd, filters = [], ["ffmpeg", "-y"], []
    maximum = int(cfg().get("audio", {}).get("max_ambience_cues", 3))
    for index, row in enumerate(audio_plan or []):
        asset = row.get("ambience")
        if asset and len(cues) < maximum:
            cues.append((index, asset))
    if not cues:
        run(cmd + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{total:.3f}", str(out)])
        return []
    labels = []
    for i, (scene_i, asset) in enumerate(cues):
        cmd += ["-stream_loop", "-1", "-i", str(asset["path"])]
        start = max(0, int(float(starts[scene_i]) * 1000))
        duration = max(0.1, float(durs[scene_i]))
        fade = min(0.7, duration / 3)
        filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                       f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
                       f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={max(0,duration-fade):.3f}:d={fade:.3f},"
                       f"volume=0.12,adelay={start}|{start}[a{i}]")
        labels.append(f"[a{i}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
                   f"apad=whole_dur={total:.3f},atrim=duration={total:.3f}[amb]")
    run(cmd + ["-filter_complex", ";".join(filters), "-map", "[amb]", "-ar", "48000", str(out)])
    return [asset for _, asset in cues]

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


def _mix_filter(starts, total):
    """Dialogue-first mix: gentle voice control, key-driven score/ambience ducking and final limiter."""
    audio = cfg().get("audio", {})
    noise_reduction = max(0.0, min(20.0, float(audio.get("voice_noise_reduction_db", 0))))
    noise_filter = f"afftdn=nr={noise_reduction:g}:nf=-50:tn=1," if noise_reduction else ""
    f = ["[0:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=75," + noise_filter +
         "acompressor=threshold=0.12:ratio=2.0:attack=15:release=220,alimiter=limit=0.89,"
         "asplit=3[voice_main][voice_sc_music][voice_sc_amb]",
         f"[1:a]aresample=48000,volume=0.90,afade=t=in:d=2,afade=t=out:st={max(total - 4, 1):.2f}:d=3[music_pre]",
         "[music_pre][voice_sc_music]sidechaincompress=threshold=0.035:ratio=6:attack=45:release=700[music]",
         "[4:a]aresample=48000,volume=0.90[ambience_pre]",
         "[ambience_pre][voice_sc_amb]sidechaincompress=threshold=0.03:ratio=5:attack=60:release=500[ambience]",
         "[3:a]aresample=48000,volume=0.20,adelay=80|80[impact]"]
    labels = ["[voice_main]", "[music]", "[ambience]", "[impact]"]
    if starts:
        f.append(f"[2:a]asplit={len(starts)}" + "".join(f"[w{i}]" for i in range(len(starts))))
        for i, start in enumerate(starts):
            ms = max(int(float(start) * 1000) - 180, 0)
            f.append(f"[w{i}]aresample=48000,adelay={ms}|{ms},volume=0.20[s{i}]")
            labels.append(f"[s{i}]")
    f.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
             f"alimiter=limit=0.89,atrim=duration={total:.3f}[mix]")
    return ";".join(f)


def _loudnorm_report(path, target_lufs=-14.0, true_peak=-1.0, lra=10.0):
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
           f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:print_format=json", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError("audio loudness analysis failed: " + result.stderr[-1000:])
    blocks = re.findall(r"\{\s*\"input_i\".*?\}", result.stderr, re.S)
    if not blocks:
        raise RuntimeError("FFmpeg did not return loudness measurements")
    return json.loads(blocks[-1])


def measure_loudness(path):
    """Measure final-program integrated loudness and true peak for technical review."""
    audio = cfg().get("audio", {})
    return _loudnorm_report(path, audio.get("target_lufs", -14.0),
                            audio.get("true_peak_dbtp", -1.0), audio.get("lra", 10.0))


def audio_quality_review(path, total):
    """Measure final loudness, true peak, long silence and abrupt one-second RMS changes."""
    audio = cfg().get("audio", {})
    report = _loudnorm_report(path, audio.get("target_lufs", -14.0),
                              audio.get("true_peak_dbtp", -1.0), audio.get("lra", 10.0))
    # One-second windows find gross dropouts/jumps without retaining decoded audio.
    filter_ = ("silencedetect=noise=-55dB:d=1.2,asetnsamples=n=48000:p=1,"
              "astats=metadata=1:reset=1,"
              "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                             "-af", filter_, "-f", "null", "-"],
                            capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise RuntimeError("audio dynamics review failed: " + result.stderr[-500:])
    diagnostic = result.stdout + "\n" + result.stderr
    silence_starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", diagnostic)]
    silence_ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", diagnostic)]
    silence_events, end_index = [], 0
    for start in silence_starts:
        while end_index < len(silence_ends) and silence_ends[end_index] < start:
            end_index += 1
        end = silence_ends[end_index] if end_index < len(silence_ends) else float(total)
        if end - start >= 1.2:
            silence_events.append({"start_seconds": round(start, 2), "duration_seconds": round(end - start, 2)})
        end_index += int(end_index < len(silence_ends))
    levels = []
    for value in re.findall(r"lavfi\.astats\.Overall\.RMS_level=(-?(?:inf|[0-9.]+))", diagnostic):
        if value.lower() not in ("-inf", "inf"):
            levels.append(float(value))
    jumps = []
    for i, (before, after) in enumerate(zip(levels, levels[1:])):
        if abs(after - before) >= 18.0 and max(before, after) > -48:
            jumps.append({"window_seconds": i, "change_db": round(after - before, 1)})
    report.update({"silence_events": silence_events, "rms_jump_events": jumps,
                   "rms_window_count": len(levels),
                   "voice_clarity_protection": "high-pass, gentle compression, limiter and voice-keyed score/ambience ducking"})
    return report


def _normalize_file(source, out, target_lufs=-14.0, true_peak=-1.0, lra=10.0):
    measured = _loudnorm_report(source, target_lufs, true_peak, lra)
    values = {key: float(measured[key]) for key in
              ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    filter_ = (f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:"
               f"measured_I={values['input_i']}:measured_TP={values['input_tp']}:"
               f"measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}:"
               f"offset={values['target_offset']}:linear=true:print_format=summary")
    run(["ffmpeg", "-y", "-i", str(source), "-af", filter_, "-ar", "48000", str(out)])
    final_report = _loudnorm_report(out, target_lufs, true_peak, lra)
    try:
        missed_target = abs(float(final_report["input_i"]) - float(target_lufs)) > 1.0
        peak_over = float(final_report["input_tp"]) > float(true_peak) + 0.2
    except (KeyError, TypeError, ValueError):
        missed_target, peak_over = True, True
    if missed_target or peak_over:
        retry = Path(out).with_name(Path(out).stem + "_dynamic.wav")
        run(["ffmpeg", "-y", "-i", str(out), "-af",
             f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:linear=false:print_format=summary",
             "-ar", "48000", str(retry)])
        Path(retry).replace(out)
        final_report = _loudnorm_report(out, target_lufs, true_peak, lra)
    return final_report


def _dhash(gray_9x8):
    if len(gray_9x8) != 72:
        raise ValueError("a 9x8 grayscale frame is required")
    value = 0
    for y in range(8):
        row = gray_9x8[y * 9:(y + 1) * 9]
        for x in range(8):
            value = (value << 1) | int(row[x] > row[x + 1])
    return value


def visual_review(path, sample_interval=5):
    """Sample the full timeline and detect sustained black/frozen intervals and repeated images."""
    analysis = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                              "blackdetect=d=1.0:pix_th=0.10:pic_th=0.98,freezedetect=n=-60dB:d=4",
                              "-an", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=1800)
    if analysis.returncode:
        raise RuntimeError("black/freeze review failed: " + analysis.stderr[-500:])
    diagnostics = analysis.stderr
    black_intervals = []
    for match in re.finditer(r"black_start:([0-9.]+).*?black_end:([0-9.]+).*?black_duration:([0-9.]+)",
                             diagnostics):
        black_intervals.append({"start_seconds": round(float(match.group(1)), 2),
                                "end_seconds": round(float(match.group(2)), 2),
                                "duration_seconds": round(float(match.group(3)), 2)})
    freeze_starts = [float(value) for value in re.findall(
        r"lavfi\.freezedetect\.freeze_start:\s*([0-9.]+)", diagnostics)]
    freeze_ends = [float(value) for value in re.findall(
        r"lavfi\.freezedetect\.freeze_end:\s*([0-9.]+)", diagnostics)]
    freeze_durations = [float(value) for value in re.findall(
        r"lavfi\.freezedetect\.freeze_duration:\s*([0-9.]+)", diagnostics)]
    frozen_intervals, end_index = [], 0
    for index, start in enumerate(freeze_starts):
        while end_index < len(freeze_ends) and freeze_ends[end_index] < start:
            end_index += 1
        end = freeze_ends[end_index] if end_index < len(freeze_ends) else None
        duration = ((end - start) if end is not None else
                    freeze_durations[index] if index < len(freeze_durations) else None)
        if duration is not None and duration >= 4:
            frozen_intervals.append({"start_seconds": round(start, 2),
                                     "end_seconds": round(start + duration, 2),
                                     "duration_seconds": round(duration, 2)})
        end_index += int(end is not None)
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                             f"fps=1/{int(sample_interval)},scale=9:8:flags=area,format=gray",
                             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            capture_output=True, timeout=1800)
    if result.returncode:
        raise RuntimeError("frame sampling failed: " + result.stderr.decode("utf-8", "replace")[-500:])
    raw = result.stdout
    frames = [raw[index:index + 72] for index in range(0, len(raw) - 71, 72)]
    hashes = [_dhash(frame) for frame in frames]
    repeats = []
    for i, current in enumerate(hashes):
        for j in range(i + 3, len(hashes)):
            distance = (current ^ hashes[j]).bit_count()
            if distance <= 1:
                repeats.append({"first_seconds": i * sample_interval,
                                "again_seconds": j * sample_interval,
                                "hamming_distance": distance})
    means = [sum(frame) / len(frame) for frame in frames]
    return {"sample_interval_seconds": sample_interval, "sample_count": len(frames),
            "near_duplicate_pairs": repeats, "dark_samples": sum(mean < 7 for mean in means),
            "black_intervals": black_intervals, "frozen_intervals": frozen_intervals,
            "mean_luma_0_255": round(sum(means) / len(means), 2) if means else None}


def mix_audio(voice, music, whoosh, impact, starts, total, out, ambience=None):
    """Mix speech once with ducked score, then apply measured two-pass loudness normalization."""
    raw = Path(out).with_name(Path(out).stem + "_premix.wav")
    graph = _mix_filter(starts, total)
    ambience = ambience or Path(out).with_name("ambience.wav")
    run(["ffmpeg", "-y", "-i", str(voice), "-i", str(music), "-i", str(whoosh), "-i", str(impact), "-i", str(ambience),
         "-filter_complex", graph, "-map", "[mix]", "-t", f"{total:.2f}", "-ar", "48000", str(raw)])
    audio = cfg().get("audio", {})
    report = _normalize_file(raw, out, audio.get("target_lufs", -14.0),
                             audio.get("true_peak_dbtp", -1.0), audio.get("lra", 10.0))
    return report


def _source_label(asset):
    source = asset.get("source", "")
    if "timeline" in source.lower():
        return "TIMELINE • DATES FROM NARRATION"
    if "Generated context" in source:
        return "ILLUSTRATIVE • CONTEXT"
    if "Internet Archive" in source:
        return "ARCHIVAL CLIP • INTERNET ARCHIVE"
    if "Wikimedia" in source:
        return "REFERENCE IMAGE • WIKIMEDIA"
    if "NASA" in source:
        return "NASA SOURCE"
    return f"ILLUSTRATIVE • {source.upper()}" if source else "ILLUSTRATIVE"


def assemble(scenes, durs, plans, assets_by_scene, voice, wd, out, hook_text="", shot_decisions=None):
    """Render every shot, label sources, burn captions, and mix sound design."""
    wd = Path(wd)
    shots, n, clock = [], 0, 0.0
    shot_labels = []
    shot_decisions = shot_decisions or [[] for _ in scenes]
    for scene_i, (scene, plan, assets) in enumerate(zip(scenes, plans, assets_by_scene)):
        decisions = shot_decisions[scene_i] if scene_i < len(shot_decisions) else []
        for shot_i, (l, a) in enumerate(zip(plan, assets)):
            o = wd / f"shot{n:04d}.mp4"
            if not _shot_is_complete(o, l):
                decision = decisions[shot_i] if shot_i < len(decisions) else {}
                if not isinstance(decision, dict):
                    decision = {}
                visual_plan = scene.get("visual_plan")
                camera_movement = (visual_plan.get("camera_movement")
                                   if isinstance(visual_plan, dict) else None)
                render_shot(a, l, o, n, punch=clock < 10.0,
                            emotion=scene.get("emotion", ""),
                            movement=decision.get("movement", "push_in"),
                            camera_movement=camera_movement)
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
    audio_cfg = cfg().get("audio", {})
    audio_director_instance = audio_director.AudioDirector(
        Path(__file__).resolve().parent.parent, wd, audio_cfg)
    audio_plan = audio_director_instance.plan(scenes, durs)
    make_music(total, wd / "music.wav", wd, scenes=scenes, durs=durs, audio_plan=audio_plan)
    make_ambience(total, wd / "ambience.wav", wd, scenes, durs, starts, audio_plan)
    make_whoosh(wd / "whoosh.wav")
    make_impact(wd / "impact.wav")
    whoosh_cues = director.sound_cues(scenes, starts, audio_cfg.get("scene_whoosh_max", 3))
    mix_audio(voice, wd / "music.wav", wd / "whoosh.wav", wd / "impact.wav",
              whoosh_cues, total, wd / "mix.wav", ambience=wd / "ambience.wav")
    audio_report = {"scene_plan": [{"profile": row["profile"], "key_information": row["key_information"],
                                    "music_asset": row["music"].get("title") if row["music"] else "generated synth",
                                    "ambience_profile": row["ambience_profile"],
                                    "ambience_asset": row["ambience"].get("title") if row["ambience"] else None}
                                   for row in audio_plan],
                    "assets": audio_director_instance.credits(audio_plan),
                    "diagnostics": audio_director_instance.diagnostics}
    (wd / "audio_report.json").write_text(json.dumps(audio_report, ensure_ascii=False, indent=2), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "shots.txt", "-i", "mix.wav",
         "-vf", "subtitles=subs.ass", "-map", "0:v", "-map", "1:a",      # no fade-in: the video starts on a hit
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)], cwd=wd)
    return starts
