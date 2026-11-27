from .common import cfg, gemini, load_insights


def words(scenes):
    return sum(len(s["narration"].split()) for s in scenes)


def write_script(topic, validation_feedback=None, previous_draft=None):
    c = cfg()
    v = c["video"]
    lessons = "\n".join("- " + l for l in load_insights().get("lessons", []))
    feedback = "\n".join("- " + str(p) for p in (validation_feedback or [])) or "- none"
    target_total = (int(v["words_min"]) + int(v["words_max"])) // 2
    minimum_total = int(v["words_min"])
    maximum_total = int(v["words_max"])
    progression_budget = max(8, round((target_total - 38) / 16))
    per_scene_min = max(8, round((minimum_total - 42) / 16))
    per_scene_max = max(per_scene_min, round((maximum_total - 26) / 16))
    prompt = f"""Write a rigorous 8-10 minute English documentary narration using one evidence-led story blueprint.
Channel tone: {c['channel']['tone']}.
Topic: {topic['topic']}
Angle: {topic['angle']}

STORY CONTRACT
- Create exactly 18 numbered scenes in exactly three acts: Act I scenes 1-5; Act II scenes 6-14; Act III scenes 15-18.
- Scene 1 is a standalone cold-open hook: one truthful sentence, at most 12 words, no greeting, no channel intro.
  It must open a specific question answered later. It is the entire hook scene, not a paragraph that runs past five seconds.
- The central_question, title_promise and opening narration must describe the same documented story.
  Reuse at least two important title_promise words in the opening sentence, without making a claim
  stronger than the evidence. Add hook_text: a truthful 3-5 word on-screen phrase drawn from that hook.
- Act I establishes the object, stakes and investigation; Act II develops evidence and complications; Act III delivers the evidence-bounded answer, meaning and concise natural CTA.
- Create 1-3 open loops, each with a unique ID, opening scene, question and a later explicit payoff scene before scene 18.
- Every scene must move the story forward with new information, a reveal, an obstacle, a consequence or a payoff.
- Plan 20-45 seconds of spoken progression for scenes 2-18. Put a target_duration_seconds estimate in each plan; actual audio timing is checked after voice generation.
- Use a pattern interrupt only when it clarifies or changes the viewer's understanding; do not add empty spectacle.
- Deliver a satisfying resolution in scene 17, then a brief thank-you/Like/Comment/Subscribe/related-next-video invitation in scene 18.
- NARRATION LENGTH IS A HARD CONSTRAINT: write at least {minimum_total} narration words; hard maximum {maximum_total};
  aim for {target_total}. Count only spoken narration. Do not stop early.
  Scene 1 must be one standalone sentence of 6-12 words. Scene 18 should be a concise 20-30 word outro.
  Each of scenes 2-17 needs roughly {per_scene_min}-{per_scene_max} spoken words (aim for about
  {progression_budget} each). Add evidence-backed context, causal explanation, uncertainty and payoffs;
  do not pad or repeat. A scene with fewer than 8 narration words is invalid. Count narration only,
  not storyboard fields.

EVIDENCE AND HONESTY
- Facts must be checkable and linked to research; distinguish observed evidence, published interpretation, hypothesis and unknown.
- Never invent names, quotations, dates, numbers, source links, or certainty. Qualify unresolved disputes.
- Use a truthful, surprising reveal supported by evidence, not a fabricated twist.
- The later claim-to-source checker attaches source IDs; leave each scene's evidence_ids as [] in this draft.
- Do not restate the same fact in multiple scenes unless the later repetition adds a consequence or payoff.

SCENE-BY-SCENE STORYBOARD
For every scene return: number, act (1/2/3), title, narration, story_purpose, story_progress, causal_link, visual_anchor, emotion,
opens_loop (list of IDs), pays_off (list of IDs), pattern_interrupt, information_reveal,
target_duration_seconds, evidence_ids, visuals (4 stock search phrases) and visual_plan with:
description, camera_movement, footage_keywords (at least 2 concrete terms), animation, sound_effect,
music_mood and transition.
Clearly mark archival evidence vs illustrative/reconstructed imagery in visual descriptions. No unrelated visuals.

IMPORTANT VALIDATION RULES:
- Include every required root and scene field in the JSON schema below, especially hook_text.
- visual_anchor must be 1-3 concrete words/short phrase copied verbatim from BOTH that scene's narration
  and its visual_plan description or footage_keywords. Keep the planned visual tied to that narration.
- Before returning JSON, count narration words and check the opening, all 18 scene lengths, title promise,
  visual anchors, act labels, and loop payoffs against this contract.

Return ONLY JSON:
{{"central_question":"","title_promise":"","hook_text":"","acts":[{{"name":"Act I — ...","scene_numbers":[1,2,3,4,5],"dramatic_question":""}},{{"name":"Act II — ...","scene_numbers":[6,7,8,9,10,11,12,13,14],"dramatic_question":""}},{{"name":"Act III — ...","scene_numbers":[15,16,17,18],"dramatic_question":""}}],
"open_loops":[{{"id":"L1","question":"","opened_scene":1,"payoff_scene":8}}],
"scenes":[{{"number":1,"act":1,"title":"","narration":"","story_purpose":"","story_progress":"","causal_link":"","visual_anchor":"","emotion":"","opens_loop":["L1"],"pays_off":[],"pattern_interrupt":"","information_reveal":"","target_duration_seconds":4,"evidence_ids":[],"visuals":["","","",""],"visual_plan":{{"description":"","camera_movement":"","footage_keywords":["",""],"animation":"","sound_effect":"","music_mood":"","transition":""}}}}]}}
VALIDATION FEEDBACK FROM THE PREVIOUS DRAFT (correct each item):
{feedback}
If feedback reports a word count, revise the narration materially to fit the hard limit; do not merely acknowledge it.
Lessons from channel analytics (apply only when consistent with evidence):
{lessons or '- none yet'}"""
    if isinstance(previous_draft, dict):
        import json
        prompt += ("\n\nREPAIR THIS PREVIOUS DRAFT. Keep its researched subject, valid claims, evidence boundaries, "
                   "and useful story structure. Rewrite the narration and scene plans as needed to fix EVERY "
                   "validation item above. Do not copy its failures or invent facts. Return the complete JSON.\n"
                   "PREVIOUS DRAFT:\n" + json.dumps(previous_draft, ensure_ascii=False))
    result = gemini(prompt, temperature=0.75)
    if isinstance(result, dict):
        from .story import align_visual_anchors
        result = align_visual_anchors(result)
        if not str(result.get("hook_text", "")).strip() and result.get("scenes"):
            result["hook_text"] = _hook_text(result["scenes"][0].get("narration", ""))
        result["story_schema_version"] = 1
        from .story import quality_score
        result["story_quality"] = quality_score(result, require_evidence=False)
    return result

def fact_check(script):
    from .evidence import evidence_record, normalize_evidence

    prompt = f"""You are a strict documentary fact-checker. Use the available live web-search tool.
For every material factual claim, provide a claim-to-source record: scene number (1-based), exact claim,
a concise supporting explanation grounded in the search result, source name, direct HTTPS source URL,
and confidence (high/medium/low). Never invent a URL, publication, quote, or evidence.
Correct errors; remove or qualify claims that search does not support. Keep the same scenes and story.
Return ONLY JSON with:
{{"scenes":[...],"claims":[{{"scene":1,"claim":"","evidence":"","source":"","source_url":"https://...","confidence":"medium"}}],"changes":["..."]}}
Include only claims actually checked using search; do not say that this is independent human verification.

SCRIPT:
{script}"""
    try:
        out = gemini(prompt, grounding=True, temperature=0.2)
        scenes = out.get("scenes")
        claims, reason = normalize_evidence(out, len(script.get("scenes", [])))
        if (isinstance(scenes, list) and len(scenes) >= 8 and claims
                and abs(len(scenes) - len(script.get("scenes", []))) <= 1):
            result = dict(out)
            result["sources"] = list(dict.fromkeys(row["source_url"] for row in claims))
            result["_fact_check"] = evidence_record(claims)
            return result
        reason = reason or "fact-check response has an incomplete scene structure"
        print("fact_check returned incomplete evidence; keeping original script")
    except Exception as e:
        reason = type(e).__name__
        print("fact_check failed; keeping original script:", reason)
    fallback = dict(script)
    fallback["_fact_check"] = evidence_record([], reason)
    return fallback

def valid(script):
    v = cfg()["video"]
    sc = script.get("scenes", [])
    w = words(sc) if sc else 0
    problems = []
    if len(sc) < 8:
        problems.append("too few scenes")
    if not (v["words_min"] * 0.9 <= w <= v["words_max"] * 1.1):
        problems.append(f"word count {w}")
    # The hook is the opening sentence. Later narration in Scene 1 must not
    # make a correctly sized five-second hook fail validation.
    if sc:
        opening = str(sc[0].get("narration", "")).strip()
        ends = [opening.find(mark) for mark in ".?!"]
        ends = [index for index in ends if index >= 0]
        first_sentence = opening[:min(ends) + 1] if ends else opening
        if len(first_sentence.split()) > 12:
            problems.append("5-second hook too long (max 12 words in opening sentence)")
    hook = str(script.get("hook_text", "")).split()
    if not 3 <= len(hook) <= 5:
        # Older/model drafts may omit or mis-size this graphics-only field. Derive
        # it from the truthful cold open instead of wasting a complete script retry.
        derived_hook = _hook_text(sc[0].get("narration", "")) if sc else ""
        if derived_hook:
            script["hook_text"] = derived_hook
        else:
            problems.append("hook_text must contain 3-5 words")
    if not sc or len(sc[-1].get("narration", "").split()) < 20:
        problems.append("missing documentary outro/payoff")
    if script.get("story_schema_version") == 1:
        from .story import quality_score
        story_result = quality_score(script, require_evidence=False)
        problems.extend(story_result["issues"])
        minimum = int(cfg().get("story", {}).get("minimum_quality_score", 76))
        if story_result["score"] < minimum:
            problems.append(f"story quality score {story_result['score']} is below {minimum}")
        script["story_quality"] = story_result
    return list(dict.fromkeys(problems))


def _hook_text(narration):
    """Make a short overlay from words already spoken in the cold open."""
    import re
    stop = {"a", "an", "the", "and", "for", "from", "with", "what", "when", "where", "why", "how",
            "was", "were", "that", "this", "into", "over", "after", "before", "did", "does"}
    tokens = re.findall(r"[A-Za-z0-9'-]+", str(narration))
    meaningful = [word for word in tokens if word.lower() not in stop]
    chosen = meaningful[:5] if len(meaningful) >= 3 else tokens[:5]
    return " ".join(chosen).upper() if len(chosen) >= 3 else ""


def _fallback_terms(text, limit=4):
    import re
    stop = {"the", "and", "for", "from", "with", "what", "when", "where", "why", "how", "was", "were",
            "that", "this", "into", "over", "after", "before", "story", "about"}
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", str(text)) if w.lower() not in stop]
    return words[:limit]


def _fact_check_note(record):
    record = record if isinstance(record, dict) else {}
    if record.get("status") == "search_linked":
        return ("search-assisted; claim/source links are recorded in the production manifest, "
                "but still need editorial review.")
    return "not search-verified for this run; factual claims need editorial review."


def make_metadata(topic, scenes_with_times, sources, credits, fact_check=None, story_blueprint=None):
    chapters = "\n".join(f"{t} {s['title']}" for t, s in scenes_with_times)
    outline = "; ".join(s["title"] for _, s in scenes_with_times)
    story_context = ""
    if story_blueprint:
        story_context = (f"Supported title promise: {story_blueprint.get('title_promise', '')}\n"
                         f"Central question: {story_blueprint.get('central_question', '')}\n"
                         f"Opening hook: {story_blueprint.get('scenes', [{}])[0].get('narration', '')}\n")
    prompt = f"""Create honest YouTube packaging for this researched documentary.
Topic: {topic['topic']} | Angle: {topic['angle']} | Chapters: {outline}
{story_context}
The title and at least one thumbnail text must match the supported title promise and opening hook.
1) Write 10 truthful title candidates (max 65 chars). Claims must be supported by the topic and chapter facts.
2) Give 3 distinct thumbnail texts (2-4 words), each traceable to the topic, angle, or chapter names. No invented claim.
3) Write a description: 2 punchy opening lines, then 2 short accurate paragraphs. Do NOT include chapters or sources.
4) Give 12 relevant tags.
Return JSON: {{"titles":[{{"title":"","curiosity":0,"clarity":0,"honesty":0}}],"thumb_texts":["","",""],"description":"","tags":[""]}}"""
    try:
        m = gemini(prompt)
    except Exception as e:
        print("metadata generation failed; using factual fallback:", e)
        title = str(topic.get("topic") or "Documentary")[:65]
        title_b = (f"The story of {topic.get('topic', 'this subject')}")[:65]
        angle = str(topic.get("angle") or "")
        m = {
            "titles": [{"title": title, "curiosity": 5, "clarity": 8, "honesty": 10},
                       {"title": title_b, "curiosity": 6, "clarity": 8, "honesty": 10}],
            "thumb_texts": [" ".join(_fallback_terms(topic.get("topic"), 3)),
                            " ".join(_fallback_terms(angle, 3)),
                            " ".join(_fallback_terms(scenes_with_times[0][1].get("title", ""), 3))
                            if scenes_with_times else title[:24]],
            "description": (f"This documentary examines {topic.get('topic', 'the subject')} and the question "
                            f"{angle}. It follows the evidence and separates established facts from uncertainty."),
            "tags": [str(topic.get("category", "documentary")), "documentary", "history", "research"],
        }
    titles = sorted(m.get("titles", []),
                    key=lambda t: t.get("curiosity", 0) + t.get("clarity", 0) + 2 * t.get("honesty", 0),
                    reverse=True)
    if not titles:
        titles = [{"title": str(topic.get("topic") or "Documentary"), "honesty": 10}]
    src = "\n".join("- " + s for s in sources[:8])
    desc = (f"{m.get('description', topic.get('topic', 'Documentary'))}\n\nCHAPTERS\n{chapters}\n\nSOURCES\n{src}\n\n"
            f"FOOTAGE: {credits}\nNarration is AI-generated; {_fact_check_note(fact_check)}")
    anchors = set(w.lower() for w in _fallback_terms(
        topic.get("topic", "") + " " + topic.get("angle", "") + " " + outline, 80))
    fallback_texts = [" ".join(_fallback_terms(topic.get("topic"), 3)),
                      " ".join(_fallback_terms(topic.get("angle"), 3)),
                      " ".join(_fallback_terms(outline, 3))]
    thumb_texts = []
    generated = m.get("thumb_texts") or []
    stop = {"the", "and", "for", "from", "with", "what", "when", "where", "why", "how", "was", "were",
            "that", "this", "into", "over", "after", "before"}
    import re
    for i in range(3):
        text = " ".join(str(generated[i] if i < len(generated) else "").upper().split())
        words = re.findall(r"[A-Z0-9'-]+", text)
        claims = {w.lower() for w in words if w.lower() not in stop}
        if not (2 <= len(words) <= 4 and claims and claims <= anchors):
            text = fallback_texts[i] or str(topic.get("topic", "DOCUMENTARY"))[:24]
        thumb_texts.append(text.upper())
    packaged = {
        "title": str(titles[0].get("title") or topic.get("topic", "Documentary"))[:100],
        "title_b": str((titles[1] if len(titles) > 1 else titles[0]).get("title") or topic.get("topic", "Documentary"))[:100],
        "thumb_texts": thumb_texts,
        "description": desc[:4900],
        "tags": m.get("tags", ["documentary", "research"])[:15],
    }
    if story_blueprint:
        from .story import validate_packaging
        if validate_packaging(story_blueprint, packaged):
            # Safe, literal fallback uses the script's own evidence-bounded promise.
            promise_words = _fallback_terms(story_blueprint.get("title_promise", ""), 4)
            packaged["title"] = str(story_blueprint.get("title_promise", topic.get("topic", "Documentary")))[:100]
            promise_text = " ".join(promise_words[:3]).upper()
            packaged["thumb_texts"][0] = promise_text or str(topic.get("topic", "DOCUMENTARY"))[:24].upper()
            if validate_packaging(story_blueprint, packaged):
                raise ValueError("generated title/thumbnail cannot be aligned to the story promise")
    return packaged

def beats(scenes, durs, plans):
    """Split each scene's narration across its shots, in proportion to shot length -> text spoken during each shot."""
    out = []
    for s, plan in zip(scenes, plans):
        words = s["narration"].split()
        total, cum, idx, row = (sum(plan) or 1), 0.0, 0, []
        for k, l in enumerate(plan):
            cum += l
            end = len(words) if k == len(plan) - 1 else round(len(words) * cum / total)
            row.append(" ".join(words[idx:end]))
            idx = end
        out.append(row)
    return out


def shot_queries(topic, script, scenes, beats_):
    """One literal stock-footage query per shot, written from what the narrator says in that shot."""
    flat = [(si, t) for si, bs in enumerate(beats_) for t in bs]
    found = {}
    setting = script.get("setting", "")
    for start in range(0, len(flat), 40):
        chunk = flat[start:start + 40]
        beat_lines = []
        for k, (si, text) in enumerate(chunk):
            scene = scenes[si]
            plan = scene.get("visual_plan", {})
            keywords = plan.get("footage_keywords", []) if isinstance(plan, dict) else []
            visual_hint = (f"visual={plan.get('description', '')}; camera={plan.get('camera_movement', '')}; "
                           f"keywords={', '.join(keywords[:4])}" if isinstance(plan, dict) else "")
            beat_lines.append(f"{start + k}. [{scene.get('title', '')}] narration={text[:240]}; {visual_hint}")
        lines = "\n".join(beat_lines)
        prompt = f"""You choose stock-footage search queries for a documentary about: {topic['topic']}.
Real setting: {setting}
Below are numbered beats: what the narrator says during one ~5 second shot. For EACH beat give:
 "q": 2-4 words describing what the viewer should SEE while hearing it - literal, concrete, searchable on stock sites,
      faithful to the beat's meaning AND the real place/era. Abstract beat -> matching atmosphere (e.g. "stormy sea waves").
 "alt": a more generic backup, 2-3 words. Never use people's names. No words like "footage" or "video".
BEATS:
{lines}
Return JSON: {{"shots":[{{"i":0,"q":"","alt":""}}]}}"""
        try:
            for it in gemini(prompt, temperature=0.4).get("shots", []):
                found[int(it["i"])] = (str(it.get("q", "")).strip(), str(it.get("alt", "")).strip())
        except Exception as e:
            print("shot_queries chunk failed, using scene visuals:", e)
    out, n = [], 0
    for si, bs in enumerate(beats_):
        vis = scenes[si].get("visuals") or ["dark cinematic landscape"]
        row = []
        for bi in range(len(bs)):
            q, alt = found.get(n, ("", ""))
            row.append((q or vis[bi % len(vis)], alt or vis[(bi + 1) % len(vis)]))
            n += 1
        out.append(row)
    return out


def translate_meta(title, intro, languages):
    """Translate title + opening description into several languages (YouTube 'localizations')."""
    prompt = f"""Translate this YouTube title and description opening into each language code below.
Natural, idiomatic, same curiosity and tone; keep the title under 90 characters; do not add facts.
Language codes: {", ".join(languages)}
TITLE: {title}
DESCRIPTION: {intro}
Return JSON: {{"translations":{{"<code>":{{"title":"","description":""}}}}}}"""
    out = gemini(prompt, temperature=0.3).get("translations", {})
    return {k: v for k, v in out.items() if k in languages and v.get("title") and v.get("description")}
