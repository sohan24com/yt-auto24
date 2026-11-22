import random, re, requests, time
from pathlib import Path
from .common import env

PEXELS = {"Authorization": ""}
FALLBACK = ["dark cinematic landscape", "old map parchment", "night sky stars", "misty mountains", "ancient architecture"]
credits = set()


def _get(url, **kw):
    PEXELS["Authorization"] = env("PEXELS_API_KEY")
    for i in range(3):
        try:
            r = requests.get(url, headers=PEXELS, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(30)
                continue
            if r.ok:
                return r.json()
        except Exception:
            time.sleep(3)
    return {}


def _download(url, dest):
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > 90 << 20:
                        return False
                    f.write(chunk)
        return True
    except Exception:
        return False


def _search_videos(q):
    d = _get("https://api.pexels.com/videos/search", params={"query": q, "per_page": 10, "orientation": "landscape"})
    out = []
    for v in d.get("videos", []):
        files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4" and f.get("width")]
        good = sorted([f for f in files if f["width"] >= 1280], key=lambda f: f["width"])
        pick = good[0] if good else (max(files, key=lambda f: f["width"]) if files else None)
        if pick:
            out.append(("video", f"pexels-v{v['id']}", pick["link"], v.get("user", {}).get("name", "")))
    return out


def _search_photos(q):
    d = _get("https://api.pexels.com/v1/search", params={"query": q, "per_page": 6, "orientation": "landscape"})
    return [("image", f"pexels-p{p['id']}", p["src"]["large2x"], p.get("photographer", "")) for p in d.get("photos", [])]


def _search_nasa(q):
    try:
        d = requests.get("https://images-api.nasa.gov/search", params={"q": q, "media_type": "image"}, timeout=30).json()
        out = []
        for it in d["collection"]["items"][:4]:
            nid = it["data"][0]["nasa_id"]
            assets = requests.get(it["href"], timeout=30).json()
            big = [a for a in assets if re.search(r"~(large|medium)\.jpg$", a)]
            if big:
                out.append(("image", f"nasa-{nid}", big[0].replace(" ", "%20"), "NASA"))
        return out
    except Exception:
        return []


def gather(idx, queries, need, used, wd):
    """Return `need` assets for a scene: [{path, kind, reused}]."""
    wd = Path(wd)
    pool = []
    for q in list(queries) + FALLBACK:
        if len(pool) >= need:
            break
        cands = _search_videos(q) + _search_photos(q)
        if len(pool) + len(cands) < need and idx >= 0:
            cands += _search_nasa(q)
        random.shuffle(cands)
        cands.sort(key=lambda c: c[0] != "video")  # prefer real footage
        for kind, uid, url, author in cands:
            if uid in used or len(pool) >= need:
                continue
            ext = "mp4" if kind == "video" else "jpg"
            dest = wd / f"{uid}.{ext}"
            if dest.exists() or _download(url, dest):
                used.add(uid)
                pool.append({"path": dest, "kind": kind, "reused": False})
                credits.add(("NASA" if author == "NASA" else "Pexels") + (f" ({author})" if author and author != "NASA" else ""))
    if not pool:
        raise RuntimeError(f"no footage for scene {idx}")
    out, i = list(pool), 0
    while len(out) < need:  # reuse only when we must
        a = dict(pool[i % len(pool)])
        a["reused"] = True
        out.append(a)
        i += 1
    return out[:need]


def credit_line():
    names = sorted(c.split(" (")[0] for c in credits)
    uniq = sorted(set(names))
    return ", ".join(uniq) + " (free-license footage). Full contributor list available on request."
