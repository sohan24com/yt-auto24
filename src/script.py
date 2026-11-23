import re
from .common import cfg, gemini, load_insights

BANNED_CLICHES = (
    "in a world",
    "little did they know",
    "buckle up",
    "let's dive in",
    "lets dive in",
)


def words(scenes):
    return sum(len((s or {}).get("narration", "").split()) for s in (scenes or []))


def _clean_visuals(raw_visuals, fallback_topic):
    cleaned = []
    seen = set()
    for item in raw_visuals or []:
        q = re.sub(r"[^A-Za-z0-9\s-]", " ", str(item or ""))
        q = re.sub(r"\s+", " ", q).strip()
        words_q = q.split()
        if len(words_q) > 4:
            q = " ".join(words_q[:4])
        key = q.lower()
        if q and len(q.split()) >= 2 and key not in seen:
            seen.add(key)
            cleaned.append(q)
        if len(cleaned) >= 4:
            break

    base_fallbacks = [
        f"{fallback_topic} wide shot",
        "historical archive map",
        "night sky stars",
        "dramatic landscape view",
    ]
    for fb in base_fallbacks:
        if len(cleaned) >= 4:
            break
        q = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9\s-]", " ", fb)).strip()
        q = " ".join(q.split()[:4])
        if q and q.lower() not in seen:
            seen.add(q.lower())
            cleaned.append(q)
    return cleaned[:4]


def _normalize_script(data, topic):
    if not isinstance(data, dict):
        return data
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        return data
    topic_label = str((topic or {}).get("topic") or "documentary").strip()
    norm_scenes = []
    for idx, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            continue
        title = re.sub(r"\s+", " ", str(sc.get("title") or f"Part {idx + 1}")).strip()
        narration = re.sub(r"\s+", " ", str(sc.get("narration") or "")).strip()
        visuals = _clean_visuals(sc.get("visuals"), topic_label)
        norm_scenes.append({
            "title": title,
            "narration": narration,
            "visuals": visuals,
        })
    data["scenes"] = norm_scenes
    if not isinstance(data.get("sources"), list):
        data["sources"] = []
    return data


def write_script(topic):
    c = cfg()
    v = c["video"]
    lessons = "\n".join("- " + str(l) for l in (load_insights() or {}).get("lessons", []))
    prompt = f"""Write the narration for a #1 retention documentary video of 8-10 minutes.
Channel tone: {c['channel']['tone']}.
Topic: {topic['topic']}
Angle: {topic['angle']}

STRUCTURE
- Scene 1 is the HOOK (max 85 words): open in the VERY FIRST sentence with a startling, TRUE,
  specific fact, paradox, or sharp question. No greeting, no "welcome", no channel intro.
  State clearly what mystery or truth will be uncovered by the end.
- Then 10-12 more scenes. Every second scene must end with a clear OPEN LOOP (a specific question or
  tension that is answered later in the video). Every 2-3 scenes should also deliver a concrete insight
  or micro-payoff so mid-video retention stays high.
- Final scene: deliver the main payoff, close every open loop, end with a memorable thought-provoking
  line, and add one short line inviting viewers to subscribe for the next story.
STYLE
- Written to be spoken aloud naturally via TTS: concrete details (dates, places, numbers), varied
  sentence lengths (short punchy lines mixed with clear explanations), plain everyday words.
- Avoid repeating the same point across adjacent scenes; each scene must advance the story.
- Banned cliches: "in a world", "little did they know", "buckle up", "let's dive in".
- Total narration {v['words_min']}-{v['words_max']} words. Spoken text only (no stage directions, no markdown).
HONESTY
- Strictly separate documented facts from theories ("historians believe", "according to ...", "researchers suggest").
- Never invent quotes, statistics, names, or sources.
VISUALS
- For each scene give 4 stock-footage search queries: concrete visual nouns/scenes, 2-4 words each,
  landscape-friendly and easy to find on Pixabay, Wikimedia Commons, or NASA
  (e.g. "ancient stone ruins", "foggy forest aerial", "old handwritten map", "earth orbit night").
  Vary angles/subjects across the 4 queries and include no people's names.
Lessons from our analytics (apply them to hook, pacing, and framing):
{lessons or '- none yet'}
Return JSON: {{"scenes":[{{"title":"short chapter title","narration":"","visuals":["","","",""]}}],"sources":["real reference works/institutions"]}}"""
    raw = gemini(prompt, temperature=0.85)
    return _normalize_script(raw, topic)


def fact_check(script):
    prompt = f"""You are a strict documentary fact-checker. Use Google Search to verify every factual claim
(dates, names, numbers, causes) in this script. Fix errors. Remove or hedge anything you cannot verify
so documented facts and theories/speculation stay clearly separated.
Keep the same structure, tone, and length (within 10%). Keep every scene's "title" and "visuals" intact
unless a title is factually wrong.
Return ONLY JSON: {{"scenes":[...same format...],"sources":["urls or publication names actually used"],"changes":["what you changed"]}}

SCRIPT:
{script}"""
    try:
        out = gemini(prompt, grounding=True, temperature=0.2)
        if isinstance(out, dict) and out.get("scenes") and len(out["scenes"]) >= 8:
            orig_scenes = script.get("scenes", []) if isinstance(script, dict) else []
            for i, sc in enumerate(out["scenes"]):
                if isinstance(sc, dict) and not sc.get("visuals") and i < len(orig_scenes):
                    sc["visuals"] = orig_scenes[i].get("visuals", [])
            return _normalize_script(out, {"topic": ""})
    except Exception as e:
        print("fact_check failed, keeping original:", e)
    return script


def valid(script):
    v = cfg()["video"]
    sc = (script or {}).get("scenes", [])
    w = words(sc) if sc else 0
    problems = []
    if len(sc) < 8:
        problems.append("too few scenes")
    if not (v["words_min"] * 0.9 <= w <= v["words_max"] * 1.1):
        problems.append(f"word count {w}")
    if sc and len(sc[0].get("narration", "").split()) > 120:
        problems.append("hook too long")
    full_text = " ".join(s.get("narration", "").lower() for s in sc)
    if any(cliche in full_text for cliche in BANNED_CLICHES):
        problems.append("banned cliche")
    if sc and any(not s.get("narration", "").strip() for s in sc):
        problems.append("empty narration")
    return problems


def make_metadata(topic, scenes_with_times, sources, credits):
    chapters = "\n".join(f"{t} {s['title']}" for t, s in scenes_with_times)
    outline = "; ".join(s["title"] for _, s in scenes_with_times)
    prompt = f"""Create YouTube packaging for a documentary video.
Topic: {topic['topic']} | Angle: {topic['angle']} | Chapters: {outline}
1) Write 10 title candidates (max 65 chars, curiosity + clarity, honest: the video must deliver on it).
   Score each 1-10 for curiosity, clarity, honesty. Mix styles: question, bold claim, number, mystery.
2) Give 3 thumbnail texts (max 4 words each, ALL CAPS, different angles).
3) Write a description: 2 punchy opening lines (no greeting), then 2 short paragraphs summarising the story
   without spoiling the payoff. Do NOT include chapters or sources, I add them.
4) 12 tags.
Return JSON: {{"titles":[{{"title":"","curiosity":0,"clarity":0,"honesty":0}}],"thumb_texts":["","",""],"description":"","tags":[""]}}"""
    m = gemini(prompt) or {}
    raw_titles = m.get("titles") or []
    if raw_titles:
        titles = sorted(
            [t for t in raw_titles if isinstance(t, dict)],
            key=lambda t: float(t.get("curiosity", 0)) + float(t.get("clarity", 0)) + 2 * float(t.get("honesty", 0)),
            reverse=True,
        )
        t_primary = str((titles[0] if titles else {}).get("title") or topic["topic"])[:100]
        t_secondary = str((titles[1] if len(titles) > 1 else (titles[0] if titles else {})).get("title") or t_primary)[:100]
    else:
        t_primary = t_secondary = str(topic["topic"])[:100]

    thumbs = [str(x).strip() for x in (m.get("thumb_texts") or []) if str(x).strip()]
    if not thumbs:
        thumbs = [str(topic["topic"]).upper()[:28]]
    thumbs = (thumbs + thumbs)[:3]

    desc_body = str(m.get("description") or "").strip()
    src = "\n".join("- " + str(s) for s in (sources or [])[:8])
    desc = (
        f"{desc_body}\n\nCHAPTERS\n{chapters}\n\nSOURCES\n{src}\n\n"
        f"FOOTAGE: {credits}\nNarration is AI-generated; research assisted by AI and fact-checked against web sources."
    )
    tags = [str(x).strip() for x in (m.get("tags") or []) if str(x).strip()][:15]

    return {
        "title": t_primary,
        "title_b": t_secondary,
        "thumb_texts": thumbs,
        "description": desc,
        "tags": tags,
    }
