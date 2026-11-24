"""Footage that matches what the narrator says.
One search query per SHOT (written from the sentence spoken during that shot), results re-ranked by how well
their tags/title match the query, plus real photos of real places from Wikimedia Commons (CC0 / public
domain / CC BY only, with attribution added to the description)."""
import hashlib, html, os, random, re, requests, time
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from urllib.parse import quote

FALLBACK = ["dark cinematic landscape", "old map parchment", "night sky stars", "misty mountains", "ancient architecture"]
STOP = set("the and with for from over under into this that were was are his her their its they them have has had "
           "been will would could then than when what which while about after before video stock footage".split())
SPACE_WORDS = ("space", "planet", "star", "galaxy", "moon", "mars", "earth", "sun", "astronaut", "rocket",
               "telescope", "nebula", "satellite", "orbit", "comet", "cosmos", "universe", "solar")
UA = {"User-Agent": "yt-auto24-docbot/1.0 (https://github.com/sohan24com/yt-auto24)"}

credits = set()
attributions = []
_cache = {}
_all = []              # every downloaded asset (for last-resort reuse)
_wiki_used = [0]
_last_pixabay = [0.0]


def reset():
    """Call at the start of every build: drop state from an earlier attempt (its files are deleted)."""
    credits.clear()
    attributions.clear()
    _all.clear()
    _wiki_used[0] = 0


def _key(name):
    return os.environ.get(name, "").strip()


def _words(text):
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}


def _score(query, text):
    return len(_words(query) & _words(text))


def _get(url, headers=None, **kw):
    for i in range(3):
        try:
            if "pixabay.com" in url:                       # Pixabay allows ~100 requests/minute
                wait = 0.7 - (time.time() - _last_pixabay[0])
                if wait > 0:
                    time.sleep(wait)
                _last_pixabay[0] = time.time()
            r = requests.get(url, headers=headers, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(30)
                continue
            if r.ok:
                return r.json()
        except Exception:
            time.sleep(3)
    return {}


def _download(url, dest, headers=None):
    dest = Path(dest)
    try:
        with requests.get(url, stream=True, timeout=60, headers=headers) as r:
            r.raise_for_status()
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > 90 << 20:
                        raise ValueError("download exceeds 90 MB limit")
                    f.write(chunk)
        if size == 0:
            raise ValueError("download returned an empty file")
        return True
    except Exception:
        dest.unlink(missing_ok=True)
        return False


def _slug(url):
    return (url or "").rstrip("/").split("/")[-1].replace("-", " ")


# ---------------- providers: each returns [(kind, uid, url, source, text)] ----------------
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
            out.append(("video", f"pexels-v{v['id']}", pick["link"], "Pexels", _slug(v.get("url")),
                    {"asset_page": v.get("url", ""), "license": "Pexels License",
                     "license_url": "https://www.pexels.com/license/"}))
    return out


def _pexels_photos(q):
    k = _key("PEXELS_API_KEY")
    if not k:
        return []
    d = _get("https://api.pexels.com/v1/search", {"Authorization": k},
             params={"query": q, "per_page": 8, "orientation": "landscape"})
    return [("image", f"pexels-p{p['id']}", p["src"]["large2x"], "Pexels", p.get("alt", "") + " " + _slug(p.get("url")),
             {"asset_page": p.get("url", ""), "license": "Pexels License",
              "license_url": "https://www.pexels.com/license/"})
            for p in d.get("photos", [])]


def _pixabay_videos(q):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get("https://pixabay.com/api/videos/", params={"key": k, "q": q, "per_page": 12})
    out = []
    for h in d.get("hits", []):
        v = h.get("videos", {})
        pick = next((v[s]["url"] for s in ("large", "medium") if v.get(s, {}).get("url")), None)
        if pick:
            out.append(("video", f"pixabay-v{h['id']}", pick, "Pixabay", h.get("tags", ""),
                        {"asset_page": f"https://pixabay.com/videos/id-{h['id']}/",
                         "license": "Pixabay Content License",
                         "license_url": "https://pixabay.com/service/license/"}))
    return out


def _pixabay_photos(q):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get("https://pixabay.com/api/", params={"key": k, "q": q, "image_type": "photo",
                                                   "orientation": "horizontal", "per_page": 10, "min_width": 1280})
    return [("image", f"pixabay-p{h['id']}", h["largeImageURL"], "Pixabay", h.get("tags", ""),
             {"asset_page": h.get("pageURL", ""), "license": "Pixabay Content License",
              "license_url": "https://pixabay.com/service/license/"}) for h in d.get("hits", [])]


def _nasa(q):
    out = []
    for media in ("image", "video"):
        try:
            d = requests.get("https://images-api.nasa.gov/search", params={"q": q, "media_type": media}, timeout=30).json()
            for it in d["collection"]["items"][:3]:
                nid = it["data"][0]["nasa_id"]
                title = it["data"][0].get("title", "")
                assets = requests.get(it["href"], timeout=30).json()
                pat = r"~(large|medium)\.jpg$" if media == "image" else r"~(large|medium|orig)\.mp4$"
                big = [a for a in assets if re.search(pat, a)]
                if media == "video":
                    big.sort(key=lambda a: 0 if "~large" in a else 1 if "~medium" in a else 2)
                if big:
                    out.append((media, f"nasa-{nid}", big[0].replace(" ", "%20"), "NASA", title,
                            {"asset_page": f"https://images.nasa.gov/details/{nid}",
                             "license": "NASA media usage terms (review third-party restrictions)",
                             "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                             "license_status": "review_required"}))
        except Exception:
            continue
    return out


def _archive(q):
    """Return only Internet Archive items with a recorded public-domain/CC0/CC BY license."""
    out = []
    try:
        query = (f"({q}) AND mediatype:movies AND "
                 "(collection:prelinger OR collection:nasa OR licenseurl:*publicdomain* OR "
                 "licenseurl:*creativecommons.org/licenses/by/*)")
        d = requests.get("https://archive.org/advancedsearch.php",
                         params={"q": query, "fl[]": "identifier", "rows": 4, "output": "json",
                                 "sort[]": "downloads desc"}, timeout=30).json()
        for doc in d["response"]["docs"][:3]:
            ident = doc["identifier"]
            meta = requests.get(f"https://archive.org/metadata/{ident}", timeout=30).json()
            metadata = meta.get("metadata", {})
            license_url = str(metadata.get("licenseurl", "")).strip()
            license_name = str(metadata.get("license", "") or metadata.get("rights", "")).strip()
            low = license_url.lower()
            if "creativecommons.org/publicdomain/zero" in low:
                license_status = "allowed_public_domain"
            elif "creativecommons.org/licenses/by/" in low and "by-sa" not in low:
                license_status = "allowed_attribution"
            elif "publicdomain" in low or "public-domain" in low:
                license_status = "allowed_public_domain"
            else:
                continue
            files = [f for f in meta.get("files", [])
                     if f.get("name", "").lower().endswith(".mp4") and f.get("size", "0").isdigit()
                     and 2 << 20 < int(f["size"]) < 80 << 20]
            if files:
                best = max(files, key=lambda f: int(f["size"]))
                out.append(("video", f"archive-{ident}",
                            f"https://archive.org/download/{ident}/{quote(best['name'])}",
                            "Internet Archive", ident.replace("_", " ").replace("-", " "),
                            {"asset_page": f"https://archive.org/details/{ident}",
                             "license": license_name or license_status,
                             "license_url": license_url, "license_status": license_status}))
    except Exception:
        pass
    return out

def _stock(q, with_photos):
    key = (q, with_photos)
    if key not in _cache:
        c = _pexels_videos(q) + _pixabay_videos(q)
        if with_photos:
            c += _pexels_photos(q) + _pixabay_photos(q)
        _cache[key] = c
    return _cache[key]


# ---------------- Wikimedia Commons: real photos of real places/objects ----------------
def wiki_pool(terms, wd):
    """Download a few licence-safe photos per named term. Only CC0 / public domain / CC BY (not BY-SA, NC, ND)."""
    wd = Path(wd)
    pool = []
    _wiki_used[0] = 0
    for term in list(terms or [])[:6]:
        d = _get("https://commons.wikimedia.org/w/api.php", UA,
                 params={"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": term,
                         "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
                         "iiurlwidth": 1920, "format": "json"})
        n = 0
        for pg in (d.get("query") or {}).get("pages", {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            if ii.get("mime") not in ("image/jpeg", "image/png") or ii.get("width", 0) < 1200:
                continue
            md = ii.get("extmetadata", {})
            lic = md.get("LicenseShortName", {}).get("value", "")
            if not re.match(r"(?i)^(cc0|public domain|pd\b|cc by \d)", lic):
                continue
            artist = re.sub(r"<[^>]+>", "", html.unescape(md.get("Artist", {}).get("value", ""))).strip()[:60] or "unknown"
            title = pg.get("title", "").replace("File:", "")[:70]
            url = ii.get("thumburl") or ii.get("url")
            dest = wd / f"wiki-{pg['pageid']}.jpg"
            if url and (dest.exists() or _download(url, dest, UA)):
                pool.append({"path": dest, "kind": "image", "reused": False, "source": "Wikimedia Commons",
                             "words": _words(term + " " + title), "used": False,
                             "credit": f"{title} - {artist} ({lic})",
                             "asset_id": f"commons-{pg['pageid']}",
                             "source_url": f"https://commons.wikimedia.org/wiki/{quote(title.replace(' ', '_'))}",
                             "license": lic,
                             "license_url": md.get("LicenseUrl", {}).get("value", ""),
                             "license_status": "allowed_attribution" if lic.lower().startswith("cc by") else "allowed_public_domain",
                             "illustrative": False})
                n += 1
            if n >= 3:
                break
    print(f"wiki photos: {len(pool)}")
    return pool


# ---------------- one asset per shot ----------------
def _try(cands, q, used, wd, min_score):
    ranked = sorted((c for c in cands if c[1] not in used and _score(q, c[4]) >= min_score),
                    key=lambda c: (-_score(q, c[4]), c[0] != "video", random.random()))
    for candidate in ranked[:4]:
        kind, uid, url, source, match_text = candidate[:5]
        source_meta = candidate[5] if len(candidate) > 5 and isinstance(candidate[5], dict) else {}
        dest = Path(wd) / f"{uid}.{'mp4' if kind == 'video' else 'jpg'}"
        if dest.exists() or _download(url, dest):
            used.add(uid)
            credits.add(source)
            defaults = {
                "Pexels": ("Pexels License", "https://www.pexels.com/license/", "provider_terms"),
                "Pixabay": ("Pixabay Content License", "https://pixabay.com/service/license/", "provider_terms"),
            }
            license_name, license_url, license_status = defaults.get(
                source, ("unknown", "", "review_required"))
            license_name = source_meta.get("license", license_name)
            license_url = source_meta.get("license_url", license_url)
            license_status = source_meta.get("license_status", license_status)
            a = {"path": dest, "kind": kind, "reused": False, "source": source,
                 "asset_id": uid, "source_url": source_meta.get("asset_page", url),
                 "license": license_name, "license_url": license_url,
                 "license_status": license_status, "illustrative": source in ("Pexels", "Pixabay"),
                 "credit": source}
            a.update({"match_words": _words(q + " " + match_text),
                      "match_score": _score(q, match_text)})
            _all.append(a)
            return a
    return None


def _context_card(query, beat, setting, wd):
    """Relevant, visibly labeled graphic fallback; never substitute unrelated stock."""
    key = hashlib.sha1(f"{query}|{beat}|{setting}".encode("utf-8")).hexdigest()[:12]
    dest = Path(wd) / f"context-{key}.png"
    if not dest.exists():
        w, h = 1920, 1080
        im = Image.new("RGB", (w, h), (17, 20, 34))
        d = ImageDraw.Draw(im)
        for radius in (260, 390, 520):
            d.ellipse((w - radius, 100 - radius, w + radius, 100 + radius),
                      outline=(42, 49, 69), width=5)
        d.rectangle((0, 0, w, 14), fill=(174, 111, 42))
        try:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            label_font = ImageFont.truetype(font_path, 36)
            title_font = ImageFont.truetype(font_path, 82)
            body_font = ImageFont.truetype(font_path, 38)
        except OSError:
            label_font = title_font = body_font = ImageFont.load_default()
        d.rounded_rectangle((112, 100, 740, 164), radius=18, fill=(165, 93, 28))
        d.text((140, 111), "ILLUSTRATIVE CONTEXT", font=label_font, fill=(255, 255, 255))
        title = " ".join((query or setting or "Documentary context").split()[:8])
        d.text((112, 265), title[:64], font=title_font, fill=(245, 230, 184))
        excerpt = " ".join((beat or "").split())
        lines, line = [], ""
        for word in excerpt.split():
            candidate = (line + " " + word).strip()
            if d.textlength(candidate, font=body_font) > 1500 and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        for i, row in enumerate(lines[:5]):
            d.text((120, 430 + i * 58), row, font=body_font, fill=(210, 216, 225))
        d.line((112, 850, 1808, 850), fill=(184, 129, 52), width=3)
        d.text((112, 885), f"Place / period: {setting or 'context from narration'}",
               font=label_font, fill=(188, 198, 214))
        im.save(dest)
    return {"path": dest, "kind": "image", "reused": False, "source": "Generated context card",
            "asset_id": key, "source_url": f"urn:yt-auto24:context:{key}",
            "license": "Original generated graphic", "license_url": "",
            "license_status": "original_generated", "illustrative": True,
            "match_words": _words(query + " " + beat + " " + setting), "match_score": 0,
            "is_context_fallback": True}


def gather_shot(q, alt, beat, setting, used, wd, wiki, wiki_cap=0.3, total_shots=100):
    """Find a semantically related asset or create a labeled context graphic."""
    bw = _words(beat)
    qwords = _words(q + " " + alt)
    target = bw | qwords
    if wiki and _wiki_used[0] < wiki_cap * total_shots:
        for w in wiki:
            if not w["used"] and (w["words"] & target):
                w["used"] = True
                _wiki_used[0] += 1
                attributions.append(w["credit"])
                credits.add("Wikimedia Commons")
                return {"path": w["path"], "kind": "image", "reused": False, "source": "Wikimedia Commons",
                        "asset_id": w["asset_id"], "source_url": w["source_url"],
                        "license": w["license"], "license_url": w["license_url"],
                        "license_status": w["license_status"], "illustrative": False,
                        "credit": w["credit"],
                        "match_words": w["words"], "match_score": len(w["words"] & target)}
    setting_q = " ".join((setting or "").split()[:5])
    queries = list(dict.fromkeys(x for x in (q, alt, setting_q) if x))
    # First prefer direct text/tag matches, then lower-confidence matches that still share a term.
    for query in queries:
        a = _try(_stock(query, False), query, used, wd, 1)
        if a:
            return a
        a = _try(_stock(query, True), query, used, wd, 1)
        if a:
            return a
    for query in (q, alt):
        if query and any(w in query.lower() for w in SPACE_WORDS):
            a = _try(_nasa(query), query, used, wd, 1)
            if a:
                return a
    if q or alt:
        a = _try(_archive(q or alt), q or alt, used, wd, 1)
        if a:
            return a
    # Reuse only an asset whose indexed terms overlap this shot; otherwise show a contextual graphic.
    reusable = [a for a in _all if a.get("match_words", set()) & target]
    if reusable:
        a = dict(max(reusable, key=lambda item: len(item.get("match_words", set()) & target)))
        a["reused"] = True
        a["match_score"] = len(a.get("match_words", set()) & target)
        return a
    card = _context_card(q or alt, beat, setting, wd)
    credits.add("Generated context cards")
    return card

def credit_line():
    src = ", ".join(sorted(credits)) or "no external footage"
    line = f"Visual sources: {src}."
    if attributions:
        line += "\nPhotos: " + "; ".join(dict.fromkeys(attributions))[:1500] + " - via Wikimedia Commons."
    return line
