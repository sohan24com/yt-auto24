"""Feedback loop: snapshots at 24h/72h/7d, thumbnail+title swap on weak CTR,
category re-weighting, and AI-written lessons that feed the next topics/scripts."""
import datetime as dt, tempfile
from pathlib import Path
from . import drive as D, youtube as Y
from .common import cfg, now, load_videos, save_video, load_insights, save_insights, gemini, notify

SNAPS = {"24h": 24, "72h": 72, "7d": 168}


def age_h(rec):
    return (now() - dt.datetime.fromisoformat(rec["created"])).total_seconds() / 3600


def biggest_drop(rec):
    s = now().date()
    curve = Y.retention_curve(rec["video_id"], rec["created"][:10], s.isoformat())
    if len(curve) < 10:
        return None
    worst = max(range(1, len(curve)), key=lambda i: curve[i - 1][1] - curve[i][1])
    t = curve[worst][0] * rec["duration"]
    scene = [sc for sc in rec["scenes"] if sc["start"] <= t][-1]["title"] if rec["scenes"] else ""
    return {"at_ratio": round(curve[worst][0], 2), "scene": scene}


def score(r):
    return r.get("views", 0) * (0.5 + r.get("averageViewPercentage", 0) / 100)


def packaging_underperforming(report, threshold, minimum_impressions=100):
    impressions = report.get("videoThumbnailImpressions")
    ctr = report.get("videoThumbnailImpressionsClickRate")
    if not isinstance(impressions, (int, float)) or impressions < minimum_impressions or ctr is None:
        return False
    ctr = ctr / 100 if ctr > 1 else ctr
    return ctr < threshold


def main():
    c = cfg()["optimize"]
    videos = [v for v in load_videos() if v.get("video_id")]
    if not videos:
        return
    st = Y.stats([v["video_id"] for v in videos])
    today = now().date().isoformat()

    for rec in videos:
        a = age_h(rec)
        rep = Y.video_report(rec["video_id"], rec["created"][:10], today)
        rep["public_views"] = st.get(rec["video_id"], {}).get("viewCount", 0)
        for k, h in SNAPS.items():
            if a >= h and k not in rec["snapshots"]:
                rec["snapshots"][k] = rep
        rec["latest"] = rep

        # weak packaging -> swap thumbnail + title once, after 48h
        ctr = rep.get("videoThumbnailImpressionsClickRate")
        if ctr is not None and ctr > 1:
            ctr = ctr / 100
        # Avoid changing live packaging based on a sparse or absent CTR sample.
        weak = packaging_underperforming(rep, c["ctr_threshold"])
        if a >= c["thumb_swap_after_hours"] and weak and not rec["swapped"] and len(rec["thumb_ids"]) > 1:
            try:
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "alt.jpg"
                    D.download(rec["thumb_ids"][1], p)
                    Y.set_thumbnail(rec["video_id"], p)
                if rec.get("title_b") and rec["title_b"] != rec["title"]:
                    Y.update_title(rec["video_id"], rec["title_b"])
                rec["swapped"] = True
                notify(f"Swapped thumbnail/title for {rec['video_id']} (CTR {ctr})")
            except Exception as e:
                print("swap failed:", e)
        if "7d" in rec["snapshots"] and "drop" not in rec:
            rec["drop"] = biggest_drop(rec)
        save_video(rec)

    # ---- learn: category weights + lessons ----
    done = [v for v in videos if "7d" in v["snapshots"]]
    reliable = [v for v in done if int(v["snapshots"]["7d"].get("views") or 0) >= 50]
    ins = load_insights()
    if len(reliable) >= 8:
        by = {}
        for v in reliable:
            by.setdefault(v["category"], []).append(score(v["snapshots"]["7d"]))
        prior = cfg()["category_mix"]
        avg = {k: sum(x) / len(x) for k, x in by.items() if len(x) >= 3}
        if len(avg) >= 2:
            tot = sum(avg.values()) or 1
            w = {k: 0.5 * prior.get(k, 0.1) + 0.5 * avg.get(k, 0) / tot for k in prior}
            w = {k: max(x, 0.08) for k, x in w.items()}
            s = sum(w.values())
            ins["weights"] = {k: round(x / s, 3) for k, x in w.items()}
    reliable_views = sum(int(v["snapshots"]["7d"].get("views") or 0) for v in reliable)
    if len(reliable) >= 8 and reliable_views >= 1000:
        rows = "\n".join(f"- [{v['category']}] \"{v['title']}\" views={v['snapshots']['7d'].get('views')} "
                         f"avg_view%={v['snapshots']['7d'].get('averageViewPercentage')} "
                         f"ctr={v['snapshots']['7d'].get('videoThumbnailImpressionsClickRate')} "
                         f"drop={v.get('drop')}" for v in reliable[-30:])
        try:
            ins["lessons"] = gemini(
                "Analyse these documentary videos (best and worst). Return 6 concrete, short rules to improve the "
                "NEXT videos' topics, titles, hooks and pacing. Mention where viewers drop off.\n" + rows +
                '\nReturn JSON: {"lessons":["..."]}')["lessons"][:8]
        except Exception as e:
            print("lessons failed:", e)
    save_insights(ins)
    print("insights:", ins)


if __name__ == "__main__":
    main()
