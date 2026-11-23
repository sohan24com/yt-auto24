import time
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from .common import cfg
from .google_auth import creds

_yt = _an = None


def yt():
    global _yt
    if _yt is None:
        _yt = build("youtube", "v3", credentials=creds(), cache_discovery=False)
    return _yt


def analytics_api():
    global _an
    if _an is None:
        _an = build("youtubeAnalytics", "v2", credentials=creds(), cache_discovery=False)
    return _an


def _clean_tags(tags):
    out = []
    seen = set()
    total_len = 0
    for t in (tags or []):
        s = " ".join(str(t or "").split()).strip().replace("<", "").replace(">", "")
        if not s:
            continue
        s = s[:50]
        key = s.lower()
        if key in seen:
            continue
        # YouTube video tags total character budget is 500 chars
        if total_len + len(s) > 480:
            break
        seen.add(key)
        out.append(s)
        total_len += len(s)
    return out


def upload(path, title, description, tags, thumb=None):
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"video file not found for upload: {src}")

    c = cfg().get("youtube") or {}
    status = {
        "privacyStatus": c.get("privacy", "private"),
        "selfDeclaredMadeForKids": False,
    }
    if c.get("synthetic_disclosure"):
        status["containsSyntheticMedia"] = True

    snippet = {
        "title": " ".join(str(title or "Untitled Documentary").split()).strip()[:100],
        "description": str(description or "")[:4900],
        "categoryId": str(c.get("category_id", "27")),
        "defaultLanguage": c.get("default_language", "en"),
        "defaultAudioLanguage": c.get("default_audio_language", "en"),
    }
    clean_tags = _clean_tags(tags)
    if clean_tags:
        snippet["tags"] = clean_tags

    body = {"snippet": snippet, "status": status}
    media = MediaFileUpload(str(src), chunksize=16 << 20, resumable=True, mimetype="video/mp4")
    try:
        req = yt().videos().insert(part="snippet,status", body=body, media_body=media)
        resp, fails = None, 0
        while resp is None:
            try:
                _, resp = req.next_chunk()
            except HttpError as e:
                status_code = getattr(getattr(e, "resp", None), "status", None)
                if status_code in (500, 502, 503, 504) and fails < 6:
                    fails += 1
                    time.sleep(min(60, 5 * fails))
                else:
                    raise
        vid = resp["id"]
    finally:
        if hasattr(media, "stream") and media.stream():
            try:
                media.stream().close()
            except Exception:
                pass

    if thumb:
        set_thumbnail(vid, thumb)
    return vid


def set_thumbnail(vid, path):
    p = Path(path)
    if not p.exists():
        print("thumbnail file missing:", p)
        return
    media = MediaFileUpload(str(p), resumable=True)
    try:
        yt().thumbnails().set(videoId=vid, media_body=media).execute()
    except HttpError as e:
        print("thumbnail failed (channel must be verified):", e)
    finally:
        if hasattr(media, "stream") and media.stream():
            try:
                media.stream().close()
            except Exception:
                pass


def update_title(vid, title):
    res = yt().videos().list(part="snippet", id=vid).execute()
    items = res.get("items") or []
    if not items:
        raise RuntimeError(f"video not found for title update: {vid}")
    snippet = dict(items[0].get("snippet") or {})
    snippet["title"] = " ".join(str(title or "").split()).strip()[:100]
    yt().videos().update(part="snippet", body={"id": vid, "snippet": snippet}).execute()


def stats(ids):
    clean_ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    out = {}
    for i in range(0, len(clean_ids), 50):
        batch = clean_ids[i:i + 50]
        r = yt().videos().list(part="statistics", id=",".join(batch)).execute()
        for it in r.get("items", []):
            parsed = {}
            for k, v in (it.get("statistics") or {}).items():
                try:
                    parsed[k] = int(v)
                except (TypeError, ValueError):
                    parsed[k] = v
            out[it["id"]] = parsed
    return out


def video_report(vid, start, end):
    """Views, avg view %, thumbnail CTR (when the channel exposes it)."""
    base = "views,averageViewPercentage,averageViewDuration"
    for metrics in (base + ",videoThumbnailImpressions,videoThumbnailImpressionsClickRate", base):
        try:
            r = analytics_api().reports().query(
                ids="channel==MINE",
                startDate=start,
                endDate=end,
                metrics=metrics,
                filters=f"video=={vid}",
            ).execute()
            rows = r.get("rows") or []
            if not rows or not rows[0]:
                continue
            names = [h["name"] for h in (r.get("columnHeaders") or [])]
            return dict(zip(names, rows[0]))
        except HttpError:
            continue
    return {}


def retention_curve(vid, start, end):
    try:
        r = analytics_api().reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={vid}",
        ).execute()
        return [(row[0], row[1]) for row in (r.get("rows") or []) if len(row) >= 2]
    except HttpError:
        return []
