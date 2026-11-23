import random
from .common import cfg, gemini, load_videos, load_insights


def pick_category():
    insights = load_insights() or {}
    weights = insights.get("weights") or {}
    mix = weights if isinstance(weights, dict) and weights else (cfg().get("category_mix") or {})
    cats = [c for c, w in mix.items() if (w or 0) > 0]
    if not cats:
        return "history"
    return random.choices(cats, [float(mix[c]) for c in cats])[0]


def _normalize_key(text):
    return " ".join("".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in str(text or "")).split())


def _is_duplicate(candidate_topic, used_topics):
    cand_norm = _normalize_key(candidate_topic)
    if not cand_norm:
        return True
    cand_tokens = set(cand_norm.split())
    for u in used_topics:
        u_norm = _normalize_key(u)
        if not u_norm:
            continue
        if cand_norm == u_norm or cand_norm in u_norm or u_norm in cand_norm:
            return True
        u_tokens = set(u_norm.split())
        if cand_tokens and u_tokens:
            overlap = len(cand_tokens & u_tokens) / max(1, min(len(cand_tokens), len(u_tokens)))
            if overlap >= 0.75:
                return True
    return False


def choose_topic():
    c = cfg()
    cat = pick_category()
    history = load_videos() or []
    used = [str(v.get("topic", "")) for v in history if v.get("topic")][-120:]
    recent_cats = [str(v.get("category", "")) for v in history[-6:] if v.get("category")]
    lessons = "\n".join("- " + str(l) for l in ((load_insights() or {}).get("lessons") or []))

    cat_hint = ""
    if recent_cats and recent_cats.count(cat) >= 3:
        cat_hint = f"Note: '{cat}' appeared recently; make sure this idea offers a distinctly fresh sub-subject.\n"

    prompt = f"""You are head of research for a documentary YouTube channel: {c['channel']['niche']}.
Category for this video: {cat}.
{cat_hint}Propose 8 topic ideas for an 8-10 minute documentary. For each give:
topic, angle (the single surprising question or paradox the video answers), search_demand (1-10),
uniqueness (1-10), evidence_depth (1-10: enough well-documented, reliable material for 9 minutes).
Rules: real and well documented; no conspiracy theories presented as fact; no private living
individuals; no exploiting recent tragedies. Do NOT repeat or closely paraphrase these used topics: {used}
Lessons learned from our analytics (apply them):
{lessons or '- none yet'}
Return JSON: {{"ideas":[{{"topic":"","angle":"","search_demand":0,"uniqueness":0,"evidence_depth":0}}]}}"""

    data = gemini(prompt, temperature=0.8) or {}
    ideas = [i for i in (data.get("ideas") or []) if isinstance(i, dict) and str(i.get("topic") or "").strip()]
    if not ideas:
        raise RuntimeError("no topic ideas returned")

    fresh_ideas = [i for i in ideas if not _is_duplicate(i.get("topic"), used)]
    pool = fresh_ideas if fresh_ideas else ideas

    def _score(item):
        sd = float(item.get("search_demand", 5) or 5)
        uq = float(item.get("uniqueness", 5) or 5)
        ed = float(item.get("evidence_depth", 5) or 5)
        dup_penalty = -10.0 if _is_duplicate(item.get("topic"), used) else 0.0
        return sd + 1.2 * uq + 1.5 * ed + dup_penalty

    best = dict(max(pool, key=_score))
    best["topic"] = " ".join(str(best.get("topic", "")).split()).strip()
    best["angle"] = " ".join(str(best.get("angle", "")).split()).strip()
    best["category"] = cat
    return best
