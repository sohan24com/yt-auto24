"""One run = one finished documentary: topic -> script -> fact-check -> voice -> footage -> edit
-> quality gate -> thumbnails -> Drive -> YouTube -> Short -> memory."""
import datetime as dt, json, os, shutil, subprocess, tempfile, traceback
from pathlib import Path
from . import topic as T, script as S, voice as V, footage as F, edit as E
from . import thumbnail as TH, shorts as SH, drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, notify


def stamp(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def media_problems(path, expected_duration=None, portrait=False, min_bytes=1 << 20):
    """Probe completed media and reject missing, incomplete, wrong-frame, or unsynchronized output."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < min_bytes:
        return ["missing or undersized output file"]
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if r.returncode:
        return ["ffprobe could not read output"]
    try:
        info = json.loads(r.stdout)
        streams = info.get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video")
        audio = next(s for s in streams if s.get("codec_type") == "audio")
        duration = float(info["format"]["duration"])
    except (ValueError, KeyError, StopIteration, TypeError):
        return ["output is missing readable audio/video streams or duration"]
    bad = []
    expected_size = (1080, 1920) if portrait else (1920, 1080)
    if (int(video.get("width", 0)), int(video.get("height", 0))) != expected_size:
        bad.append(f"wrong resolution: {video.get('width')}x{video.get('height')}")
    def stream_duration(stream):
        try:
            return float(stream.get("duration", duration))
        except (TypeError, ValueError):
            return duration

    vd = stream_duration(video)
    ad = stream_duration(audio)
    if abs(vd - ad) > 1.0:
        bad.append(f"audio/video duration mismatch: {ad:.2f}s vs {vd:.2f}s")
    if expected_duration is not None and abs(duration - expected_duration) > 1.5:
        bad.append(f"output duration {duration:.2f}s differs from expected {expected_duration:.2f}s")
    return bad


def gate(final, total, reuse):
    c = cfg()["video"]
    bad = []
    if not (c["min_seconds"] <= total <= c["max_seconds"]):
        bad.append(f"duration {total:.0f}s outside {c['min_seconds']}-{c['max_seconds']}")
    if reuse > c["max_footage_reuse"]:
        bad.append(f"footage reuse {reuse:.0%}")
    bad.extend(media_problems(final, expected_duration=total, min_bytes=20 << 20))
    ass = Path(final).parent / "subs.ass"
    if not ass.is_file() or ass.stat().st_size < 100:
        bad.append("subtitle source missing or empty")
    elif sum(1 for line in ass.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue: 0,")) < 8:
        bad.append("subtitle events missing")
    return bad


def build_once(wd):
    F.reset()
    topic = T.choose_topic()
    print("TOPIC:", topic)
    script = None
    problems = []
    for _ in range(3):
        script = S.write_script(topic, validation_feedback=problems)
        problems = S.valid(script)
        if not problems:
            break
        print("script rejected:", problems)
    else:
        raise RuntimeError(f"could not produce a valid script: {problems}")
    checked = S.fact_check(script)
    for k in ("setting", "wiki_terms", "hook_text"):            # fact-check output may drop these
        checked[k] = script.get(k, checked.get(k))
    for i, sc in enumerate(checked["scenes"]):                  # ...and per-scene emotion / visuals
        if i < len(script["scenes"]):
            sc.setdefault("emotion", script["scenes"][i].get("emotion"))
            sc.setdefault("visuals", script["scenes"][i].get("visuals", []))
    script = checked
    scenes = script["scenes"]

    voice_wav, durs, engine = V.build_voice(scenes, wd)
    total = sum(durs)
    print(f"voice {total:.0f}s ({engine})")
    limits = cfg()["video"]
    if not (limits["min_seconds"] <= total <= limits["max_seconds"]):
        problem = f"voice duration {total:.0f}s outside {limits['min_seconds']}-{limits['max_seconds']}"
        print("QUALITY GATE failed before footage:", problem)
        return topic, script, scenes, durs, [], Path(wd) / "final.mp4", total, [problem]

    plans = [E.plan_shots(d, fast=10.0 if i == 0 else 0.0) for i, d in enumerate(durs)]   # cold open = fast cuts
    queries = S.shot_queries(topic, script, scenes, S.beats(scenes, durs, plans))
    beats_ = S.beats(scenes, durs, plans)
    wiki = F.wiki_pool(script.get("wiki_terms", []), wd)
    n_shots = sum(len(p) for p in plans)
    used, assets = set(), []
    for si in range(len(scenes)):
        row = []
        for bi in range(len(plans[si])):
            q, alt = queries[si][bi]
            row.append(F.gather_shot(q, alt, beats_[si][bi], script.get("setting", ""), used, wd, wiki,
                                     total_shots=n_shots))
        assets.append(row)
        print(f"scene {si + 1}/{len(scenes)} footage ready", flush=True)
    all_assets = [a for sc in assets for a in sc]
    reuse = sum(a["reused"] for a in all_assets) / len(all_assets)

    final = Path(wd) / "final.mp4"
    starts = E.assemble(scenes, durs, plans, assets, voice_wav, wd, final, hook_text=script.get("hook_text", ""))
    problems = gate(final, total, reuse)
    return topic, script, scenes, durs, starts, final, total, problems


def main():
    print("code version:", (Path(__file__).resolve().parent.parent / "VERSION.txt").read_text().strip())
    c = cfg()
    force = os.environ.get("FORCE") == "1"
    today = now().date()
    day = (today - dt.date.fromisoformat(str(c["start_date"]))).days + 1
    if day > c["days"] and not force:
        print(f"{c['days']}-day run finished (day {day}). Nothing to do.")
        return
    made_today = sum(1 for v in load_videos() if v["created"][:10] == today.isoformat())
    if made_today >= c["videos_per_day"] and not force:
        print("daily quota already met")
        return

    wd = tempfile.mkdtemp(prefix="doc_")
    try:
        for attempt in range(1, 4):
            shutil.rmtree(wd, ignore_errors=True)
            Path(wd).mkdir()
            topic, script, scenes, durs, starts, final, total, problems = build_once(wd)
            if not problems:
                break
            print(f"QUALITY GATE failed (attempt {attempt}):", problems)
        else:
            raise RuntimeError(f"quality gate failed 3x: {problems}")

        meta = S.make_metadata(topic, [(stamp(st), s) for st, s in zip(starts, scenes)],
                               script.get("sources", []), F.credit_line())
        shots = sorted(Path(wd).glob("shot*.mp4"))          # clean shots have no burned subtitles
        frame_paths = []
        for n, idx in enumerate(sorted({0, len(shots) // 2, len(shots) - 1})):
            frame = Path(wd) / f"frame_{n}.jpg"
            TH.grab_frame(shots[idx], 0.8, frame)
            frame_paths.append(frame)
        thumbs = TH.make_thumbs(frame_paths, meta["thumb_texts"], wd)

        drive_video = D.upload(final, f"{today}_{meta['title'][:40]}.mp4")
        thumb_ids = [D.upload(t, f"{today}_{t.name}") for t in thumbs]

        vid = Y.upload(final, meta["title"], meta["description"], meta["tags"], thumb=thumbs[0])
        rec = {"video_id": vid, "url": f"https://youtu.be/{vid}", "created": now().isoformat(),
               "day": day, "topic": topic["topic"], "angle": topic["angle"], "category": topic["category"],
               "title": meta["title"], "title_b": meta["title_b"], "thumb_ids": thumb_ids,
               "drive_video_id": drive_video, "duration": round(total), "short_id": None,
               "scenes": [{"title": s["title"], "start": round(st)} for st, s in zip(starts, scenes)],
               "fact_check_changes": script.get("changes", []), "swapped": False, "snapshots": {}}
        save_video(rec)

        if c.get("localization", {}).get("enabled"):          # titles/descriptions in the viewer's language
            try:
                intro = meta["description"].split("\n\nCHAPTERS")[0]
                tail = meta["description"][len(intro):]
                tr = S.translate_meta(meta["title"], intro, c["localization"]["languages"])
                Y.localize(vid, {k: {"title": v["title"][:100], "description": (v["description"] + tail)[:4900]}
                                 for k, v in tr.items()})
                rec["localized"] = sorted(tr)
                save_video(rec)
            except Exception as e:
                print("localization failed (non-fatal):", e)

        if c["shorts"]["enabled"]:
            try:
                sp = Path(wd) / "short.mp4"
                short_start, short_duration, short_scene = SH.choose_moment(
                    scenes, starts, durs, c["shorts"]["seconds"])
                SH.make_short(final, short_start, short_duration, sp)
                short_bad = media_problems(sp, expected_duration=short_duration, portrait=True)
                if short_bad:
                    raise RuntimeError("Short quality check failed: " + "; ".join(short_bad))
                full_url = f"https://youtu.be/{vid}"
                short_desc = (f"Watch the full documentary: {full_url}\n"
                              "The story continues in the full video.\n\n#Shorts #documentary")
                rec["short_scene"] = scenes[short_scene].get("title", "")
                rec["short_id"] = Y.upload(sp, f"{meta['title'][:80]} #Shorts",
                                           short_desc, meta["tags"] + ["shorts"])
                try:
                    Y.add_comment(rec["short_id"], f"Full documentary: {full_url}")
                except Exception as e:
                    print("Short comment not posted (non-fatal):", e)
                save_video(rec)
            except Exception as e:
                print("short failed (non-fatal):", e)
        notify(f"Published day {day}: {meta['title']}\nhttps://youtu.be/{vid}")
    except Exception as e:
        notify(f"PIPELINE FAILED: {e}\n{traceback.format_exc()[-1200:]}")
        raise
    finally:
        shutil.rmtree(wd, ignore_errors=True)


if __name__ == "__main__":
    main()
