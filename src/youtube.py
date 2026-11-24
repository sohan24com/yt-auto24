import time
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


def upload(path, title, description, tags, thumb=None):
    c = cfg()["youtube"]
    status = {"privacyStatus": c["privacy"], "selfDeclaredMadeForKids": False}
    if c.get("synthetic_disclosure"):
        status["containsSyntheticMedia"] = True
    body = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags,
                        "categoryId": c["category_id"], "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status}
    media = MediaFileUpload(str(path), chunksize=16 << 20, resumable=True, mimetype="video/mp4")
    req = yt().videos().insert(part="snippet,status", body=body, media_body=media)
    resp, fails = None, 0
    while resp is None:
        try:
            _, resp = req.next_chunk()
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and fails < 6:
                fails += 1
                time.sleep(5 * fails)
            else:
                raise
    vid = resp["id"]
    if thumb:
        set_thumbnail(vid, thumb)
    return vid


def set_thumbnail(vid, path):
    try:
        yt().thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(path))).execute()
    except HttpError as e:
        print("thumbnail failed (channel must be verified):", e)


def update_title(vid, title):
    v = yt().videos().list(part="snippet", id=vid).execute()["items"][0]["snippet"]
    v["title"] = title[:100]
    yt().videos().update(part="snippet", body={"id": vid, "snippet": v}).execute()


def stats(ids):
    out = {}
    for i in range(0, len(ids), 50):
        r = yt().videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            out[it["id"]] = {k: int(v) for k, v in it["statistics"].items()}
    return out


def video_report(vid, start, end):
    """Views, avg view %, thumbnail CTR (when the channel exposes it)."""
    base = "views,averageViewPercentage,averageViewDuration"
    for metrics in (base + ",videoThumbnailImpressions,videoThumbnailImpressionsClickRate", base):
        try:
            r = analytics_api().reports().query(ids="channel==MINE", startDate=start, endDate=end,
                                                metrics=metrics, filters=f"video=={vid}").execute()
            row = (r.get("rows") or [[]])[0]
            names = [h["name"] for h in r["columnHeaders"]]
            return dict(zip(names, row))
        except HttpError:
            continue
    return {}


def retention_curve(vid, start, end):
    try:
        r = analytics_api().reports().query(ids="channel==MINE", startDate=start, endDate=end,
                                            metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
                                            filters=f"video=={vid}").execute()
        return [(row[0], row[1]) for row in r.get("rows", [])]
    except HttpError:
        return []


def localize(vid, localizations):
    """Add translated titles/descriptions; YouTube shows each viewer the one for their language."""
    yt().videos().update(part="localizations", body={"id": vid, "localizations": localizations}).execute()
