"""Deterministic editorial decisions layered over the fact-checked script and AI shot brief."""
import re


MOVEMENTS = ("push_in", "pull_out", "drift_left", "drift_right", "hold")
FRAMINGS = ("wide", "medium", "detail", "graphic", "archival")


def scene_role(index, count):
    """Map scene position to a documentary arc without inventing story facts."""
    if index == 0:
        return "hook"
    if index == 1:
        return "setup"
    if index == count - 1:
        return "resolution"
    if index == count - 2:
        return "consequence"
    if index >= max(2, count - 4):
        return "reveal"
    return "investigation"


def review_story(scenes):
    """Return concrete editorial warnings for the script review pass."""
    warnings = []
    if len(scenes) < 8:
        warnings.append("story arc has fewer than eight scenes")
    titles = [re.sub(r"[^a-z0-9]+", " ", str(s.get("title", "")).lower()).strip()
              for s in scenes]
    repeated = sorted({title for title in titles if title and titles.count(title) > 1})
    if repeated:
        warnings.append("repeated scene titles: " + ", ".join(repeated[:3]))
    emotions = [re.sub(r"[^a-z]+", "", str(s.get("emotion", "")).lower()) for s in scenes]
    if len({e for e in emotions if e}) < 3:
        warnings.append("emotional arc uses fewer than three distinct voice directions")
    if scenes:
        final_text = str(scenes[-1].get("narration", "")).lower()
        if not any(term in final_text for term in ("answer", "evidence", "know", "shows", "remains", "so", "but")):
            warnings.append("ending may need a clearer evidence-based payoff")
    return warnings


def _urgency(scene):
    text = str(scene.get("narration", "")).lower()
    emotion = str(scene.get("emotion", "")).lower()
    markers = ("urgent", "danger", "tense", "panic", "urgent", "frantic", "chase")
    questions = text.count("?") + text.count("but ") + text.count("yet ")
    return sum(term in emotion for term in markers) + min(questions, 2)


def plan_shots(duration, scene, scene_index=0, scene_count=10, cold_open_seconds=0.0):
    """Choose varied, reproducible shot lengths from story energy and scene duration."""
    duration = max(0.0, float(duration))
    if duration <= 0.05:
        return []
    role = str(scene.get("role") or scene_role(scene_index, max(1, int(scene_count))))
    emotion = str(scene.get("emotion", "")).lower()
    words = str(scene.get("narration", "")).split()
    density = len(words) / max(duration, 0.1)
    urgency = _urgency(scene)
    if scene_index == 0 and cold_open_seconds > 0:
        targets = (2.2, 2.8, 3.4, 4.6)
        fast = min(float(cold_open_seconds), duration)
    elif urgency >= 2 or density > 2.8:
        targets, fast = (3.2, 3.8, 4.3, 3.5), 0.0
    elif any(k in emotion for k in ("somber", "reflect", "warm", "quiet", "tender")):
        targets, fast = (5.4, 6.0, 5.0, 6.2), 0.0
    elif role == "reveal" or any(k in emotion for k in ("wonder", "awe", "discovery")):
        targets, fast = (4.2, 5.0, 4.6, 5.4), 0.0
    else:
        targets, fast = (4.4, 5.0, 4.1, 5.3), 0.0

    shots, remaining, index = [], duration, 0
    while remaining > 0.05:
        target = targets[index % len(targets)]
        if fast > 0.05:
            target = min(targets[index % 3], 2.8, fast)
        length = min(target, remaining)
        if 0 < remaining - length < 2.4 and length + remaining - length <= 6.6:
            length = remaining
        if length < 2.4 and shots:
            shots[-1] = round(shots[-1] + length, 3)
            break
        shots.append(round(length, 3))
        remaining -= length
        fast = max(0.0, fast - length)
        index += 1
    # Preserve exact scene duration after rounding.
    if shots:
        shots[-1] = round(shots[-1] + duration - sum(shots), 3)
    return shots


def shot_decision(scene, beat, ai_brief=None, shot_index=0, previous_move=None):
    """Constrain AI suggestions to a small visual grammar and avoid repetitive motion."""
    brief = ai_brief if isinstance(ai_brief, dict) else {}
    query = str(brief.get("q", "") or "").lower()
    beat = str(beat or "").lower()
    text = query + " " + beat
    framing = str(brief.get("framing", "")).lower()
    if framing not in FRAMINGS:
        framing = ("graphic" if any(k in text for k in ("map", "timeline", "diagram", "chart"))
                   else "detail" if any(k in text for k in ("hand", "object", "letter", "artifact", "gear"))
                   else "wide" if any(k in text for k in ("landscape", "city", "coast", "mountain", "sky"))
                   else "medium")
    requested = str(brief.get("movement", "")).lower()
    if requested not in MOVEMENTS:
        role = str(scene.get("role", ""))
        emotion = str(scene.get("emotion", "")).lower()
        if role in ("hook", "reveal"):
            requested = "push_in"
        elif any(k in emotion for k in ("somber", "reflect", "calm", "hushed")):
            requested = "hold" if shot_index % 2 == 0 else "drift_left"
        else:
            requested = ("drift_left", "push_in", "drift_right", "pull_out")[shot_index % 4]
    if requested == previous_move and requested != "hold":
        requested = MOVEMENTS[(MOVEMENTS.index(requested) + 1) % (len(MOVEMENTS) - 1)]
    transition = str(brief.get("transition", "cut")).lower()
    if transition not in ("cut", "dissolve"):
        transition = "cut"
    # Factual B-roll generally reads best on a clean cut. Dissolves are reserved for
    # a reflective change of time/place so effects do not become the point of the edit.
    if transition == "dissolve" and not any(k in str(scene.get("emotion", "")).lower()
                                               for k in ("reflect", "somber", "quiet", "memory")):
        transition = "cut"
    return {"framing": framing, "movement": requested, "transition": transition}


def sound_cues(scenes, starts, maximum=3):
    """Reserve synthetic whooshes for meaningful act/emotion shifts, not every scene cut."""
    if len(scenes) < 2 or len(starts) != len(scenes):
        return []
    candidates = []
    for i in range(1, len(scenes)):
        previous = str(scenes[i - 1].get("emotion", "")).lower()
        current = str(scenes[i].get("emotion", "")).lower()
        role = str(scenes[i].get("role", ""))
        score = int(role in ("reveal", "consequence", "resolution"))
        score += int(previous != current and bool(previous) and bool(current))
        score += int(any(k in current for k in ("urgent", "wonder", "reveal", "triumph")))
        if score >= 2:
            candidates.append((score, i, float(starts[i])))
    selected = []
    for _, index, moment in sorted(candidates, key=lambda row: (-row[0], row[1])):
        if all(abs(moment - cue) > 20 for cue in selected):
            selected.append(moment)
        if len(selected) >= max(0, int(maximum)):
            break
    return sorted(selected)
