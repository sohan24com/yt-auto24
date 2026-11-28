"""One run = one finished documentary: topic -> script -> fact-check -> voice -> footage -> edit
-> quality gate -> thumbnails -> Drive -> YouTube -> Short -> memory."""
import datetime as dt, hashlib, json, os, shutil, subprocess, tempfile, traceback
from pathlib import Path
from . import topic as T, script as S, voice as V, footage as F, edit as E
from . import thumbnail as TH, shorts as SH, drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, notify
from . import quality, story as STORY
from .evidence import manifest_rows
from . import director as DR, toolchain as TC

CHECKPOINT_VERSION = 3


def _state_path(wd):
    return Path(wd) / "checkpoint.json"


def _load_state(wd):
    try:
        state = json.loads(_state_path(wd).read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("version") == CHECKPOINT_VERSION:
            return state
    except (OSError, ValueError, TypeError):
        pass
    return {"version": CHECKPOINT_VERSION}


def _save_state(wd, state):
    path = _state_path(wd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _asset_to_json(asset):
    result = dict(asset)
    result["path"] = str(asset["path"])
    result["match_words"] = sorted(asset.get("match_words", set()))
    return result


def _asset_from_json(asset):
    return {**asset, "path": Path(asset["path"]), "match_words": set(asset.get("match_words", []))}


def _state_fingerprint(config, today, job_id=None):
    return hashlib.sha256(json.dumps({"date": today.isoformat(), "job_id": job_id, "config": config},
                                    sort_keys=True, default=str).encode()).hexdigest()


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
    try:
        audio = E.audio_quality_review(final, total)
        target = float(cfg().get("audio", {}).get("target_lufs", -14.0))
        peak_limit = float(cfg().get("audio", {}).get("true_peak_dbtp", -1.0))
        if abs(float(audio["input_i"]) - target) > 1.5:
            bad.append(f"integrated loudness {float(audio['input_i']):.1f} LUFS outside target {target:.1f} ±1.5")
        if float(audio["input_tp"]) > peak_limit + 0.3:
            bad.append(f"true peak {float(audio['input_tp']):.1f} dBTP exceeds {peak_limit:.1f} dBTP")
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        bad.append(f"audio loudness review failed: {type(exc).__name__}")
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


def _narration_budget_feedback(script, limits, reported_count=None):
    """Give the retry prompt a corrective instruction that matches the deficit."""
    current = (int(reported_count) if reported_count is not None else
               S.words(script.get("scenes", [])) if isinstance(script, dict) else 0)
    target = (int(limits["words_min"]) + int(limits["words_max"])) // 2
    minimum = int(limits["words_min"] * 0.9)
    if current < minimum:
        return (
            f"Narration budget is under length: current {current}; minimum {minimum}; target {target}. "
            f"Add at least {minimum - current} substantive, evidence-supported words across scenes 2-17. "
            "Expand context, evidence, consequences, and causal links; preserve the short opening and outro. "
            "Do not compress."
        )
    return (
        f"Narration budget is over length: current {current}; target {target}; "
        f"hard maximum {limits['words_max']}. Remove repeated exposition and decorative filler across scenes "
        "while preserving evidence, causality, uncertainty, and payoffs."
    )


def _fact_check_and_attach(script):
    """Run live claim checks and preserve the generated story plan."""
    checked = S.fact_check(script)
    checked = STORY.preserve_planning(script, checked)
    checked = STORY.align_visual_anchors(checked)
    checked = STORY.reconcile_loop_references(checked)
    for key in ("setting", "wiki_terms", "hook_text"):
        checked[key] = script.get(key, checked.get(key))
    return STORY.attach_evidence(checked, checked.get("_fact_check", {}))

def build_once(wd, validation_feedback=None):
    wd = Path(wd)
    state = _load_state(wd)
    F.reset()
    topic = state.get("topic") or T.choose_topic()
    state["topic"] = topic
    _save_state(wd, state)
    print("TOPIC:", topic)
    script = None
    fact_checked_this_run = False
    problems = list(validation_feedback or [])
    script = state.get("script")
    if not isinstance(script, dict):
        script = state.get("script_draft")
    if not isinstance(script, dict) or S.valid(script) or validation_feedback:
        if validation_feedback:
            state.pop("geometry", None)
            state.pop("assets", None)
            state.pop("voice", None)
            for artifact in ("voice.wav", "final.mp4", "audio_report.json", "subs.ass"):
                (wd / artifact).unlink(missing_ok=True)
        previous_draft = state.get("script_draft")
        if not isinstance(previous_draft, dict):
            previous_draft = script if isinstance(script, dict) else None
        problems = list(state.get("script_feedback") or validation_feedback or [])
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            script = S.write_script(topic, validation_feedback=problems,
                                    previous_draft=previous_draft)
            problems = list(S.valid(script))
            if not problems:
                if isinstance(script, dict):
                    state["script_draft"] = script
                    state["script_feedback"] = []
                    _save_state(wd, state)
                break
            print(f"script rejected (draft {attempt}/{max_attempts}):", problems)
            previous_draft = script if isinstance(script, dict) else None
            limits = cfg()["video"]
            word_count_issue = next((str(issue) for issue in problems
                                     if str(issue).startswith("word count ")), None)
            if word_count_issue:
                try:
                    reported_count = int(word_count_issue.split()[2])
                except (IndexError, ValueError):
                    reported_count = None
                problems.append(_narration_budget_feedback(script, limits, reported_count))
            if isinstance(script, dict):
                state["script_draft"] = script
                state["script_feedback"] = list(problems)
                _save_state(wd, state)
        else:
            raise RuntimeError(f"could not produce a valid script: {problems}")
        script = _fact_check_and_attach(script)
        fact_checked_this_run = True
        state.pop("script_draft", None)
        state.pop("script_feedback", None)
        state["script"] = script
        _save_state(wd, state)
    if (cfg().get("publishing", {}).get("require_search_linked_fact_check", True)
            and script.get("_fact_check", {}).get("status") != "search_linked"
            and not fact_checked_this_run):
        # An interrupted/failed run may have checkpointed a valid script with an
        # unavailable fact-check provider. Reuse the script, but retry grounded
        # research instead of treating a not_verified result as a completed stage.
        script = _fact_check_and_attach(script)
        state["script"] = script
        _save_state(wd, state)
    checked = script
    fact_status = checked.get("_fact_check", {}).get("status")
    if cfg().get("publishing", {}).get("require_search_linked_fact_check", True) and fact_status != "search_linked":
        raise RuntimeError("publish held: this run has no valid search-linked claim evidence")
    story_issues = STORY.validate_blueprint(checked, require_evidence=True)
    if story_issues:
        raise RuntimeError("publish held by story quality checks: " + "; ".join(story_issues[:12]))
    checked["story_quality"] = STORY.quality_score(checked, require_evidence=True)
    script = checked
    scenes = script["scenes"]

    voice_state = state.get("voice", {})
    voice_wav = wd / "voice.wav"
    if voice_wav.is_file() and voice_state.get("durations") and voice_state.get("engine"):
        durs, engine = voice_state["durations"], voice_state["engine"]
    else:
        voice_wav, durs, engine = V.build_voice(scenes, wd)
        state["voice"] = {"durations": durs, "engine": engine}
        _save_state(wd, state)
    total = sum(durs)
    print(f"voice {total:.0f}s ({engine})")
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
        print("QUALITY GATE failed before footage:", timing_issues)
        return topic, script, scenes, durs, [], Path(wd) / "final.mp4", total, timing_issues
    if not (limits["min_seconds"] <= total <= limits["max_seconds"]):
        problem = f"voice duration {total:.0f}s outside {limits['min_seconds']}-{limits['max_seconds']}s"
        print("QUALITY GATE failed before footage:", problem)
        return topic, script, scenes, durs, [], Path(wd) / "final.mp4", total, [problem]

    for i, scene in enumerate(scenes):
        scene["role"] = DR.scene_role(i, len(scenes))
        scene["category"] = topic.get("category", "") if isinstance(topic, dict) else ""
        scene["setting"] = script.get("setting", "")
    geometry = state.get("geometry")
    if geometry:
        plans, queries, beats_ = geometry["plans"], geometry["queries"], geometry["beats"]
    else:
        plans = [E.plan_shots(d, fast=10.0 if i == 0 else 0.0, scene=scenes[i],
                              scene_index=i, scene_count=len(scenes)) for i, d in enumerate(durs)]
        beats_ = S.beats(scenes, durs, plans)
        queries = S.shot_queries(topic, script, scenes, beats_)
        state["geometry"] = {"plans": plans, "queries": queries, "beats": beats_}
        state["assets"] = []
        _save_state(wd, state)
    wiki = F.wiki_pool(script.get("wiki_terms", []), wd)
    n_shots = sum(len(p) for p in plans)
    flat = [_asset_from_json(a) for a in state.get("assets", [])]
    used = set()
    if any(not a["path"].is_file() for a in flat):
        flat = []
    for asset in flat:
        if asset.get("source"):
            F.credits.add(asset["source"])
        if asset.get("license_status") == "allowed_attribution" and asset.get("credit"):
            F.attributions.append(asset["credit"])
        if not asset.get("reused"):
            used.add(asset["path"].stem)
            F._all.append(asset)
            if asset.get("content_hash"):
                F._content_hashes[asset["content_hash"]] = asset["path"].stem
    restored_wiki = {str(asset["path"]) for asset in flat
                     if asset.get("source") == "Wikimedia Commons"}
    for item in wiki:
        if str(item["path"]) in restored_wiki:
            item["used"] = True
            F._wiki_used[0] += 1
    assets, asset_index = [], 0
    for si in range(len(scenes)):
        row = []
        for bi in range(len(plans[si])):
            q, alt = queries[si][bi]
            if asset_index < len(flat):
                asset = flat[asset_index]
            else:
                asset = F.gather_shot(q, alt, beats_[si][bi], script.get("setting", ""), used, wd, wiki,
                                      total_shots=n_shots)
                flat.append(asset)
                state["assets"] = [_asset_to_json(a) for a in flat]
                _save_state(wd, state)
            row.append(asset)
            asset_index += 1
        assets.append(row)
        print(f"scene {si + 1}/{len(scenes)} footage ready", flush=True)
    all_assets = [a for sc in assets for a in sc]
    reuse = sum(a["reused"] for a in all_assets) / len(all_assets)
    script["_asset_manifest"] = manifest_rows(all_assets)

    final = Path(wd) / "final.mp4"
    audio_ready = (final.is_file() and (wd / "audio_report.json").is_file()
                   and not media_problems(final, expected_duration=total, min_bytes=20 << 20,
                                          expected_fps=30, deep=False))
    if audio_ready:
        starts, cursor = [], 0.0
        for duration in durs:
            starts.append(cursor)
            cursor += duration
    else:
        starts = E.assemble(scenes, durs, plans, assets, voice_wav, wd, final,
                             hook_text=script.get("hook_text", ""), shot_decisions=queries)
    problems = gate(final, total, reuse, script["_asset_manifest"])
    state["audio_review"] = E.audio_quality_review(final, total) if final.is_file() and not problems else {}
    _save_state(wd, state)
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
    media_tools = TC.detect_media_tools()
    print("Post-production tools:", json.dumps({"media": media_tools,
          "optional_editors": TC.software_inventory()}, ensure_ascii=False))
    if not media_tools.get("ready"):
        raise RuntimeError("FFmpeg/ffprobe or required audio filters are unavailable")
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

    root = Path(__file__).resolve().parent.parent
    checkpoint_root = Path(os.environ.get("CHECKPOINT_DIR", root / ".work" / "checkpoint")).resolve()
    fingerprint = _state_fingerprint(c, today, job_id)
    wd = checkpoint_root / f"{job_id}-{fingerprint[:10]}"
    checkpoint = _load_state(wd)
    wd.mkdir(parents=True, exist_ok=True)
    checkpoint["fingerprint"] = fingerprint
    checkpoint["run_date"] = today.isoformat()
    _save_state(wd, checkpoint)
    try:
        validation_feedback = []
        for attempt in range(1, 4):
            topic, script, scenes, durs, starts, final, total, problems = build_once(
                wd, validation_feedback=validation_feedback)
            if not problems:
                break
            validation_feedback = list(problems)
            print(f"QUALITY GATE failed (attempt {attempt}):", problems)
            (Path(wd) / "final.mp4").unlink(missing_ok=True)
            _load_state(wd).pop("audio_review", None)
        else:
            raise RuntimeError(f"quality gate failed 3x: {problems}")

        plans = _load_state(wd)["geometry"]["plans"]
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
        shot_scenes = [scene for scene, plan in zip(scenes, plans) for _ in plan]
        frame_info = []
        for frame, index in zip(frame_paths, sorted({0, len(shots) // 2, len(shots) - 1})):
            frame_info.append({"path": frame, "scene": shot_scenes[index]})
        thumbnail_package = TH.generate_thumbnail_package(
            frame_info, scenes, {**topic, "topic": topic.get("topic", "")}, meta, wd)
        thumbs = thumbnail_package["exports"]
        thumb_report = thumbnail_package["report"]
        thumb_candidates = {item["id"]: item for item in thumb_report.get("candidates", [])}
        selected_thumbs = [thumb_candidates[i] for i in thumb_report.get("selected", [])
                           if i in thumb_candidates]
        for thumb in thumbs:
            thumb_issues = quality.validate_thumbnail(thumb)
            if thumb_issues:
                raise RuntimeError("thumbnail quality check failed: " + "; ".join(thumb_issues))

        manifest_path = Path(wd) / "asset_manifest.json"
        manifest_path.write_text(json.dumps(script.get("_asset_manifest", []), indent=2), encoding="utf-8")
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
        story_drive_id = D.upload(story_path, f"{today}_story_blueprint.json")
        thumb_ids = [D.upload(t, f"{today}_{t.name}") for t in thumbs]

        rec = {"video_id": None, "url": "", "created": now().isoformat(),
               "job_id": job_id, "day": day, "topic": topic["topic"], "angle": topic["angle"], "category": topic["category"],
               "title": meta["title"], "title_b": meta["title_b"], "thumb_ids": thumb_ids,
               "thumbnail_options": [{"drive_id": drive_id, "score": item.get("score", {}),
                                      "concept": item.get("concept", "")}
                                     for drive_id, item in zip(thumb_ids, selected_thumbs)],
               "drive_video_id": drive_video, "asset_manifest_drive_id": manifest_drive_id,
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
    except Exception as e:
        notify(f"PIPELINE FAILED: {e}\n{traceback.format_exc()[-1200:]}")
        raise
    finally:
        # Keep checkpoints and validated render artifacts for a later run/resume.
        pass


if __name__ == "__main__":
    main()


