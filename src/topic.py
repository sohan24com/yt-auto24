import random
from .common import cfg, gemini, load_videos, load_insights


def pick_category():
    mix = load_insights().get("weights") or cfg()["category_mix"]
    cats = list(mix)
    return random.choices(cats, [mix[c] for c in cats])[0]


def choose_topic():
    c = cfg()
    cat = pick_category()
    used = [v["topic"] for v in load_videos()][-80:]
    lessons = "\n".join("- " + l for l in load_insights().get("lessons", []))
    prompt = f"""You are head of research for a documentary YouTube channel: {c['channel']['niche']}.
Category for this video: {cat}.
Propose 8 topic ideas for an 8-10 minute documentary. For each give:
topic, angle (the single surprising question the video answers), search_demand (1-10),
uniqueness (1-10), evidence_depth (1-10: enough well-documented, reliable material for 9 minutes).
Rules: real and well documented; no conspiracy theories presented as fact; no private living
individuals; no exploiting recent tragedies. Do NOT repeat these used topics: {used}
Lessons learned from our analytics (apply them):
{lessons or '- none yet'}
Return JSON: {{"ideas":[{{"topic":"","angle":"","search_demand":0,"uniqueness":0,"evidence_depth":0}}]}}"""
    ideas = gemini(prompt)["ideas"]
    best = max(ideas, key=lambda i: i.get("search_demand", 5) + 1.2 * i.get("uniqueness", 5)
               + 1.5 * i.get("evidence_depth", 5))
    best["category"] = cat
    return best
