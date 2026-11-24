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
- Scene 1 is the HOOK (max 90 words): open with a startling, TRUE, specific fact or question. No greeting,
  no "welcome", no channel intro. Promise what the viewer will learn by the end.
- Then 10-12 more scenes. Every second scene must end with an OPEN LOOP (a question answered later).
- Final scene: the payoff, a thought-provoking close, one short line inviting viewers to subscribe for the next story.
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
Lessons from our analytics (apply them):
{lessons or '- none yet'}
Return JSON: {{"scenes":[{{"title":"short chapter title","narration":"","visuals":["","","",""]}}],"sources":["real reference works/institutions"]}}"""
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
    if sc and len(sc[0]["narration"].split()) > 120:
        problems.append("hook too long")
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
    m = gemini(prompt)
    titles = sorted(m["titles"], key=lambda t: t["curiosity"] + t["clarity"] + 2 * t["honesty"], reverse=True)
    src = "\n".join("- " + s for s in sources[:8])
    desc = (f"{m['description']}\n\nCHAPTERS\n{chapters}\n\nSOURCES\n{src}\n\n"
            f"FOOTAGE: {credits}\nNarration is AI-generated; research assisted by AI and fact-checked against web sources.")
    return {
        "title": titles[0]["title"][:100], "title_b": (titles[1] if len(titles) > 1 else titles[0])["title"][:100],
        "thumb_texts": (m["thumb_texts"] + m["thumb_texts"])[:3], "description": desc,
        "tags": m["tags"][:15],
    }
