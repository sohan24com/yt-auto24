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



def _job_dir():
    path = Path(__file__).resolve().parent.parent / "memory" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_job_state(payload):
    path = _job_dir() / f"{payload['job_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _pending_job():
    for path in sorted(_job_dir().glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("state") in ("upload_pending", "long_uploaded", "short_pending", "short_failed"):
            return job
    return None


def _short_tag(job_id):
    return f"yt-auto24-id-{job_id}-short1"


def _publish_short(record, final_path, wd, tags, title, seconds):
    scenes = record.get("scenes", [])
    starts = [float(scene["start"]) for scene in scenes]
    durs = [float(scene.get("duration", 0)) for scene in scenes]
    if not scenes or not all(scene.get("narration") for scene in scenes):
        raise RuntimeError("saved scene narration is missing; cannot safely resume the Short")
    out = Path(wd) / "short.mp4"
    start, duration, scene_index = SH.choose_moment(scenes, starts, durs, seconds)
    SH.make_short(final_path, start, duration, out)
    problems = media_problems(out, expected_duration=duration, portrait=True,
                              expected_fps=30, deep=True)
    if problems:
        raise RuntimeError("Short quality check failed: " + "; ".join(problems))
    video_url = record["url"]
    description = (f"Watch the full documentary: {video_url}\\n"
                   "The story continues in the full video.\\n\\n#Shorts #documentary")
    tag = _short_tag(record["job_id"])
    existing = Y.find_upload_by_tag(tag)
    if existing:
        short_id = existing
    else:
        short_id = Y.upload(out, f"{title[:80]} #Shorts", description,
                            list(tags or ["documentary"]) + ["shorts"],
                            idempotency_tag=tag)
    record["short_scene"] = scenes[scene_index].get("title", "")
    record["short_id"] = short_id
    try:
        Y.add_comment(short_id, f"Full documentary: {video_url}")
    except Exception as e:
        print("Short comment not posted (non-fatal):", type(e).__name__)
    save_video(record)
    return short_id


def _resume_pending_job(job):
    """Resume the publish/Short stages from Drive artifacts without rendering/uploading a duplicate."""
    wd = Path(tempfile.mkdtemp(prefix="doc_resume_"))
    try:
        record = job["record"]
        metadata = job["metadata"]
        video_tag = f"yt-auto24-id-{job['job_id']}-long"
        if not record.get("video_id"):
            existing = Y.find_upload_by_tag(video_tag)
            if existing:
                video_id = existing
            else:
                final_path = wd / "final.mp4"
                D.download(job["drive_video_id"], final_path)
                thumb_path = wd / "thumb.jpg"
                if job.get("thumb_ids"):
                    D.download(job["thumb_ids"][0], thumb_path)
                video_id = Y.upload(final_path, metadata["title"], metadata["description"],
                                    metadata["tags"], thumb=thumb_path if thumb_path.exists() else None,
                                    idempotency_tag=video_tag)
            record["video_id"] = video_id
            record["url"] = f"https://youtu.be/{video_id}"
            record["created"] = job.get("created", now().isoformat())
            save_video(record)
            job["state"] = "long_uploaded"
            job["record"] = record
            _write_job_state(job)

        if job.get("shorts_enabled") and not record.get("short_id"):
            job["state"] = "short_pending"
            _write_job_state(job)
            final_path = wd / "final.mp4"
            D.download(job["drive_video_id"], final_path)
            _publish_short(record, final_path, wd, metadata.get("tags", []),
                           metadata["title"], float(job.get("short_seconds", 50)))
        job["state"] = "complete"
        job["record"] = record
        job["completed"] = now().isoformat()
        _write_job_state(job)
        notify(f"Resumed production job {job['job_id']}: {record.get('url', '')}")
    except Exception as e:
        job["last_error"] = type(e).__name__
        _write_job_state(job)
        raise
    finally:
        shutil.rmtree(wd, ignore_errors=True)

def main():
    print("code version:", (Path(__file__).resolve().parent.parent / "VERSION.txt").read_text().strip())
    c = cfg()
    force = os.environ.get("FORCE") == "1"
    pending = _pending_job()
    if pending:
        print("Resuming saved production job:", pending["job_id"])
        _resume_pending_job(pending)
        return
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

        rec = {"video_id": None, "url": "", "created": now().isoformat(),
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
        job = {"job_id": job_id, "state": "upload_pending", "created": rec["created"],
               "record": rec, "metadata": meta, "drive_video_id": drive_video,
               "thumb_ids": thumb_ids, "manifest_drive_id": manifest_drive_id,
               "shorts_enabled": bool(c["shorts"]["enabled"]),
               "short_seconds": c["shorts"]["seconds"]}
        _write_job_state(job)
        video_tag = f"yt-auto24-id-{job_id}-long"
        existing_video = Y.find_upload_by_tag(video_tag)
        if existing_video:
            vid = existing_video
        else:
            vid = Y.upload(final, meta["title"], meta["description"], meta["tags"],
                           thumb=thumbs[0], idempotency_tag=video_tag)
        rec["video_id"] = vid
        rec["url"] = f"https://youtu.be/{vid}"
        job["state"] = "long_uploaded"
        job["record"] = rec
        _write_job_state(job)
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
                job["state"] = "short_pending"
                _write_job_state(job)
                _publish_short(rec, final, wd, meta["tags"], meta["title"], c["shorts"]["seconds"])
                job["record"] = rec
                job["state"] = "complete"
                job["completed"] = now().isoformat()
                _write_job_state(job)
            except Exception as e:
                job["record"] = rec
                job["state"] = "short_failed"
                job["last_error"] = type(e).__name__
                _write_job_state(job)
                print("short failed; saved for the next run:", type(e).__name__)
        else:
            job["state"] = "complete"
            job["record"] = rec
            job["completed"] = now().isoformat()
            _write_job_state(job)
        notify(f"Published day {day}: {meta['title']}\nhttps://youtu.be/{vid}")
    except Exception as e:
        notify(f"PIPELINE FAILED: {e}\n{traceback.format_exc()[-1200:]}")
        raise
    finally:
        shutil.rmtree(wd, ignore_errors=True)


if __name__ == "__main__":
    main()
