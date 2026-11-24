"""One run = one finished documentary: topic -> script -> fact-check -> voice -> footage -> edit
-> quality gate -> thumbnails -> Drive -> YouTube -> Short -> memory."""
import datetime as dt, os, shutil, tempfile, traceback
from pathlib import Path
from . import topic as T, script as S, voice as V, footage as F, edit as E
from . import thumbnail as TH, shorts as SH, drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, notify


def stamp(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def gate(final, total, reuse):
    c = cfg()["video"]
    bad = []
    if not (c["min_seconds"] <= total <= c["max_seconds"]):
        bad.append(f"duration {total:.0f}s outside {c['min_seconds']}-{c['max_seconds']}")
    if reuse > c["max_footage_reuse"]:
        bad.append(f"footage reuse {reuse:.0%}")
    size = Path(final).stat().st_size
    if size < 20 << 20:
        bad.append(f"file too small ({size >> 20} MB)")
    return bad


def build_once(wd):
    F.reset()
    topic = T.choose_topic()
    print("TOPIC:", topic)
    script = None
    for _ in range(3):
        script = S.write_script(topic)
        if not S.valid(script):
            break
        print("script rejected:", S.valid(script))
    else:
        raise RuntimeError("could not produce a valid script")
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
        frame = Path(wd) / "frame.jpg"
        shots = sorted(Path(wd).glob("shot*.mp4"))          # frame from a clean shot (no burned subtitles)
        TH.grab_frame(shots[len(shots) // 8], 1.0, frame)
        thumbs = TH.make_thumbs(frame, meta["thumb_texts"], wd)

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
                SH.make_short(final, c["shorts"]["seconds"], sp)
                rec["short_id"] = Y.upload(sp, f"{meta['title'][:80]} #Shorts",
                                           f"Full documentary: https://youtu.be/{vid}\n\n#Shorts #documentary",
                                           meta["tags"] + ["shorts"])
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
