"""One run = one finished documentary: topic -> script -> fact-check -> voice -> footage -> edit
-> quality gate -> thumbnails -> Drive -> YouTube -> Short -> memory."""
import datetime as dt, hashlib, json, os, re, shutil, subprocess, tempfile, traceback
from pathlib import Path
from . import topic as T, script as S, voice as V, footage as F, edit as E
from . import thumbnail as TH, shorts as SH, drive as D, youtube as Y
from .common import ROOT, cfg, now, load_videos, load_insights, save_video, notify
from . import quality, story as STORY
from .evidence import manifest_rows
from .checkpoints import BuildLock, StageCheckpoints, atomic_json, fingerprint


def stamp(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def media_problems(path, expected_duration=None, portrait=False, min_bytes=1 << 20,
                    expected_fps=None, deep=False):
    """Compatibility wrapper around the shared FFmpeg/FFprobe quality checker."""
    return quality.validate_video(path, expected_duration=expected_duration, portrait=portrait,
                                  min_bytes=min_bytes, expected_fps=expected_fps, deep=deep)


def gate(final, total, reuse, asset_manifest=None, scenes=None, durs=None,
         plans=None, assets_by_scene=None):
    c = cfg()["video"]
    bad = []
    if not (c["min_seconds"] <= total <= c["max_seconds"]):
        bad.append(f"duration {total:.0f}s outside {c['min_seconds']}-{c['max_seconds']}")
    if reuse > c["max_footage_reuse"]:
        bad.append(f"footage reuse {reuse:.0%}")
    bad.extend(media_problems(final, expected_duration=total, min_bytes=20 << 20,
                              expected_fps=30, deep=True))
    bad.extend(quality.validate_ass(Path(final).parent / "subs.ass", total))
    if scenes is not None:
        bad.extend(quality.validate_story_media(scenes, durs, plans, assets_by_scene))
    allowed = {"provider_terms", "allowed_public_domain", "allowed_attribution", "original_generated"}
    for row in asset_manifest or []:
        label = row.get("asset_id") or row.get("source") or "unknown asset"
        if row.get("license_status") not in allowed:
            bad.append(f"asset license needs review: {label}")
        if not row.get("source_url") or not row.get("license"):
            bad.append(f"asset provenance is incomplete: {label}")
        if row.get("license_status") != "original_generated" and not row.get("license_url"):
            bad.append(f"asset license URL is missing: {label}")
    return bad

def _build_input_fingerprint():
    """Invalidate cached work when code/config changes, without persisting secret values."""
    paths = [Path(__file__).resolve().parent.parent / "config.yaml",
             Path(__file__).resolve().parent.parent / "VERSION.txt"]
    paths.extend(sorted(Path(__file__).resolve().parent.glob("*.py")))
    digest = []
    for path in paths:
        digest.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return fingerprint(digest)


def _cache_json(checkpoints, stage, stage_fp, path):
    if checkpoints.is_complete(stage, stage_fp):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    checkpoints.invalidate_from(stage)
    return None


def _save_stage_json(checkpoints, stage, stage_fp, path, value, extra_artifacts=()):
    atomic_json(path, value)
    checkpoints.complete(stage, [path, *extra_artifacts], stage_fingerprint=stage_fp)


def _asset_to_record(asset, root):
    row = dict(asset)
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("footage asset escaped the persistent build workspace")
    row["path"] = path.relative_to(root.resolve()).as_posix()
    for key, value in list(row.items()):
        if isinstance(value, set):
            row[key] = sorted(value)
        elif isinstance(value, Path):
            row[key] = str(value)
    return row


def _asset_from_record(row, root):
    result = dict(row)
    relative = Path(result["path"])
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("cached footage asset escaped the persistent build workspace")
    result["path"] = str(path)
    if isinstance(result.get("match_words"), list):
        result["match_words"] = set(result["match_words"])
    return result


def _restore_footage_state(assets):
    F._all.extend(assets)
    for asset in assets:
        source = asset.get("source", "")
        if source:
            F.credits.add(source)
            F._source_counts[source] = F._source_counts.get(source, 0) + 1
        role = asset.get("visual_role", "general")
        F._role_counts[role] = F._role_counts.get(role, 0) + 1
        if asset.get("credit") and (asset.get("attribution_required") or
                                    asset.get("license_status") == "allowed_attribution" or
                                    source in ("Pexels", "Pixabay")):
            F.attributions.append(asset["credit"])
        if source == "Wikimedia Commons":
            F._wiki_used[0] += 1


def build_once(wd, validation_feedback=None):
    """Build/reuse each durable stage in a persistent per-job workspace."""
    wd = Path(wd).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    cp = StageCheckpoints(wd)
    base_fp = _build_input_fingerprint()
    F.reset()

    # Topic selection is real provider research. With no provider/key it raises; no fake topic is substituted.
    research_path = wd / "research.json"
    research_fp = fingerprint({"base": base_fp,
                               "used_topics": [v.get("topic") for v in load_videos()][-80:],
                               "insights": load_insights()})
    research = _cache_json(cp, "research", research_fp, research_path)
    if research is None:
        topic = T.choose_topic()
        research = {"topic": topic}
        _save_stage_json(cp, "research", research_fp, research_path, research)
    topic = research["topic"]
    print("TOPIC:", topic)

    # Script draft and its validation feedback have their own fingerprint.
    draft_path = wd / "script-draft.json"
    script_fp = fingerprint({"base": base_fp, "research": cp.stage_token("research"),
                             "feedback": list(validation_feedback or [])})
    script = _cache_json(cp, "script", script_fp, draft_path)
    if script is None:
        problems = list(validation_feedback or [])
        previous_draft = None
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            script = S.write_script(topic, validation_feedback=problems,
                                    previous_draft=previous_draft)
            problems = S.valid(script)
            if not problems:
                break
            print(f"script rejected (draft {attempt}/{max_attempts}):", problems)
            previous_draft = script if isinstance(script, dict) else None
            limits = cfg()["video"]
            if any(str(issue).startswith("word count ") for issue in problems):
                minimum, maximum = int(limits["words_min"]), int(limits["words_max"])
                target = (minimum + maximum) // 2
                per_scene_min = max(8, round((minimum - 42) / 16))
                per_scene_max = max(per_scene_min, round((maximum - 26) / 16))
                problems.append(
                    f"Narration budget: hard minimum {minimum} words; target {target} words; "
                    f"hard maximum {maximum} words. Scenes 2-17 should each be about "
                    f"{per_scene_min}-{per_scene_max} words. Add supported context; do not pad, repeat, "
                    "or omit evidence and payoffs."
                )
        else:
            raise RuntimeError(f"could not produce a valid script: {problems}")
        _save_stage_json(cp, "script", script_fp, draft_path, script)

    # Evidence validation must come from the configured grounded provider. Failed/empty evidence is never cached as success.
    evidence_path = wd / "evidence-validation.json"
    evidence_fp = fingerprint({"base": base_fp, "script": cp.stage_token("script")})
    checked = _cache_json(cp, "evidence_validation", evidence_fp, evidence_path)
    if checked is None:
        checked = S.fact_check(script)
        checked = STORY.preserve_planning(script, checked)
        checked = STORY.align_visual_anchors(checked)
        checked = STORY.reconcile_loop_references(checked)
        for key in ("setting", "wiki_terms", "hook_text"):
            checked[key] = script.get(key, checked.get(key))
        checked = STORY.attach_evidence(checked, checked.get("_fact_check", {}))
        fact_status = checked.get("_fact_check", {}).get("status")
        if (cfg().get("publishing", {}).get("require_search_linked_fact_check", True)
                and fact_status != "search_linked"):
            raise RuntimeError("publish held: this run has no valid search-linked claim evidence")
        _save_stage_json(cp, "evidence_validation", evidence_fp, evidence_path, checked)

    # Storyboard is a separate validated stage so downstream media never sees an unchecked plan.
    storyboard_path = wd / "storyboard.json"
    storyboard_fp = fingerprint({"base": base_fp,
                                 "evidence": cp.stage_token("evidence_validation")})
    script = _cache_json(cp, "storyboard", storyboard_fp, storyboard_path)
    if script is None:
        story_issues = STORY.validate_blueprint(checked, require_evidence=True)
        if story_issues:
            raise RuntimeError("publish held by story quality checks: " + "; ".join(story_issues[:12]))
        checked["story_quality"] = STORY.quality_score(checked, require_evidence=True)
        script = checked
        _save_stage_json(cp, "storyboard", storyboard_fp, storyboard_path, script)
    scenes = script["scenes"]

    narration_path = wd / "voice.wav"
    narration_info_path = wd / "narration.json"
    narration_fp = fingerprint({"base": base_fp, "storyboard": cp.stage_token("storyboard"),
                                "voice_config": cfg().get("video", {}).get("voice"),
                                "gemini_tts": cfg().get("video", {}).get("gemini_tts")})
    narration_info = _cache_json(cp, "narration", narration_fp, narration_info_path)
    if narration_info is None:
        narration_work = wd / ".narration-attempt"
        narration_work.mkdir(parents=True, exist_ok=True)
        voice_wav, durs, engine = V.build_voice(scenes, narration_work)
        os.replace(voice_wav, narration_path)
        narration_info = {"durations": durs, "engine": engine}
        _save_stage_json(cp, "narration", narration_fp, narration_info_path, narration_info,
                         extra_artifacts=[narration_path])
    voice_wav = narration_path
    durs = narration_info["durations"]
    total = sum(durs)
    print(f"voice {total:.0f}s ({narration_info['engine']})")
    limits = cfg()["video"]
    timing = cfg().get("story", {})
    timing_issues = STORY.validate_timing(
        script, durs,
        min_scene=float(timing.get("min_progression_seconds", 20)),
        max_scene=float(timing.get("max_progression_seconds", 45)),
        max_hook=float(timing.get("hook_max_seconds", 5)),
        max_total=float(limits["max_seconds"]),
    )
    if timing_issues:
        return topic, script, scenes, durs, [], wd / "final.mp4", total, timing_issues
    if not (limits["min_seconds"] <= total <= limits["max_seconds"]):
        problem = f"voice duration {total:.0f}s outside {limits['min_seconds']}-{limits['max_seconds']}s"
        return topic, script, scenes, durs, [], wd / "final.mp4", total, [problem]

    # Narration timing affects shot boundaries; preserve the choices so retries do not re-randomize them.
    footage_path = wd / "footage.json"
    footage_progress_path = wd / "footage-progress.json"
    footage_fp = fingerprint({"base": base_fp, "storyboard": cp.stage_token("storyboard"),
                              "narration": cp.stage_token("narration")})
    footage_data = _cache_json(cp, "footage", footage_fp, footage_path)
    footage_was_cached = footage_data is not None
    if footage_data is None:
        try:
            progress = json.loads(footage_progress_path.read_text(encoding="utf-8"))
            rows = progress.get("assets", [])
            progress_ok = progress.get("fingerprint") == footage_fp
            if progress_ok:
                for scene_assets in rows:
                    for record in scene_assets:
                        actual = cp.artifact_records([wd / record["path"]])[0]
                        if actual["sha256"] != record["sha256"]:
                            progress_ok = False
                            break
                    if not progress_ok:
                        break
            if not progress_ok:
                raise ValueError("stale partial footage")
            plans = progress["plans"]
            assets = [[_asset_from_record(a, wd) for a in row] for row in progress["assets"]]
        except (OSError, ValueError, KeyError, TypeError):
            plans = [E.plan_shots(d, fast=10.0 if i == 0 else 0.0)
                     for i, d in enumerate(durs)]
            progress = {"fingerprint": footage_fp, "plans": plans, "assets": []}
            atomic_json(footage_progress_path, progress)
            assets = []
        _restore_footage_state([a for row in assets for a in row])
        used = {a.get("asset_id") for row in assets for a in row if a.get("asset_id")}
        beats_ = S.beats(scenes, durs, plans)
        queries = progress.get("queries")
        if not queries:
            queries = S.shot_queries(topic, script, scenes, beats_)
            progress["queries"] = queries
            atomic_json(footage_progress_path, progress)
        prior_wiki_count = F._wiki_used[0]
        wiki = F.wiki_pool(script.get("wiki_terms", []), wd) if not assets else []
        F._wiki_used[0] = prior_wiki_count
        n_shots = sum(len(p) for p in plans)
        for si in range(len(assets), len(scenes)):
            row = []
            for bi in range(len(plans[si])):
                q, alt = queries[si][bi]
                row.append(F.gather_shot(q, alt, beats_[si][bi], script.get("setting", ""), used, wd, wiki,
                                         total_shots=n_shots, mood=scenes[si].get("emotion", ""),
                                         topic=topic.get("topic", ""),
                                         historical_period=scenes[si].get("historical_period", ""),
                                         scene_duration=durs[si] / max(1, len(plans[si]))))
            assets.append(row)
            records = [[dict(_asset_to_record(asset, wd), sha256=cp.artifact_records([asset["path"]])[0]["sha256"])
                        for asset in scene_assets] for scene_assets in assets]
            progress["assets"] = records
            atomic_json(footage_progress_path, progress)
            print(f"scene {si + 1}/{len(scenes)} footage ready", flush=True)
        flat_assets = [a for row in assets for a in row]
        reuse = sum(bool(a.get("reused")) for a in flat_assets) / max(1, len(flat_assets))
        all_records = [_asset_to_record(a, wd) for a in flat_assets]
        footage_data = {"plans": plans, "assets": all_records, "reuse": reuse}
        _save_stage_json(cp, "footage", footage_fp, footage_path, footage_data,
                         extra_artifacts=[a["path"] for a in flat_assets])
    plans = footage_data["plans"]
    assets = [[_asset_from_record(a, wd) for a in row]
              for row in _chunk_assets(footage_data["assets"], plans)]
    flat_assets = [a for row in assets for a in row]
    if footage_was_cached:
        _restore_footage_state(flat_assets)
    reuse = float(footage_data["reuse"])
    script["_asset_manifest"] = manifest_rows(flat_assets)

    # Editing prep and final encode are distinct durable stages. Shot-level progress survives a runner crash.
    edit_path = wd / "edit-stage.json"
    edit_fp = fingerprint({"base": base_fp, "storyboard": cp.stage_token("storyboard"),
                           "footage": cp.stage_token("footage"), "narration": cp.stage_token("narration")})
    edit_data = _cache_json(cp, "editing", edit_fp, edit_path)
    if edit_data is None:
        starts = E.prepare_edit(scenes, durs, plans, assets, voice_wav, wd,
                                hook_text=script.get("hook_text", ""), progress_fingerprint=edit_fp)
        edit_data = json.loads(edit_path.read_text(encoding="utf-8"))
        edit_artifacts = [*sorted(wd.glob("shot*.mp4")), wd / "shots.txt", wd / "subs.ass",
                          wd / "music.wav", wd / "whoosh.wav", wd / "impact.wav", wd / "mix.wav",
                          wd / "edit-progress.json", edit_path]
        cp.complete("editing", edit_artifacts, stage_fingerprint=edit_fp)
    starts = edit_data["starts"]

    final = wd / "final.mp4"
    render_fp = fingerprint({"base": base_fp, "editing": cp.stage_token("editing")})
    if not cp.is_complete("render", render_fp):
        cp.invalidate_from("render")
        E.render_prepared(wd, final, total)
        cp.complete("render", [final], stage_fingerprint=render_fp)

    qc_path = wd / "quality-control.json"
    qc_fp = fingerprint({"base": base_fp, "render": cp.stage_token("render"), "total": total,
                         "reuse": reuse, "asset_manifest": script["_asset_manifest"]})
    qc = _cache_json(cp, "quality_control", qc_fp, qc_path)
    if qc is None:
        problems = gate(final, total, reuse, script["_asset_manifest"], scenes, durs, plans, assets)
        qc = {"problems": problems}
        _save_stage_json(cp, "quality_control", qc_fp, qc_path, qc)
    return topic, script, scenes, durs, starts, final, total, qc["problems"]


def _chunk_assets(flat_assets, plans):
    result, index = [], 0
    for plan in plans:
        result.append(flat_assets[index:index + len(plan)])
        index += len(plan)
    if index != len(flat_assets):
        raise RuntimeError("cached footage does not match the saved shot plan")
    return result



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
    description = (f"Watch the full documentary: {video_url}\n"
                   "The story continues in the full video.\n\n#Shorts #documentary")
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
    # If an upload exists but its saved job manifest is missing, stop instead of
    # attaching that existing video ID to newly generated content.
    orphan_tag = f"yt-auto24-id-{job_id}-long"
    if Y.find_upload_by_tag(orphan_tag):
        raise RuntimeError("matching YouTube upload exists but its saved job state is missing; refusing a duplicate or mismatched record")

    # Keep resumable build artifacts under the existing Actions-cached .media-cache;
    # never place generated video/audio in committed memory or overwrite voices.txt.
    safe_job_id = re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)
    wd = ROOT / ".media-cache" / "builds" / safe_job_id
    wd.mkdir(parents=True, exist_ok=True)
    build_lock = BuildLock(wd)
    build_lock.__enter__()
    build_succeeded = False
    try:
        validation_feedback = []
        for attempt in range(1, 4):
            topic, script, scenes, durs, starts, final, total, problems = build_once(
                wd, validation_feedback=validation_feedback)
            if not problems:
                break
            validation_feedback = list(problems)
            StageCheckpoints(wd).invalidate_from("script")
            print(f"QUALITY GATE failed (attempt {attempt}):", problems)
        else:
            raise RuntimeError(f"quality gate failed 3x: {problems}")

        meta = S.make_metadata(topic, [(stamp(st), s) for st, s in zip(starts, scenes)],
                               script.get("sources", []), F.credit_line(),
                               fact_check=script.get("_fact_check"), story_blueprint=script)
        packaging_issues = STORY.validate_packaging(script, meta)
        if packaging_issues:
            raise RuntimeError("publish held by story packaging checks: " + "; ".join(packaging_issues))
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
        credits_json_path, credits_text_path = F.write_credit_files(script.get("_asset_manifest", []), wd)
        story_path = Path(wd) / "story_blueprint.json"
        story_artifact = {
            "schema_version": script.get("story_schema_version", 1),
            "title": meta["title"], "title_promise": script.get("title_promise", ""),
            "central_question": script.get("central_question", ""),
            "acts": script.get("acts", []), "open_loops": script.get("open_loops", []),
            "research_dossier": script.get("research_dossier", []),
            "fact_check": script.get("_fact_check", {}),
            "quality": script.get("story_quality", {}),
            "scenes": script.get("scenes", []),
        }
        story_path.write_text(json.dumps(story_artifact, indent=2), encoding="utf-8")
        drive_video = D.upload(final, f"{today}_{meta['title'][:40]}.mp4")
        manifest_drive_id = D.upload(manifest_path, f"{today}_asset_manifest.json")
        credits_drive_id = None
        try:
            credits_drive_id = D.upload(credits_text_path, f"{today}_media_credits.txt")
        except Exception as error:
            print("Media credits file not copied to Drive (manifest still included):", type(error).__name__)
        story_drive_id = D.upload(story_path, f"{today}_story_blueprint.json")
        thumb_ids = [D.upload(t, f"{today}_{t.name}") for t in thumbs]

        rec = {"video_id": None, "url": "", "created": now().isoformat(),
               "job_id": job_id, "day": day, "topic": topic["topic"], "angle": topic["angle"], "category": topic["category"],
               "open_research_sources": (topic.get("open_research_sources") or [])[:20],
               "learning_skills_offered": [
                   {key: skill.get(key) for key in ("id", "version", "category", "confidence", "status",
                                                     "source_title", "source_url", "license")}
                   for skill in (topic.get("learning_skills") or [])[:5]
               ],
               "title": meta["title"], "title_b": meta["title_b"], "thumb_ids": thumb_ids,
               "drive_video_id": drive_video, "asset_manifest_drive_id": manifest_drive_id,
               "media_credits_drive_id": credits_drive_id,
               "story_blueprint_drive_id": story_drive_id,
               "story_quality": script.get("story_quality", {}),
               "central_question": script.get("central_question", ""),
               "title_promise": script.get("title_promise", ""),
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
        build_succeeded = True
    except Exception as e:
        notify(f"PIPELINE FAILED: {e}\n{traceback.format_exc()[-1200:]}")
        raise
    finally:
        build_lock.__exit__(None, None, None)
        # A successful publication no longer needs its private scratch workspace.
        # Failed builds remain available to the Actions cache for a safe resume.
        if build_succeeded:
            shutil.rmtree(wd, ignore_errors=True)


if __name__ == "__main__":
    main()
