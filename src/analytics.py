"""Feedback loop: snapshots at 24h/72h/7d, thumbnail+title swap on weak CTR,
category re-weighting, and AI-written lessons that feed the next topics/scripts."""
import datetime as dt
import tempfile
from pathlib import Path
from . import drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, load_insights, save_insights, gemini, notify

SNAPS = {"24h": 24, "72h": 72, "7d": 168}


def _parse_created(rec):
    raw = str(rec.get("created") or "").strip()
    parsed = dt.datetime.fromisoformat(raw)
    cur = now()
    if parsed.tzinfo is not None and cur.tzinfo is None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    elif parsed.tzinfo is None and cur.tzinfo is not None:
        parsed = parsed.replace(tzinfo=cur.tzinfo)
    return parsed


def age_h(rec):
    return (now() - _parse_created(rec)).total_seconds() / 3600


def biggest_drop(rec):
    s = now().date()
    start_date = str(rec.get("created") or "")[:10]
    if not start_date:
        return None
    curve = Y.retention_curve(rec["video_id"], start_date, s.isoformat()) or []
    if len(curve) < 10:
        return None
    worst = max(range(1, len(curve)), key=lambda i: curve[i - 1][1] - curve[i][1])
    ratio = float(curve[worst][0] or 0.0)
    dur = float(rec.get("duration") or 0.0)
    t = ratio * dur
    scenes = rec.get("scenes") or []
    matched = [sc for sc in scenes if float(sc.get("start", 0)) <= t]
    scene = matched[-1].get("title", "") if matched else ""
    return {"at_ratio": round(ratio, 2), "scene": scene}


def score(r):
    data = r or {}
    return float(data.get("views", 0) or 0) * (0.5 + float(data.get("averageViewPercentage", 0) or 0) / 100.0)


def _normalize_ctr(raw_ctr):
    if raw_ctr is None:
        return None
    ctr = float(raw_ctr)
    if ctr > 1.0:  # API may return percent instead of ratio
        ctr = ctr / 100.0
    return ctr


def main():
    c = cfg().get("optimize") or {}
    videos = [v for v in (load_videos() or []) if v.get("video_id")]
    if not videos:
        return

    st = Y.stats([v["video_id"] for v in videos]) or {}
    today = now().date().isoformat()

    for rec in videos:
        rec.setdefault("snapshots", {})
        rec.setdefault("thumb_ids", [])
        rec.setdefault("swapped", False)

        a = age_h(rec)
        start_date = str(rec.get("created") or today)[:10]
        try:
            rep = Y.video_report(rec["video_id"], start_date, today) or {}
        except Exception as e:
            print("report failed:", rec["video_id"], e)
            continue

        rep["public_views"] = (st.get(rec["video_id"]) or {}).get("viewCount", 0)
        for k, h in SNAPS.items():
            if a >= h and k not in rec["snapshots"]:
                rec["snapshots"][k] = rep
        rec["latest"] = rep

        # weak packaging -> swap thumbnail + title once, after threshold hours
        ctr = _normalize_ctr(rep.get("videoThumbnailImpressionsClickRate"))
        ctr_thresh = float(c.get("ctr_threshold", 0.04))
        swap_after = float(c.get("thumb_swap_after_hours", 48))
        weak = (ctr is not None and ctr < ctr_thresh) or (ctr is None and rep["public_views"] < 30)

        if a >= swap_after and weak and not rec.get("swapped") and len(rec["thumb_ids"]) > 1:
            try:
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "alt.jpg"
                    D.download(rec["thumb_ids"][1], p)
                    Y.set_thumbnail(rec["video_id"], p)
                if rec.get("title_b") and rec["title_b"] != rec.get("title"):
                    Y.update_title(rec["video_id"], rec["title_b"])
                rec["swapped"] = True
                ctr_label = f"{ctr:.2%}" if ctr is not None else "n/a"
                notify(f"Swapped thumbnail/title for {rec['video_id']} (CTR {ctr_label})")
            except Exception as e:
                print("swap failed:", e)

        if "7d" in rec["snapshots"] and "drop" not in rec:
            try:
                rec["drop"] = biggest_drop(rec)
            except Exception as e:
                print("retention drop failed:", e)

        save_video(rec)

    # ---- learn: category weights + lessons ----
    done = [v for v in videos if "7d" in (v.get("snapshots") or {}) and v.get("category")]
    ins = load_insights() or {}
    prior = cfg().get("category_mix") or {}

    if len(done) >= 3 and prior:
        by = {}
        for v in done:
            by.setdefault(v["category"], []).append(score((v.get("snapshots") or {}).get("7d")))
        avg = {k: sum(x) / len(x) for k, x in by.items() if x}
        tot = sum(avg.values()) or 1.0
        w = {k: 0.5 * float(prior.get(k, 0.1)) + 0.5 * float(avg.get(k, 0.0)) / tot for k in prior}
        w = {k: max(x, 0.08) for k, x in w.items()}  # keep exploring every category
        s = sum(w.values()) or 1.0
        ins["weights"] = {k: round(x / s, 3) for k, x in w.items()}

    if len(done) >= 4:
        rows = "\n".join(
            f"- [{v['category']}] \"{v.get('title', '')}\" views={v['snapshots']['7d'].get('views')} "
            f"avg_view%={v['snapshots']['7d'].get('averageViewPercentage')} "
            f"ctr={v['snapshots']['7d'].get('videoThumbnailImpressionsClickRate')} "
            f"drop={v.get('drop')}"
            for v in done[-30:]
        )
        try:
            res = gemini(
                "Analyse these documentary videos (best and worst). Return 6 concrete, short rules to improve the "
                "NEXT videos' topics, titles, hooks and pacing. Mention where viewers drop off.\n" + rows +
                '\nReturn JSON: {"lessons":["..."]}'
            ) or {}
            lessons = [str(x).strip() for x in (res.get("lessons") or []) if str(x).strip()]
            if lessons:
                ins["lessons"] = lessons[:8]
        except Exception as e:
            print("lessons failed:", e)

    save_insights(ins)
    print("insights:", ins)


if __name__ == "__main__":
    main()
