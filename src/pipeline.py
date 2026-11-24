"""One run = one finished documentary: topic -> script -> fact-check -> voice -> footage -> edit
-> quality gate -> thumbnails -> Drive -> YouTube -> Short -> memory."""
import datetime as dt, json, os, shutil, subprocess, tempfile, traceback
from pathlib import Path
from . import topic as T, script as S, voice as V, footage as F, edit as E
from . import thumbnail as TH, shorts as SH, drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, notify
from . import quality
from .evidence import manifest_rows


def stamp(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def media_problems(path, expected_duration=None, portrait=False, min_bytes=1 << 20,
                    expected_fps=None, deep=False):
    """Compatibility wrapper around the shared FFmpeg/FFprobe quality checker."""
    return quality.validate_video(path, expected_duration=expected_duration, portrait=portrait,
                                  min_bytes=min_bytes, expected_fps=expected_fps, deep=deep)


def gate(final, total, reuse, asset_manifest=None):
    c = cfg()["video"]
    bad = []
    if not (c["min_seconds"] <= total <= c["max_seconds"]):
        bad.append(f"duration {total:.0f}s outside {c['min_seconds']}-{c['max_seconds']}")
    if reuse > c["max_footage_reuse"]:
        bad.append(f"footage reuse {reuse:.0%}")
    bad.extend(media_problems(final, expected_duration=total, min_bytes=20 << 20,
                              expected_fps=30, deep=True))
    bad.extend(quality.validate_ass(Path(final).parent / "subs.ass", total))
    allowed = {"provider_terms", "allowed_public_domain", "allowed_attribution", "original_generated"}
    for row in asset_manifest or []:
        if row.get("license_status") not in allowed:
            bad.append(f"asset license needs review: {row.get('asset_id') or row.get('source') or 'unknown asset'}")
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
    fact_status = script.get("_fact_check", {}).get("status")
    if cfg().get("publishing", {}).get("require_search_linked_fact_check", True) and fact_status != "search_linked":
        raise RuntimeError("publish held: this run has no valid search-linked claim evidence")
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
    script["_asset_manifest"] = manifest_rows(all_assets)

    final = Path(wd) / "final.mp4"
    starts = E.assemble(scenes, durs, plans, assets, voice_wav, wd, final, hook_text=script.get("hook_text", ""))
    problems = gate(final, total, reuse, script["_asset_manifest"])
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
    if force:
        run_id = os.environ.get("GITHUB_RUN_ID") or str(int(now().timestamp()))
        job_id = f"{today:%Y%m%d}-force-{run_id}"
    else:
        job_id = f"{today:%Y%m%d}-slot-{made_today + 1}"
    os.environ["YT_AUTO24_JOB_ID"] = job_id
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
                               script.get("sources", []), F.credit_line(),
                               fact_check=script.get("_fact_check"))
        shots = sorted(Path(wd).glob("shot*.mp4"))          # clean shots have no burned subtitles
        frame_paths = []
        for n, idx in enumerate(sorted({0, len(shots) // 2, len(shots) - 1})):
            frame = Path(wd) / f"frame_{n}.jpg"
            TH.grab_frame(shots[idx], 0.8, frame)
            frame_paths.append(frame)
        thumbs = TH.make_thumbs(frame_paths, meta["thumb_texts"], wd)
        for thumb in thumbs:
            thumb_issues = quality.validate_thumbnail(thumb)
            if thumb_issues:
                raise RuntimeError("thumbnail quality check failed: " + "; ".join(thumb_issues))

        manifest_path = Path(wd) / "asset_manifest.json"
        manifest_path.write_text(json.dumps(script.get("_asset_manifest", []), indent=2), encoding="utf-8")
        drive_video = D.upload(final, f"{today}_{meta['title'][:40]}.mp4")
        manifest_drive_id = D.upload(manifest_path, f"{today}_asset_manifest.json")
        thumb_ids = [D.upload(t, f"{today}_{t.name}") for t in thumbs]

        vid = Y.upload(final, meta["title"], meta["description"], meta["tags"], thumb=thumbs[0])
        rec = {"video_id": vid, "url": f"https://youtu.be/{vid}", "created": now().isoformat(),
               "job_id": job_id, "day": day, "topic": topic["topic"], "angle": topic["angle"], "category": topic["category"],
               "title": meta["title"], "title_b": meta["title_b"], "thumb_ids": thumb_ids,
               "drive_video_id": drive_video, "asset_manifest_drive_id": manifest_drive_id,
               "asset_manifest": script.get("_asset_manifest", []),
               "fact_check": script.get("_fact_check", {}),
               "duration": round(total), "short_id": None,
               "scenes": [{"title": scene["title"], "start": round(st), "duration": round(dur, 3),
                           "narration": scene.get("narration", ""), "emotion": scene.get("emotion", "")}
                          for st, dur, scene in zip(starts, durs, scenes)],
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
                short_bad = media_problems(sp, expected_duration=short_duration, portrait=True,
                                           expected_fps=30, deep=True)
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
