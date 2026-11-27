"""Deterministic documentary story, retention and evidence planning checks.

The model drafts story material; this module validates structure, timing, links and
storyboard completeness before expensive rendering or publication.
"""
import re
from urllib.parse import urlparse

STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "his", "how", "i", "in", "into", "is", "it",
    "its", "of", "on", "or", "our", "she", "that", "the", "their", "them", "there",
    "these", "they", "this", "those", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "we", "you", "your",
}
PLAN_FIELDS = ("story_purpose", "story_progress", "causal_link", "visual_anchor",
               "emotion", "opens_loop", "pays_off", "pattern_interrupt", "information_reveal",
               "target_duration_seconds", "visual_plan")
VISUAL_FIELDS = ("description", "camera_movement", "footage_keywords", "animation",
                 "sound_effect", "music_mood", "transition")


def _tokens(value):
    return {word.lower() for word in re.findall(r"[A-Za-z0-9']+", str(value))
            if len(word) > 2 and word.lower() not in STOP}


def _similarity(left, right):
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / max(1, len(a | b))


def _scene_map(blueprint):
    result = {}
    for index, scene in enumerate(blueprint.get("scenes", []), 1):
        if not isinstance(scene, dict):
            continue
        try:
            number = int(scene.get("number", index))
        except (TypeError, ValueError):
            continue
        result[number] = scene
    return result


def align_visual_anchors(blueprint):
    """Repair a scene anchor using only words in its narration and existing visuals."""
    if not isinstance(blueprint, dict) or not isinstance(blueprint.get("scenes"), list):
        return blueprint
    result = dict(blueprint)
    scenes = []
    for original in blueprint["scenes"]:
        if not isinstance(original, dict):
            scenes.append(original)
            continue
        scene = dict(original)
        raw_plan = scene.get("visual_plan")
        plan = dict(raw_plan) if isinstance(raw_plan, dict) else {}
        raw_keywords = plan.get("footage_keywords", [])
        existing_keywords = raw_keywords if isinstance(raw_keywords, list) else []
        narration = str(scene.get("narration", ""))
        narration_order = [word.lower() for word in re.findall(r"[A-Za-z0-9']+", narration)
                           if len(word) > 2 and word.lower() not in STOP]
        narration_terms = set(narration_order)
        visual_text = " ".join((str(plan.get("description", "")),
                                " ".join(str(value) for value in existing_keywords
                                         if isinstance(value, str))))
        shared = narration_terms & _tokens(visual_text)

        # A draft's existing stock-search options are valid repair material too.
        if not shared:
            options = scene.get("visuals", [])
            scored = []
            if isinstance(options, list):
                for phrase in options:
                    phrase_terms = narration_terms & _tokens(phrase)
                    if phrase_terms:
                        scored.append((len(phrase_terms), str(phrase), phrase_terms))
            if scored:
                _, phrase, shared = max(scored, key=lambda row: (row[0], row[1]))
                description = str(plan.get("description", "")).strip()
                if phrase.casefold() not in description.casefold():
                    plan["description"] = (description + " | visual target: " + phrase).strip(" |")
                keywords = list(existing_keywords)
                for word in re.findall(r"[A-Za-z0-9'-]+", phrase):
                    if word.lower() in shared and word not in keywords:
                        keywords.append(word)
                plan["footage_keywords"] = keywords[:8]

        current_anchor = _tokens(scene.get("visual_anchor", ""))
        if not shared:
            # If the model supplied an entirely unrelated visual, use a short phrase
            # already spoken in the scene and label the planned shot as illustrative.
            # This creates no new documentary claim or assertion of authenticity.
            visual_verbs = {"about", "after", "before", "become", "became", "being", "change", "changed",
                            "explain", "explains", "explained", "find", "finds", "found", "show", "shows",
                            "showed", "tell", "tells", "told", "think", "thought", "use", "used", "using",
                            "make", "made", "create", "created", "discover", "discovers", "discovered",
                            "reveal", "reveals", "revealed", "researcher", "researchers", "scientist",
                            "scientists", "historian", "historians", "according", "perhaps", "likely",
                            "might", "could", "would", "therefore", "however"}
            candidates = [word for word in narration_order if word not in visual_verbs]
            chosen = candidates[:3]
            if chosen:
                shared = set(chosen)
                phrase = " ".join(chosen)
                description = str(plan.get("description", "")).strip()
                plan["description"] = (description + " | illustrative, narration-derived visual target: " + phrase).strip(" |")
                keywords = list(existing_keywords)
                for word in chosen:
                    if word not in keywords:
                        keywords.append(word)
                plan["footage_keywords"] = keywords[:8]
                options = scene.get("visuals", [])
                options = list(options) if isinstance(options, list) else []
                if not any(phrase.casefold() == str(value).strip().casefold() for value in options):
                    scene["visuals"] = [phrase, *options[:3]]

        current_anchor = _tokens(scene.get("visual_anchor", ""))
        if shared and (not current_anchor or not current_anchor <= shared):
            chosen = []
            for word in narration_order:
                if word in shared and word not in chosen:
                    chosen.append(word)
                if len(chosen) == 3:
                    break
            scene["visual_anchor"] = " ".join(chosen)
        scene["visual_plan"] = plan
        scenes.append(scene)
    result["scenes"] = scenes
    return result


def reconcile_loop_references(blueprint):
    """Align declared loop locations to explicit scene markers without inventing payoffs."""
    if not isinstance(blueprint, dict) or not isinstance(blueprint.get("scenes"), list):
        return blueprint
    result = dict(blueprint)
    scenes = [dict(scene) if isinstance(scene, dict) else scene for scene in blueprint["scenes"]]
    result["scenes"] = scenes
    scene_by_number = {}
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        try:
            number = int(scene.get("number", index))
        except (TypeError, ValueError):
            continue
        scene_by_number[number] = scene
    loops = result.get("open_loops")
    if not isinstance(loops, list):
        return result
    normalized = []
    for loop in loops:
        if not isinstance(loop, dict):
            normalized.append(loop)
            continue
        row = dict(loop)
        loop_id = str(row.get("id", "")).strip()
        try:
            opened = int(row.get("opened_scene"))
        except (TypeError, ValueError):
            normalized.append(row)
            continue
        opening_markers = [number for number, scene in scene_by_number.items()
                           if loop_id in (scene.get("opens_loop") or [])]
        if opening_markers:
            opened = min(opening_markers)
            row["opened_scene"] = opened
            for number, scene in scene_by_number.items():
                scene["opens_loop"] = [item for item in (scene.get("opens_loop") or [])
                                        if item != loop_id or number == opened]

        try:
            payoff = int(row.get("payoff_scene"))
        except (TypeError, ValueError):
            payoff = None
        payoff_markers = sorted(number for number, scene in scene_by_number.items()
                                if loop_id in (scene.get("pays_off") or []) and number > opened)
        if payoff_markers:
            payoff = payoff_markers[0]
            row["payoff_scene"] = payoff
            for number, scene in scene_by_number.items():
                scene["pays_off"] = [item for item in (scene.get("pays_off") or [])
                                     if item != loop_id or number == payoff]
        normalized.append(row)
    result["open_loops"] = normalized
    return result


def evidence_dossier(record):
    """Convert Phase 2 evidence rows to stable IDs consumable by scene plans."""
    rows = record.get("claims", []) if isinstance(record, dict) else []
    dossier, by_scene = [], {}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        evidence_id = f"CLAIM_{index:03d}"
        item = {
            "id": evidence_id,
            "claim": str(row.get("claim", "")),
            "evidence": str(row.get("evidence", "")),
            "source": str(row.get("source", "")),
            "url": str(row.get("source_url", "")),
            "sources": list(row.get("sources", [])),
            "conflicts": list(row.get("conflicts", [])),
            "confidence": str(row.get("confidence", "unrated")),
            "status": "supported_with_uncertainty" if str(row.get("confidence", "")).lower() == "low"
                      else "search_linked_pending_editorial_review",
            "scene": int(row.get("scene", 0) or 0),
        }
        dossier.append(item)
        by_scene.setdefault(item["scene"], []).append(evidence_id)
    return dossier, by_scene


def attach_evidence(blueprint, fact_check):
    """Attach Phase 2 claim rows to the matching planned scene and keep provenance."""
    result = dict(blueprint)
    dossier, by_scene = evidence_dossier(fact_check)
    result["research_dossier"] = dossier
    scenes = []
    for index, original in enumerate(blueprint.get("scenes", []), 1):
        scene = dict(original)
        scene["number"] = int(scene.get("number", index))
        scene["evidence_ids"] = by_scene.get(scene["number"], [])
        scenes.append(scene)
    result["scenes"] = scenes
    return result


def preserve_planning(draft, checked):
    """Keep model-generated storyboard/act fields when Phase 2 rewrites narration."""
    result = dict(checked)
    for key in ("central_question", "title_promise", "acts", "open_loops", "research_dossier"):
        if key not in result and key in draft:
            result[key] = draft[key]
    result["story_schema_version"] = 1
    planned = draft.get("scenes", [])
    merged = []
    for index, scene in enumerate(result.get("scenes", []), 1):
        item = dict(scene)
        source = planned[index - 1] if index <= len(planned) else {}
        for field in PLAN_FIELDS + ("visuals", "evidence_ids", "number", "act", "title"):
            if field not in item and field in source:
                item[field] = source[field]
        item.setdefault("number", index)
        merged.append(item)
    result["scenes"] = merged
    return result


def validate_blueprint(blueprint, require_evidence=False):
    """Return deterministic structural, story, repetition, source and storyboard issues."""
    issues = []
    scenes = blueprint.get("scenes", []) if isinstance(blueprint, dict) else []
    if not isinstance(scenes, list) or len(scenes) < 12:
        return ["story: at least 12 scene beats are required"]
    scene_map = _scene_map(blueprint)
    scene_numbers = set(scene_map)
    if len(scene_map) != len(scenes) or scene_numbers != set(range(1, len(scenes) + 1)):
        issues.append("story: scene numbers must be unique, consecutive, and 1-based")

    acts = blueprint.get("acts", [])
    if not isinstance(acts, list) or len(acts) != 3:
        issues.append("story: exactly three acts are required")
    else:
        listed = []
        expected_act = {}
        for act_index, act in enumerate(acts, 1):
            numbers = act.get("scene_numbers", []) if isinstance(act, dict) else []
            if (not isinstance(act, dict) or not act.get("name")
                    or not act.get("dramatic_question") or not numbers):
                issues.append("story: every act needs a name, dramatic question, and scene list")
            listed.extend(numbers)
            try:
                for number in numbers:
                    expected_act[int(number)] = act_index
            except (TypeError, ValueError):
                pass
        try:
            if sorted(int(number) for number in listed) != list(range(1, len(scenes) + 1)):
                issues.append("story: acts must cover each scene exactly once")
            if any(int(scene_map[n].get("act", 0)) != expected_act.get(n)
                   for n in scene_numbers if n in expected_act):
                issues.append("story: scene act labels do not match the act outline")
        except (TypeError, ValueError):
            issues.append("story: act scene references must be integers")

    question = str(blueprint.get("central_question", "")).strip()
    promise = str(blueprint.get("title_promise", "")).strip()
    if len(question) < 12:
        issues.append("story: central_question is missing or too vague")
    if len(promise) < 12:
        issues.append("hook: title_promise is required")
    hook_scene = scene_map.get(1, {})
    hook = str(hook_scene.get("narration", "")).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", hook, maxsplit=1)[0]
    if not first_sentence or len(first_sentence.split()) > 12:
        issues.append("hook: first sentence must be speakable in five seconds (max 12 words)")
    if re.search(r"\b(hello|hi everyone|welcome to|today we are going to|let's dive)\b", hook, re.I):
        issues.append("hook: greetings and channel intro are not allowed")
    if not hook_scene.get("opens_loop"):
        issues.append("retention: opening hook must create a specific question")
    promise_terms = _tokens(promise)
    if len(promise_terms & _tokens(hook)) < min(2, len(promise_terms)):
        issues.append("hook: opening narration does not support the title promise")

    loops = blueprint.get("open_loops", [])
    loop_ids = set()
    loop_open_payoff = {}
    for loop in loops if isinstance(loops, list) else []:
        if not isinstance(loop, dict):
            issues.append("retention: malformed open loop")
            continue
        loop_id = str(loop.get("id", "")).strip()
        try:
            opened, payoff = int(loop.get("opened_scene")), int(loop.get("payoff_scene"))
        except (TypeError, ValueError):
            issues.append("retention: each loop needs valid opening and payoff scenes")
            continue
        if (not loop_id or loop_id in loop_ids or opened not in scene_numbers or payoff not in scene_numbers
                or payoff <= opened or not str(loop.get("question", "")).strip()):
            issues.append(f"retention: invalid or unpaid open loop {loop_id or '(missing id)'}")
            continue
        loop_ids.add(loop_id)
        loop_open_payoff[loop_id] = (opened, payoff)
        if loop_id not in scene_map[opened].get("opens_loop", []):
            issues.append(f"retention: {loop_id} has no matching opening beat")
        if loop_id not in scene_map[payoff].get("pays_off", []):
            issues.append(f"retention: {loop_id} has no matching payoff beat")
    for scene_number, scene in scene_map.items():
        for loop_id in scene.get("opens_loop", []) or []:
            if loop_id not in loop_open_payoff or loop_open_payoff[loop_id][0] != scene_number:
                issues.append(f"retention: scene {scene_number} opens an unregistered loop {loop_id}")
        for loop_id in scene.get("pays_off", []) or []:
            if loop_id not in loop_open_payoff or loop_open_payoff[loop_id][1] != scene_number:
                issues.append(f"retention: scene {scene_number} has an unregistered loop payoff {loop_id}")

    dossier = blueprint.get("research_dossier", [])
    dossier_ids = {str(row.get("id")) for row in dossier if isinstance(row, dict) and row.get("id")}
    for row in dossier if isinstance(dossier, list) else []:
        if not isinstance(row, dict):
            issues.append("evidence: malformed dossier entry")
            continue
        parsed = urlparse(str(row.get("url", "")))
        if not row.get("claim") or not row.get("source") or parsed.scheme != "https" or not parsed.netloc:
            issues.append(f"evidence: dossier entry {row.get('id', '(missing id)')} needs a claim and HTTPS source")
    evidence_scenes = set()
    for number, scene in scene_map.items():
        refs = scene.get("evidence_ids", []) or []
        if any(str(ref) not in dossier_ids for ref in refs):
            issues.append(f"evidence: scene {number} references a missing dossier ID")
        if refs:
            evidence_scenes.add(number)
    if require_evidence:
        if not dossier_ids:
            issues.append("evidence: research dossier is empty")
        if len(evidence_scenes) < min(len(scenes), 8):
            issues.append("evidence: source-linked claims must cover at least eight scenes")
        if not any(scene_map[n].get("evidence_ids") for n in scene_map if scene_map[n].get("act") == 1):
            issues.append("evidence: Act I has no linked evidence")
        if not any(scene_map[n].get("evidence_ids") for n in scene_map if scene_map[n].get("act") == 2):
            issues.append("evidence: Act II has no linked evidence")
        if not any(scene_map[n].get("evidence_ids") for n in scene_map if scene_map[n].get("act") == 3):
            issues.append("evidence: Act III has no linked evidence")

    required_visual = set(VISUAL_FIELDS)
    repetitions = []
    for number, scene in scene_map.items():
        narration = str(scene.get("narration", "")).strip()
        if len(narration.split()) < 8:
            issues.append(f"story: scene {number} narration is too short to advance the story")
        if not scene.get("story_purpose") or not scene.get("story_progress") or not scene.get("causal_link"):
            issues.append(f"story: scene {number} needs purpose, progression, and a cause/effect link")
        anchor = _tokens(scene.get("visual_anchor", ""))
        visual = scene.get("visual_plan")
        visual = visual if isinstance(visual, dict) else {}
        keywords = visual.get("footage_keywords", [])
        visual_text = " ".join((str(visual.get("description", "")),
                                " ".join(keywords) if isinstance(keywords, list) else ""))
        if not anchor or not anchor <= (_tokens(narration) & _tokens(visual_text)):
            issues.append(f"storyboard: scene {number} visual anchor must occur in narration and planned visuals")
        missing = [field for field in PLAN_FIELDS if field not in scene]
        if missing:
            issues.append(f"storyboard: scene {number} missing " + ", ".join(missing))
        if not isinstance(visual, dict) or required_visual - set(visual):
            issues.append(f"storyboard: scene {number} needs all visual, motion, sound, music and transition fields")
        elif (not visual.get("description") or not visual.get("camera_movement")
              or not isinstance(visual.get("footage_keywords"), list)
              or len(visual["footage_keywords"]) < 2):
            issues.append(f"storyboard: scene {number} visual directions are incomplete")
        try:
            target = float(scene.get("target_duration_seconds", 0))
            if number == 1 and not 0 < target <= 5:
                issues.append("hook: planned first scene target must be no longer than five seconds")
            if number > 1 and not 20 <= target <= 45:
                issues.append(f"retention: scene {number} target must progress within 20-45 seconds")
        except (TypeError, ValueError):
            issues.append(f"retention: scene {number} has invalid target duration")
        if not isinstance(scene.get("opens_loop", []), list) or not isinstance(scene.get("pays_off", []), list):
            issues.append(f"retention: scene {number} loop references must be lists")

    for i, left in enumerate(scenes):
        for right in scenes[i + 1:]:
            a, b = str(left.get("narration", "")), str(right.get("narration", ""))
            if min(len(_tokens(a)), len(_tokens(b))) >= 8 and _similarity(a, b) >= 0.82:
                repetitions.append((left.get("number"), right.get("number")))
    for left, right in repetitions:
        issues.append(f"story: scenes {left} and {right} repeat substantially similar narration")

    last = scene_map.get(len(scenes), {})
    if len(str(last.get("narration", "")).split()) < 20:
        issues.append("ending: final scene needs a satisfying resolution and natural outro")
    if not any(scene.get("pays_off") for scene in scenes[:-1]):
        issues.append("ending: no story loop is resolved before the outro")
    return list(dict.fromkeys(issues))


def validate_timing(blueprint, actual_durations, min_scene=20.0, max_scene=45.0,
                    max_hook=5.0, max_total=None):
    """Validate actual measured TTS scene audio, not word-count estimates."""
    scenes = blueprint.get("scenes", [])
    durations = [float(value) for value in actual_durations]
    issues = []
    if len(durations) != len(scenes):
        return ["timing: measured voice duration count does not match scene count"]
    if any(value <= 0 for value in durations):
        return ["timing: voice produced a zero-length scene"]
    if durations and durations[0] > max_hook:
        issues.append(f"hook: actual voiceover takes {durations[0]:.2f}s; maximum is {max_hook:.2f}s")
    for index, value in enumerate(durations[1:], 2):
        if value < min_scene or value > max_scene:
            issues.append(f"retention: actual scene {index} progression interval is {value:.2f}s, outside {min_scene:.0f}-{max_scene:.0f}s")
    first_30 = 0.0
    loop_open_times = {}
    for index, (scene, duration) in enumerate(zip(scenes, durations), 1):
        if first_30 < 30:
            first_30 += duration
        for loop_id in scene.get("opens_loop", []) or []:
            loop_open_times[loop_id] = sum(durations[:index - 1])
    if not any(opened_at <= 30 for opened_at in loop_open_times.values()):
        issues.append("retention: no specific curiosity loop is introduced in the first 30 seconds")
    total = sum(durations)
    if max_total is not None and total > max_total:
        issues.append(f"timing: total voice duration {total:.1f}s exceeds {max_total:.0f}s")
    return issues


def validate_packaging(blueprint, metadata):
    """Ensure the selected title and thumbnail text echo the documented story promise."""
    terms = _tokens(blueprint.get("title_promise", ""))
    title = str(metadata.get("title", ""))
    if len(terms & _tokens(title)) < min(2, len(terms)):
        return ["hook: selected title does not match the supported story promise"]
    thumb_texts = metadata.get("thumb_texts", [])
    if not thumb_texts or not any(terms & _tokens(text) for text in thumb_texts):
        return ["hook: no thumbnail concept matches the supported story promise"]
    return []


def quality_score(blueprint, require_evidence=False):
    """Return a weighted structural checklist score; this is not a score of factual truth."""
    issues = validate_blueprint(blueprint, require_evidence=require_evidence)
    categories = {
        "hook": (15, lambda issue: issue.startswith("hook:")),
        "three_act_structure": (15, lambda issue: issue.startswith("story:") and "repeat substantially" not in issue),
        "retention_and_payoffs": (20, lambda issue: issue.startswith("retention:")),
        "evidence_links": (15, lambda issue: issue.startswith("evidence:")),
        "storyboard_alignment": (20, lambda issue: issue.startswith("storyboard:")),
        "ending": (10, lambda issue: issue.startswith("ending:")),
        "originality": (5, lambda issue: "repeat substantially" in issue),
    }
    components = {}
    for name, (weight, matches) in categories.items():
        failed = any(matches(issue) for issue in issues)
        components[name] = {"earned": 0 if failed else weight, "possible": weight}
    score = sum(item["earned"] for item in components.values())
    return {"score": score, "label": "story_structure_score", "components": components, "issues": issues}
