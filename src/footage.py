import os, random, re, requests, time
from pathlib import Path
from urllib.parse import quote

FALLBACK = ["dark cinematic landscape", "old map parchment", "night sky stars", "misty mountains", "ancient architecture"]
credits = set()


def _key(name):
    return os.environ.get(name, "").strip()


def _get(url, headers=None, **kw):
    for i in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=30, **kw)
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


# ---------------- Pexels ----------------
def _pexels_videos(q):
    k = _key("PEXELS_API_KEY")
    if not k:
        return []
    d = _get("https://api.pexels.com/videos/search", {"Authorization": k},
             params={"query": q, "per_page": 10, "orientation": "landscape"})
    out = []
    for v in d.get("videos", []):
        files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4" and f.get("width")]
        good = sorted([f for f in files if f["width"] >= 1280], key=lambda f: f["width"])
        pick = good[0] if good else (max(files, key=lambda f: f["width"]) if files else None)
        if pick:
            out.append(("video", f"pexels-v{v['id']}", pick["link"], "Pexels"))
    return out


def _pexels_photos(q):
    k = _key("PEXELS_API_KEY")
    if not k:
        return []
    d = _get("https://api.pexels.com/v1/search", {"Authorization": k},
             params={"query": q, "per_page": 6, "orientation": "landscape"})
    return [("image", f"pexels-p{p['id']}", p["src"]["large2x"], "Pexels") for p in d.get("photos", [])]


# ---------------- Pixabay ----------------
def _pixabay_videos(q):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get("https://pixabay.com/api/videos/", params={"key": k, "q": q, "per_page": 10})
    out = []
    for h in d.get("hits", []):
        v = h.get("videos", {})
        pick = next((v[s]["url"] for s in ("large", "medium") if v.get(s, {}).get("url")), None)
        if pick:
            out.append(("video", f"pixabay-v{h['id']}", pick, "Pixabay"))
    return out


def _pixabay_photos(q):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get("https://pixabay.com/api/", params={"key": k, "q": q, "image_type": "photo",
                                                   "orientation": "horizontal", "per_page": 8, "min_width": 1280})
    return [("image", f"pixabay-p{h['id']}", h["largeImageURL"], "Pixabay") for h in d.get("hits", [])]


# ---------------- NASA (public domain, no key) ----------------
SPACE_WORDS = ("space", "planet", "star", "galaxy", "moon", "mars", "earth", "sun", "astronaut", "rocket",
               "telescope", "nebula", "satellite", "orbit", "comet", "cosmos", "universe", "solar")


def _nasa(q):
    out = []
    for media in ("image", "video"):
        try:
            d = requests.get("https://images-api.nasa.gov/search", params={"q": q, "media_type": media}, timeout=30).json()
            for it in d["collection"]["items"][:3]:
                nid = it["data"][0]["nasa_id"]
                assets = requests.get(it["href"], timeout=30).json()
                if media == "image":
                    pat = r"~(large|medium)\.jpg$"
                else:
                    pat = r"~(large|medium|orig)\.mp4$"
                big = [a for a in assets if re.search(pat, a)]
                if media == "video":  # prefer large, avoid huge orig files
                    big.sort(key=lambda a: 0 if "~large" in a else 1 if "~medium" in a else 2)
                if big:
                    out.append((media, f"nasa-{nid}", big[0].replace(" ", "%20"), "NASA"))
        except Exception:
            continue
    return out


# ---------------- Internet Archive (public-domain films, no key) ----------------
def _archive(q):
    out = []
    try:
        query = (f"({q}) AND mediatype:movies AND "
                 "(collection:prelinger OR collection:nasa OR licenseurl:*publicdomain*)")
        d = requests.get("https://archive.org/advancedsearch.php",
                         params={"q": query, "fl[]": "identifier", "rows": 4, "output": "json",
                                 "sort[]": "downloads desc"}, timeout=30).json()
        for doc in d["response"]["docs"][:3]:
            ident = doc["identifier"]
            meta = requests.get(f"https://archive.org/metadata/{ident}", timeout=30).json()
            files = [f for f in meta.get("files", [])
                     if f.get("name", "").lower().endswith(".mp4") and f.get("size", "0").isdigit()
                     and 2 << 20 < int(f["size"]) < 80 << 20]
            if files:
                best = max(files, key=lambda f: int(f["size"]))
                out.append(("video", f"archive-{ident}", f"https://archive.org/download/{ident}/{quote(best['name'])}",
                            "Internet Archive (public domain)"))
    except Exception:
        pass
    return out


def gather(idx, queries, need, used, wd):
    """Return `need` assets for a scene: [{path, kind, reused}]."""
    if not (_key("PEXELS_API_KEY") or _key("PIXABAY_API_KEY")):
        raise RuntimeError("Set PEXELS_API_KEY and/or PIXABAY_API_KEY")
    wd = Path(wd)
    pool = []
    for q in list(queries) + FALLBACK:
        if len(pool) >= need:
            break
        cands = _pexels_videos(q) + _pixabay_videos(q) + _pexels_photos(q) + _pixabay_photos(q)
        if any(w in q.lower() for w in SPACE_WORDS):
            cands += _nasa(q)
        if len(pool) + len(cands) < need:       # still short: widen to NASA + Internet Archive
            cands += _nasa(q) + _archive(q)
        random.shuffle(cands)
        cands.sort(key=lambda c: c[0] != "video")  # prefer real footage
        for kind, uid, url, source in cands:
            if uid in used or len(pool) >= need:
                continue
            dest = wd / f"{uid}.{'mp4' if kind == 'video' else 'jpg'}"
            if dest.exists() or _download(url, dest):
                used.add(uid)
                pool.append({"path": dest, "kind": kind, "reused": False})
                credits.add(source)
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
    return ", ".join(sorted(credits)) + " (free-license footage and images)."
