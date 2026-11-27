"""Footage that matches what the narrator says.
One search query per SHOT (written from the sentence spoken during that shot), results re-ranked by how well
their tags/title match the query, plus real photos of real places from Wikimedia Commons (CC0 / public
domain / CC BY only, with attribution added to the description)."""
import hashlib, html, os, re, requests, time
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from urllib.parse import quote
from .media import SearchRequest, search as search_media
from .media.search import research_credits
from .media.downloader import download as safe_media_download

STOP = set("the and with for from over under into this that were was are his her their its they them have has had "
           "been will would could then than when what which while about after before video stock footage".split())
SPACE_WORDS = ("space", "planet", "star", "galaxy", "moon", "mars", "earth", "sun", "astronaut", "rocket",
               "telescope", "nebula", "satellite", "orbit", "comet", "cosmos", "universe", "solar")
UA = {"User-Agent": "yt-auto24-docbot/1.0 (https://github.com/sohan24com/yt-auto24)"}

credits = set()
attributions = []
_cache = {}
_all = []              # every downloaded asset (for last-resort reuse)
_source_counts = {}
_role_counts = {}
_coverr_attempts = [0]
_wiki_used = [0]


def reset():
    """Call at the start of every build: drop state from an earlier attempt (its files are deleted)."""
    credits.clear()
    attributions.clear()
    _all.clear()
    _cache.clear()
    _source_counts.clear()
    _role_counts.clear()
    _coverr_attempts[0] = 0
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
            r = requests.get(url, headers=headers, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(30)
                continue
            if r.ok:
                return r.json()
        except Exception:
            time.sleep(3)
    return {}


def _download(url, dest, headers=None, cache_key=None):
    try:
        from .common import cfg
        settings = cfg().get("media", {})
    except Exception:
        settings = {}
    max_bytes = max(1, int(settings.get("max_download_mb", os.getenv("MEDIA_MAX_DOWNLOAD_MB", "90")))) << 20
    cache_dir = Path(settings.get("cache_dir", ".media-cache")) / "assets"
    return safe_media_download(
        url, dest, max_bytes=max_bytes, headers=headers, cache_key=cache_key,
        cache_directory=cache_dir,
        cache_ttl_days=settings.get("asset_cache_ttl_days", 7),
        cache_max_total_bytes=int(settings.get("asset_cache_max_mb", 512)) << 20,
    )


def _stock(q, with_photos, scene_context=None):
    scene_context = scene_context or {}
    key = (q, with_photos, tuple(sorted(scene_context.items())))
    if key not in _cache:
        types = ("video", "image") if with_photos else ("video",)
        providers = ("pexels", "pixabay", "openverse", "wikimedia_commons") if with_photos else (
            "pexels", "pixabay")
        request = SearchRequest(
            topic=scene_context.get("topic", ""),
            scene_description=scene_context.get("scene_description", q),
            narration=scene_context.get("narration", ""),
            story_beat=scene_context.get("story_beat", q),
            location=scene_context.get("location", ""),
            historical_period=scene_context.get("historical_period", ""),
            mood=scene_context.get("mood", ""), required_visual=q,
            scene_duration=float(scene_context.get("scene_duration", 0) or 0),
            media_types=types, providers=providers, limit_per_source=8)
        _cache[key] = _legacy_candidates(search_media(request))
    return _cache[key]


def _legacy_candidates(assets):
    """Adapt normalized research records to the editor's stable candidate tuple."""
    candidates = []
    for asset in assets:
        kind = "video" if asset.media_type == "video" else "image" if asset.media_type == "image" else "audio"
        if kind == "audio":
            continue
        text = " ".join(filter(None, (asset.title, asset.creator,
                                     str(asset.extra.get("tags", "")))))
        metadata = {
            "asset_page": asset.source_url, "license": asset.license_name,
            "license_url": asset.license_url, "license_status": asset.license_status,
            "mime": asset.extra.get("mime", ""),
            "creator": asset.creator, "credit": asset.attribution,
            "attribution_required": asset.attribution_required,
            "authenticity": asset.authenticity, "verified_authentic": asset.verified_authentic,
            "visual_role": asset.visual_role, "mood_fit": asset.mood_fit,
            "restrictions": asset.restrictions, "width": asset.width, "height": asset.height,
            "duration": asset.duration, "quality_score": asset.quality_score,
            "relevance": asset.relevance, "direct_url": asset.direct_url,
            "download_date": asset.download_date,
        }
        candidates.append((kind, asset.asset_id, asset.direct_url, asset.source, text, metadata))
    return candidates


def _nasa(q):
    request = SearchRequest(required_visual=q, scene_description=q, story_beat=q,
                            media_types=("video", "image"), providers=("nasa",), limit_per_source=8)
    return _legacy_candidates(search_media(request))


def _coverr(q, scene_context):
    try:
        from .common import cfg
        per_build_limit = int(cfg().get("media", {}).get("coverr_max_searches_per_build", 12))
    except Exception:
        per_build_limit = 12
    if not _key("COVERR_API_KEY") or _coverr_attempts[0] >= per_build_limit:
        return []
    _coverr_attempts[0] += 1
    request = SearchRequest(required_visual=q,
                            scene_description=scene_context.get("scene_description", q),
                            story_beat=scene_context.get("story_beat", q),
                            location=scene_context.get("location", ""),
                            mood=scene_context.get("mood", ""),
                            scene_duration=float(scene_context.get("scene_duration", 0) or 0),
                            media_types=("video",), providers=("coverr",), limit_per_source=6)
    return _legacy_candidates(search_media(request))


def _archive(q):
    historical = any(word in q.lower() for word in
                     ("historical", "history", "ancient", "century", "war", "archive", "archival", "map"))
    providers = ("internet_archive", "wikimedia_commons", "library_of_congress") if historical else (
        "internet_archive",)
    request = SearchRequest(required_visual=q, scene_description=q, story_beat=q,
                            historical_period="historical archive" if historical else "",
                            media_types=("video", "image"), providers=providers, limit_per_source=5)
    return _legacy_candidates(search_media(request))


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
            extension = ".png" if ii.get("mime") == "image/png" else ".jpg"
            dest = wd / f"wiki-{pg['pageid']}{extension}"
            if url and (dest.exists() or _download(url, dest, UA, cache_key=f"commons-{pg['pageid']}")):
                pool.append({"path": dest, "kind": "image", "reused": False, "source": "Wikimedia Commons",
                             "words": _words(term + " " + title), "used": False,
                             "credit": f"{title} - {artist} ({lic}) — https://commons.wikimedia.org/wiki/{quote(title.replace(' ', '_'))}",
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
    def rank(candidate):
        metadata = candidate[5] if len(candidate) > 5 and isinstance(candidate[5], dict) else {}
        license_bonus = {"allowed_public_domain": 10, "allowed_attribution": 9, "provider_terms": 8}.get(
            metadata.get("license_status"), 0)
        role = metadata.get("visual_role", "general")
        source_penalty = min(14, _source_counts.get(candidate[3], 0) * 1.5)
        role_penalty = min(12, _role_counts.get(role, 0) * 3)
        return (_score(q, candidate[4]) * 10 + float(metadata.get("quality_score", 0) or 0) * 0.35
                + license_bonus - source_penalty - role_penalty)

    selected_urls = {a.get("direct_url") for a in _all if a.get("direct_url")}
    ranked = sorted((c for c in cands if c[1] not in used and c[2] not in selected_urls
                     and _score(q, c[4]) >= min_score),
                    key=lambda c: (-rank(c), c[0] != "video", str(c[1])))
    for candidate in ranked[:4]:
        kind, uid, url, source, match_text = candidate[:5]
        source_meta = candidate[5] if len(candidate) > 5 and isinstance(candidate[5], dict) else {}
        defaults = {
            "Pexels": ("Pexels License", "https://www.pexels.com/license/", "provider_terms"),
            "Pixabay": ("Pixabay Content License", "https://pixabay.com/service/license/", "provider_terms"),
        }
        license_name, license_url, license_status = defaults.get(source, ("unknown", "", "review_required"))
        license_name = source_meta.get("license", license_name)
        license_url = source_meta.get("license_url", license_url)
        license_status = source_meta.get("license_status", license_status)
        source_url = source_meta.get("asset_page", source_meta.get("source_url", url))
        if license_status == "review_required" or not url or not source_url or not license_url:
            continue
        mime = str(source_meta.get("mime", "")).lower()
        extension = (".mp4" if kind == "video" else
                     ".png" if mime == "image/png" else
                     ".webp" if mime == "image/webp" else ".jpg")
        dest = Path(wd) / f"{uid}{extension}"
        download_info = {"size_bytes": dest.stat().st_size} if dest.exists() else \
            _download(url, dest, cache_key=uid)
        if download_info:
            used.add(uid)
            credits.add(source)
            credit = source_meta.get("credit") or f"{match_text} ({source}; {license_name})"
            if source_meta.get("attribution_required") or license_status == "allowed_attribution" \
                    or source in ("Pexels", "Pixabay"):
                attributions.append(credit)
            a = {"path": dest, "kind": kind, "title": match_text, "reused": False, "source": source,
                 "asset_id": uid, "source_url": source_meta.get("asset_page", url),
                 "license": license_name, "license_url": license_url,
                 "license_status": license_status, "illustrative": source in ("Pexels", "Pixabay"),
                 "credit": credit, "creator": source_meta.get("creator", ""),
                 "attribution_required": source_meta.get("attribution_required", license_status == "allowed_attribution"),
                 "authenticity": source_meta.get("authenticity", "topic_matched_illustrative"),
                 "visual_role": source_meta.get("visual_role", "general"),
                 "mood_fit": source_meta.get("mood_fit", 0.5),
                 "verified_authentic": source_meta.get("verified_authentic", False),
                 "restrictions": source_meta.get("restrictions", []),
                 "width": source_meta.get("width", 0), "height": source_meta.get("height", 0),
                 "duration": source_meta.get("duration", 0), "quality_score": source_meta.get("quality_score", 0),
                 "download_date": source_meta.get("download_date", ""),
                 # Coverr returns signed URLs tied to the API key; use only in memory for the download.
                 "direct_url": "" if source == "Coverr" else url,
                 "file_size_bytes": download_info.get("size_bytes",
                    dest.stat().st_size if dest.exists() else 0)
                 if isinstance(download_info, dict) else (dest.stat().st_size if dest.exists() else 0),
                 "sha256": download_info.get("sha256", "") if isinstance(download_info, dict) else ""}
            a.update({"match_words": _words(q + " " + match_text),
                      "match_score": _score(q, match_text)})
            _source_counts[source] = _source_counts.get(source, 0) + 1
            visual_role = a.get("visual_role", "general")
            _role_counts[visual_role] = _role_counts.get(visual_role, 0) + 1
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


def gather_shot(q, alt, beat, setting, used, wd, wiki, wiki_cap=0.3, total_shots=100,
                mood="", topic="", historical_period="", scene_duration=0):
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
                _source_counts["Wikimedia Commons"] = _source_counts.get("Wikimedia Commons", 0) + 1
                _role_counts["archive"] = _role_counts.get("archive", 0) + 1
                return {"path": w["path"], "kind": "image", "reused": False, "source": "Wikimedia Commons",
                        "asset_id": w["asset_id"], "source_url": w["source_url"],
                        "license": w["license"], "license_url": w["license_url"],
                        "license_status": w["license_status"], "illustrative": False,
                        "credit": w["credit"],
                        "authenticity": "topic_matched_illustrative",
                        "verified_authentic": False,
                        "match_words": w["words"], "match_score": len(w["words"] & target)}
    setting_q = " ".join((setting or "").split()[:5])
    context = {"topic": topic, "scene_description": " ".join((q, beat)), "story_beat": beat,
               "location": setting, "historical_period": historical_period,
               "mood": mood, "scene_duration": scene_duration}
    queries = list(dict.fromkeys(x for x in (q, alt, setting_q) if x))
    # First prefer direct text/tag matches, then lower-confidence matches that still share a term.
    for query in queries:
        a = _try(_stock(query, False, context), query, used, wd, 1)
        if a:
            return a
        a = _try(_stock(query, True, context), query, used, wd, 1)
        if a:
            return a
    for query in (q, alt):
        if query and any(w in query.lower() for w in SPACE_WORDS):
            a = _try(_nasa(query), query, used, wd, 1)
            if a:
                return a
    if q or alt:
        archive_query = " ".join(x for x in (q or alt, setting_q) if x)
        a = _try(_archive(archive_query), q or alt, used, wd, 1)
        if a:
            return a
    coverr_query = q or alt
    if coverr_query:
        a = _try(_coverr(coverr_query, context), coverr_query, used, wd, 1)
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
        line += "\nMedia credits: " + "; ".join(dict.fromkeys(attributions))[:1200]
    return line


def write_credit_files(assets, directory):
    """Write structured and human-readable credits for the documentary build."""
    import json
    from pathlib import Path
    rows = research_credits(assets)
    directory = Path(directory)
    json_path = directory / "media_credits.json"
    text_path = directory / "media_credits.txt"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["Documentary media credits", ""]
    for row in rows:
        lines.append(f"- {row['title'] or row['asset_id']} — {row['creator'] or 'Creator not listed'} — "
                     f"{row['source']} — {row['license']} [{row['license_status']}] — "
                     f"{row['attribution'] or row['original_url']}")
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, text_path
