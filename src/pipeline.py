"""One run = one finished documentary: topic -> script -> fact-check -> voice -> footage -> edit
-> quality gate -> thumbnails -> Drive -> YouTube -> Short -> memory."""
import datetime as dt
import os
import shutil
import tempfile
import traceback
from pathlib import Path
from . import topic as T, script as S, voice as V, footage as F, edit as E
from . import thumbnail as TH, shorts as SH, drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, notify


def stamp(t):
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


def gate(final, total, reuse):
    c = cfg()["video"]
    bad = []
    if not (c["min_seconds"] <= total <= c["max_seconds"]):
        bad.append(f"duration {total:.0f}s outside {c['min_seconds']}-{c['max_seconds']}")
    if reuse > c["max_footage_reuse"]:
        bad.append(f"footage reuse {reuse:.0%}")
    fp = Path(final)
    if not fp.exists():
        bad.append("final video missing")
    else:
        size = fp.stat().st_size
        if size < 20 << 20:
            bad.append(f"file too small ({size >> 20} MB)")
    return bad


def build_once(wd):
    topic = T.choose_topic()
    print("TOPIC:", topic)

    script = None
    last_probs = []
    for _ in range(3):
        script = S.write_script(topic)
        probs = S.valid(script)
        if not probs:
            break
        last_probs = probs
        print("script rejected:", probs)
    else:
        raise RuntimeError(f"could not produce a valid script ({last_probs})")

    script = S.fact_check(script)
    scenes = script.get("scenes") or []
    if not scenes:
        raise RuntimeError("no scenes produced after fact_check")

    voice_wav, durs = V.build_voice(scenes, wd)
    total = sum(durs)
    print(f"voice {total:.0f}s")

    plans = [E.plan_shots(d) for d in durs]
    used, assets = set(), []
    for i, (s, p) in enumerate(zip(scenes, plans)):
        assets.append(F.gather(i, s.get("visuals", []), len(p), used, wd))
    all_assets = [a for sc in assets for a in sc]
    reuse = (sum(1 for a in all_assets if a.get("reused")) / len(all_assets)) if all_assets else 1.0

    final = Path(wd) / "final.mp4"
    starts = E.assemble(scenes, durs, plans, assets, voice_wav, wd, final)
    problems = gate(final, total, reuse)
    return topic, script, scenes, durs, starts, final, total, problems


def main():
    c = cfg()
    force = os.environ.get("FORCE") == "1"
    today = now().date()
    day = (today - dt.date.fromisoformat(str(c["start_date"]))).days + 1
    if day > c["days"] and not force:
        print(f"{c['days']}-day run finished (day {day}). Nothing to do.")
        return

    made_today = sum(1 for v in load_videos() if str(v.get("created", ""))[:10] == today.isoformat())
    if made_today >= c["videos_per_day"] and not force:
        print("daily quota already met")
        return

    wd = Path(tempfile.mkdtemp(prefix="doc_"))
    try:
        problems = []
        topic = script = scenes = durs = starts = final = total = None
        for attempt in range(1, 4):
            attempt_wd = wd / f"attempt_{attempt}"
            attempt_wd.mkdir(parents=True, exist_ok=True)
            topic, script, scenes, durs, starts, final, total, problems = build_once(attempt_wd)
            if not problems:
                break
            print(f"QUALITY GATE failed (attempt {attempt}):", problems)
        else:
            raise RuntimeError(f"quality gate failed 3x: {problems}")

        meta = S.make_metadata(
            topic,
            [(stamp(st), s) for st, s in zip(starts, scenes)],
            script.get("sources", []),
            F.credit_line(),
        )
        frame = Path(final).parent / "frame.jpg"
        TH.grab_frame(final, max(total * 0.12, 3), frame)
        thumbs = TH.make_thumbs(frame, meta["thumb_texts"], Path(final).parent)

        drive_video = D.upload(final, f"{today}_{meta['title'][:40]}.mp4")
        thumb_ids = [D.upload(t, f"{today}_{t.name}") for t in thumbs]

        vid = Y.upload(final, meta["title"], meta["description"], meta["tags"], thumb=thumbs[0])
        rec = {
            "video_id": vid,
            "url": f"https://youtu.be/{vid}",
            "created": now().isoformat(),
            "day": day,
            "topic": topic.get("topic"),
            "angle": topic.get("angle"),
            "category": topic.get("category"),
            "title": meta["title"],
            "title_b": meta["title_b"],
            "thumb_ids": thumb_ids,
            "drive_video_id": drive_video,
            "duration": round(total),
            "short_id": None,
            "scenes": [{"title": s["title"], "start": round(st)} for st, s in zip(starts, scenes)],
            "fact_check_changes": script.get("changes", []),
            "swapped": False,
            "snapshots": {},
        }
        save_video(rec)

        if (c.get("shorts") or {}).get("enabled"):
            try:
                sp = Path(final).parent / "short.mp4"
                SH.make_short(final, c["shorts"]["seconds"], sp)
                rec["short_id"] = Y.upload(
                    sp,
                    f"{meta['title'][:80]} #Shorts",
                    f"Full documentary: https://youtu.be/{vid}\n\n#Shorts #documentary",
                    meta["tags"] + ["shorts"],
                )
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
