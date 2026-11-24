from .common import cfg, gemini, load_insights


def words(scenes):
    return sum(len(s["narration"].split()) for s in scenes)


def write_script(topic):
    c = cfg()
    v = c["video"]
    lessons = "\n".join("- " + l for l in load_insights().get("lessons", []))
    prompt = f"""Write the narration for a documentary video of 8-10 minutes.
Channel tone: {c['channel']['tone']}.
Topic: {topic['topic']}
Angle: {topic['angle']}

STRUCTURE
- Scene 1 starts immediately with a truthful, source-checkable hook that can be spoken in 5 seconds (max 12 words).
  The FIRST sentence must use a concrete, story-specific fact or question; no invented numbers, greetings, welcome,
  channel introduction, or throat-clearing. Leave a real unresolved question for the story to answer.
  Give "hook_text": 3-5 short ALL-CAPS words that accurately summarize this hook.
- Follow with 10-12 scenes that build a clear cause-and-effect story; use an open loop only when the next scene answers it.
- Final scene delivers the researched payoff, then a natural outro thanking viewers, inviting Like/Comment/Subscribe,
  and pointing to the next related documentary without inventing a specific video title or link.
DELIVERY
- For every scene give "emotion": a short voice-acting direction that matches what happens in it, e.g.
  "hushed, suspenseful", "somber, slow", "awed, rising", "urgent, tense", "calm, matter-of-fact", "warm, reflective".
  Vary it across the video; mysterious moments are hushed, tragedies are somber, discoveries are awed.
STYLE
- Concrete details (dates, places, numbers), short sentences mixed with long ones, plain words.
- Banned cliches: "in a world", "little did they know", "buckle up", "let's dive in".
- Total narration {v['words_min']}-{v['words_max']} words. Spoken text only (no stage directions, no markdown).
HONESTY
- Separate documented facts from theories ("historians believe", "according to ...").
- Never invent quotes, statistics, names or sources.
VISUALS
- For each scene give 4 stock-footage search queries: concrete visual nouns, 2-4 words, generic enough to
  exist on Pexels (e.g. "ancient stone ruins", "foggy forest drone", "old handwritten manuscript"). No people's names.
- Match the REAL place and era of the story: put the region in the query (e.g. "Scotland stormy sea", "Atlantic lighthouse
  storm"). Prefer moody, overcast, dark footage. Avoid sunny tropical or Mediterranean-looking shots unless the story is set there.
Lessons from our analytics (apply them):
{lessons or '- none yet'}
Return JSON: {{"setting":"place + era keywords for stock searches, e.g. 'Scotland island lighthouse 1900 storm'",
"wiki_terms":["3-5 specific real places/objects/events that have photos on Wikimedia Commons, e.g. 'Flannan Isles Lighthouse'"],
"hook_text":"WHERE ARE THEY?",
"scenes":[{{"title":"short chapter title","emotion":"hushed, suspenseful","narration":"","visuals":["","","",""]}}],"sources":["real reference works/institutions"]}}"""
    return gemini(prompt, temperature=0.85)


def fact_check(script):
    prompt = f"""You are a strict documentary fact-checker. Use Google Search to verify every factual claim
(dates, names, numbers, causes) in this script. Fix errors. Remove or hedge anything you cannot verify.
Keep the same structure, tone and length (within 10%). Keep every scene's "visuals" unchanged.
Return ONLY JSON: {{"scenes":[...same format...],"sources":["urls or publication names actually used"],"changes":["what you changed"]}}

SCRIPT:
{script}"""
    try:
        out = gemini(prompt, grounding=True, temperature=0.2)
        if out.get("scenes") and len(out["scenes"]) >= 8:
            return out
    except Exception as e:
        print("fact_check failed, keeping original:", e)
    return script


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
        problems.append("hook_text must contain 3-5 words")
    if not sc or len(sc[-1].get("narration", "").split()) < 20:
        problems.append("missing documentary outro/payoff")
    return problems


def _fallback_terms(text, limit=4):
    import re
    stop = {"the", "and", "for", "from", "with", "what", "when", "where", "why", "how", "was", "were",
            "that", "this", "into", "over", "after", "before", "story", "about"}
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", str(text)) if w.lower() not in stop]
    return words[:limit]


def make_metadata(topic, scenes_with_times, sources, credits):
    chapters = "\n".join(f"{t} {s['title']}" for t, s in scenes_with_times)
    outline = "; ".join(s["title"] for _, s in scenes_with_times)
    prompt = f"""Create honest YouTube packaging for this researched documentary.
Topic: {topic['topic']} | Angle: {topic['angle']} | Chapters: {outline}
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
            f"FOOTAGE: {credits}\nNarration is AI-generated; research assisted by AI and fact-checked against web sources.")
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
    return {
        "title": str(titles[0].get("title") or topic.get("topic", "Documentary"))[:100],
        "title_b": str((titles[1] if len(titles) > 1 else titles[0]).get("title") or topic.get("topic", "Documentary"))[:100],
        "thumb_texts": thumb_texts,
        "description": desc[:4900],
        "tags": m.get("tags", ["documentary", "research"])[:15],
    }

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
        lines = "\n".join(f"{start + k}. [{scenes[si]['title']}] {t[:240]}" for k, (si, t) in enumerate(chunk))
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
