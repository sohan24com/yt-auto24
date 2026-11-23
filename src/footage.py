from concurrent.futures import ThreadPoolExecutor, as_completed
import json, random, re, requests, subprocess, time
from pathlib import Path
from .common import env

FALLBACK = [
    "dark cinematic landscape",
    "old map parchment",
    "night sky stars",
    "misty mountains",
    "ancient architecture",
    "deep ocean aerial",
    "futuristic city night",
    "dramatic clouds timelapse",
]
credits = set()

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "that", "this", "it", "as", "into", "over", "after", "before", "about",
    "how", "why", "what", "when", "where", "which", "who",
}

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "footage"
_HISTORY_FILE = _CACHE_DIR / "usage_history.json"


def _load_history():
    try:
        if _HISTORY_FILE.exists():
            data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_history(hist):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        trimmed = dict(list(hist.items())[-1500:])
        _HISTORY_FILE.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")
    except Exception:
        pass


def _expand_queries(queries):
    """Build clean search queries plus distilled 2-3 word keyword fallbacks."""
    expanded = []
    seen = set()

    def _add(q):
        q_clean = re.sub(r"\s+", " ", str(q or "")).strip()
        key = q_clean.lower()
        if q_clean and key not in seen:
            seen.add(key)
            expanded.append(q_clean)

    for raw in queries or []:
        _add(raw)
        tokens = [t for t in re.findall(r"[A-Za-z0-9]+", str(raw).lower()) if t not in _STOPWORDS and len(t) > 2]
        if len(tokens) >= 3:
            _add(" ".join(tokens[:3]))
        if len(tokens) >= 2:
            _add(" ".join(tokens[:2]))

    for fb in FALLBACK:
        _add(fb)
    return expanded


def _get(url, headers=None, **kw):
    req_headers = {"User-Agent": "yt-auto24/2.1 (documentary pipeline)"}
    if headers:
        req_headers.update(headers)
    for _ in range(3):
        try:
            r = requests.get(url, headers=req_headers, timeout=25, **kw)
            if r.status_code == 429:
                time.sleep(15)
                continue
            if r.ok:
                return r.json()
        except Exception:
            time.sleep(2)
    return {}


def _verify_media(path):
    """Verify downloaded file is decodable and at least 640x360 so edit.py never fails."""
    p = Path(path)
    if not p.exists() or p.stat().st_size < 2048:
        return False
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "default=nw=1:nk=1",
                str(p),
            ],
            capture_output=True, text=True, timeout=15,
        )
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        if r.returncode != 0 or len(lines) < 2:
            return False
        w, h = int(lines[0]), int(lines[1])
        return w >= 640 and h >= 360
    except Exception:
        return p.stat().st_size > 10_000


def _download(url, dest):
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req_headers = {"User-Agent": "yt-auto24/2.1 (documentary pipeline)"}
        with requests.get(url, stream=True, timeout=60, headers=req_headers) as r:
            r.raise_for_status()
            size = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > 90 << 20:
                        tmp.unlink(missing_ok=True)
                        return False
                    f.write(chunk)
        tmp.replace(dest)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


def _pick_pixabay_video(videos_dict):
    """Pick optimal HD landscape MP4 stream (prefer 1920x1080 or 1280x720 over 4K bloat)."""
    for tier in ("medium", "large", "small"):
        v = videos_dict.get(tier) or {}
        url = v.get("url")
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
        if url and (w == 0 or (w >= h and 1280 <= w <= 2560)):
            return url
    for tier in ("large", "medium", "small", "tiny"):
        url = (videos_dict.get(tier) or {}).get("url")
        if url:
            return url
    return None


def _search_pixabay_videos(q):
    key = env("PIXABAY_API_KEY")
    if not key:
        return []
    rv = _get(
        "https://pixabay.com/api/videos/",
        params={
            "key": key,
            "q": q,
            "video_type": "film",
            "per_page": 15,
            "safesearch": "true",
            "order": "popular",
        },
    )
    out = []
    for hit in rv.get("hits", []):
        dur = float(hit.get("duration") or 0)
        if dur and (dur < 2.0 or dur > 90.0):
            continue
        url = _pick_pixabay_video(hit.get("videos") or {})
        if url:
            out.append(("video", f"pixabay-v{hit['id']}", url, hit.get("user", ""), "Pixabay"))
    return out


def _search_pixabay_photos(q):
    key = env("PIXABAY_API_KEY")
    if not key:
        return []
    ri = _get(
        "https://pixabay.com/api/",
        params={
            "key": key,
            "q": q,
            "image_type": "photo",
            "orientation": "horizontal",
            "min_width": 1280,
            "per_page": 12,
            "safesearch": "true",
        },
    )
    out = []
    for hit in ri.get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if url:
            out.append(("image", f"pixabay-p{hit['id']}", url, hit.get("user", ""), "Pixabay"))
    return out


def _search_wikimedia(q):
    out = []
    d = _get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": f"{q} filetype:bitmap",
            "gsrnamespace": 6,
            "gsrlimit": 10,
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": 1920,
            "format": "json",
        },
    )
    pages = d.get("query", {}).get("pages", {})
    for pid, page in pages.items():
        infos = page.get("imageinfo", [])
        if not infos:
            continue
        info = infos[0]
        if info.get("mime", "") not in ("image/jpeg", "image/png"):
            continue
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        if w and h and (w < h or w < 900):
            continue
        url = info.get("thumburl") or info.get("url")
        if url:
            out.append(("image", f"wiki-{pid}", url, "Wikimedia Commons", "Wikimedia"))
    return out


def _search_nasa(q):
    out = []
    d = _get("https://images-api.nasa.gov/search", params={"q": q, "media_type": "image,video"})
    for it in d.get("collection", {}).get("items", [])[:8]:
        data = (it.get("data") or [{}])[0]
        nid = data.get("nasa_id")
        mtype = data.get("media_type", "image")
        href = it.get("href")
        if not nid or not href:
            continue
        try:
            assets = requests.get(href, timeout=20).json()
            if mtype == "video":
                mp4s = [a for a in assets if re.search(r"~(medium|small|mobile)\.mp4$", a)]
                if mp4s:
                    out.append(("video", f"nasa-v{nid}", mp4s[0].replace(" ", "%20"), "NASA", "NASA"))
            else:
                big = [a for a in assets if re.search(r"~(large|medium)\.jpg$", a)]
                if big:
                    out.append(("image", f"nasa-{nid}", big[0].replace(" ", "%20"), "NASA", "NASA"))
        except Exception:
            continue
    return out


def _search_pexels_optional(q):
    key = env("PEXELS_API_KEY")
    if not key:
        return []
    headers = {"Authorization": key}
    out = []
    d = _get("https://api.pexels.com/videos/search", headers=headers, params={"query": q, "per_page": 8, "orientation": "landscape"})
    for v in d.get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        good = sorted([f for f in files if 1280 <= f["width"] <= 2560], key=lambda f: f["width"])
        pick = good[0] if good else None
        if pick and pick.get("link"):
            out.append(("video", f"pexels-v{v['id']}", pick["link"], v.get("user", {}).get("name", ""), "Pexels"))
    return out


def _parallel_search(q):
    """Query Pixabay (videos + photos), Wikimedia, NASA, and optional Pexels concurrently."""
    tasks = [_search_pixabay_videos, _search_pixabay_photos, _search_wikimedia, _search_nasa]
    if env("PEXELS_API_KEY"):
        tasks.append(_search_pexels_optional)

    results = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = [ex.submit(fn, q) for fn in tasks]
        for fut in as_completed(futures):
            try:
                results.extend(fut.result() or [])
            except Exception:
                pass
    return results


def _rank_candidates(cands, history):
    """Rank candidates by: 1) unused in prior channel videos, 2) real video over still image,
    3) balanced source rotation across Pixabay, Wikimedia, and NASA."""
    source_seen = {}
    ranked = []
    random.shuffle(cands)
    for item in cands:
        kind, uid, url, author, source_name = item
        prior_uses = int(history.get(uid, 0))
        src_penalty = source_seen.get(source_name, 0)
        source_seen[source_name] = src_penalty + 1
        score = (
            prior_uses * 10,
            0 if kind == "video" else 3,
            src_penalty,
        )
        ranked.append((score, item))
    ranked.sort(key=lambda x: x[0])
    return [item for _, item in ranked]


def gather(idx, queries, need, used, wd):
    """Return `need` verified, high-diversity assets for a scene: [{path, kind, reused}]."""
    wd = Path(wd)
    wd.mkdir(parents=True, exist_ok=True)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    history = _load_history()
    pool = []
    search_queries = _expand_queries(queries)

    for q in search_queries:
        if len(pool) >= need:
            break
        cands = _rank_candidates(_parallel_search(q), history)

        for kind, uid, url, author, source_name in cands:
            if uid in used or len(pool) >= need:
                continue
            ext = "mp4" if kind == "video" else "jpg"
            dest = wd / f"{uid}.{ext}"
            cached = _CACHE_DIR / f"{uid}.{ext}"

            ok = False
            if dest.exists() and _verify_media(dest):
                ok = True
            elif cached.exists() and _verify_media(cached):
                dest.write_bytes(cached.read_bytes())
                ok = True
            elif _download(url, dest):
                if _verify_media(dest):
                    ok = True
                    try:
                        cached.write_bytes(dest.read_bytes())
                    except Exception:
                        pass
                else:
                    dest.unlink(missing_ok=True)

            if ok:
                used.add(uid)
                history[uid] = int(history.get(uid, 0)) + 1
                pool.append({"path": dest, "kind": kind, "reused": False})
                credits.add(source_name + (f" ({author})" if author and author != source_name else ""))

    if not pool:
        raise RuntimeError(f"no footage for scene {idx}")

    _save_history(history)

    out, i = list(pool), 0
    while len(out) < need:
        a = dict(pool[i % len(pool)])
        a["reused"] = True
        out.append(a)
        i += 1
    return out[:need]


def credit_line():
    names = sorted(c.split(" (")[0] for c in credits)
    uniq = sorted(set(names))
    if not uniq:
        uniq = ["Pixabay", "Wikimedia Commons", "NASA"]
    return ", ".join(uniq) + " (free-license footage). Full contributor list available on request."
