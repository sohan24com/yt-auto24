import random
from .common import cfg, gemini, load_videos, load_insights
from . import open_learning as OL


def pick_category():
    mix = load_insights().get("weights") or cfg()["category_mix"]
    cats = list(mix)
    return random.choices(cats, [mix[c] for c in cats])[0]


def choose_topic():
    c = cfg()
    cat = pick_category()
    used = [v["topic"] for v in load_videos()][-80:]
    insights = load_insights()
    lessons = "\n".join("- " + l for l in insights.get("lessons", []))
    library = OL.refresh_skill_library()
    skills = OL.prompt_skills(library)
    skill_notes = "\n".join(
        f"- [{item['status']}; confidence {item['confidence']:.2f}; source license {item['license']}] "
        f"{item['principle']} Source: {item['source_title']} ({item['source_url']})."
        for item in skills if item.get("principle") and item.get("source_url")
    )
    prompt = f"""You are head of research for a documentary YouTube channel: {c['channel']['niche']}.
Category for this video: {cat}.
Propose 8 topic ideas for an 8-10 minute documentary. For each give:
topic, angle (the single surprising question the video answers), and a short evidence plan naming
primary records, open research, or reliable institutions to check.
Rules: real and well documented; no conspiracy theories presented as fact; no private living
individuals; no exploiting recent tragedies. Do NOT repeat these used topics: {used}
Do not invent search-volume, popularity, competition, audience-demand, or performance scores. No such
global demand measurements are connected to this workflow.
Lessons learned from our analytics (apply them):
{lessons or '- none yet'}
Open educational research skills (candidate hypotheses; apply only when relevant, do not quote source text):
{skill_notes or '- none available; do not invent learned skills'}
Do not use YouTube cross-channel performance data, creator scraping, copied scripts, thumbnails, or footage.
Return JSON: {{"ideas":[{{"topic":"","angle":"","evidence_plan":""}}]}}"""
    ideas = gemini(prompt)["ideas"]
    if not isinstance(ideas, list) or not ideas or not isinstance(ideas[0], dict):
        raise RuntimeError("topic provider returned no usable documentary ideas")
    best = ideas[0]
    best["category"] = cat
    best["learning_skills"] = skills
    try:
        best["open_research_sources"] = OL.discover_topic_sources(best.get("topic", ""))
    except Exception as exc:
        # Research-provider outages must not turn into invented sources.
        print("open research discovery unavailable:", type(exc).__name__)
        best["open_research_sources"] = []
    return best
