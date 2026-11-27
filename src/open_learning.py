"""Open educational learning and topic research leads.

This module uses public, documented metadata APIs only. Research records are leads,
not verified facts or reuse permission. Skills are short, original heuristics linked
to openly licensed source materials; they never copy creator scripts or media.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .common import ROOT

LIBRARY = ROOT / "memory" / "open_skills.json"
CACHE = ROOT / ".media-cache" / "open_learning.json"
LIBRARY_VERSION = 1
REFRESH_SECONDS = 30 * 24 * 60 * 60
CACHE_SECONDS = 7 * 24 * 60 * 60
RATE_PAUSE_SECONDS = 60 * 60
ERROR_PAUSE_SECONDS = 15 * 60
USER_AGENT = "YT-AUTO24-documentary-research/1.0 (https://github.com/sohan24com/yt-auto24)"
OPEN_LICENSES = {
    "cc0": "CC0", "cc0-1.0": "CC0", "public-domain": "Public Domain",
    "cc-by": "CC BY", "cc-by-4-0": "CC BY 4.0", "cc-by-4.0": "CC BY 4.0",
    "cc-by-sa": "CC BY-SA", "cc-by-sa-3-0": "CC BY-SA 3.0",
    "cc-by-sa-4-0": "CC BY-SA 4.0", "cc-by-sa-4.0": "CC BY-SA 4.0",
}

SKILL_RULES = (
    ("hook", ("hook", "opening", "question"),
     "Open with a specific, verifiable question or fact, then establish the documentary's central question."),
    ("story_structure", ("narrative", "story structure", "storytelling", "sequence"),
     "Arrange evidence in a clear progression; each segment should move the central question forward."),
    ("pacing", ("pacing", "rhythm", "tempo", "duration"),
     "Vary scene length with information density and emotional weight; remove pauses that add no meaning."),
    ("visual_storytelling", ("visual storytelling", "cinematography", "shot composition"),
     "Choose visuals that add evidence, place, scale, or emotion to the spoken beat; avoid decorative mismatch."),
    ("editing", ("editing", "transition", "continuity"),
     "Use a cut or transition to clarify a change in time, place, evidence, or story beat."),
    ("sound_design", ("sound design", "sound", "audio", "music"),
     "Keep narration intelligible; use music and ambience to support scene mood without masking speech."),
    ("fact_checking", ("fact-check", "fact checking", "citation", "evidence", "source evaluation"),
     "Attach important factual claims to traceable sources and distinguish evidence from interpretation."),
    ("scriptwriting", ("screenwriting", "scriptwriting", "script writing"),
     "Give each scene a narrative purpose and a specific transition into the next scene."),
)


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _load_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _get(url, params=None, timeout=12):
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", RATE_PAUSE_SECONDS)
        try:
            retry_after = int(retry_after)
        except (TypeError, ValueError):
            retry_after = RATE_PAUSE_SECONDS
        raise SourceRateLimitedError(retry_after)
    response.raise_for_status()
    return response.json()


def _safe_url(value):
    value = str(value or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    return value if value.startswith("https://") else ""


def _record(source, title, url, **extra):
    return {"source": source, "title": " ".join(str(title or "").split())[:240],
            "url": _safe_url(url), **extra}


def _confidence(source_count, concept_count):
    """Conservative provenance confidence, not a measure of technique effectiveness."""
    independent_sources = min(3, max(0, int(source_count) - 1))
    matched_concepts = min(3, max(0, int(concept_count) - 1))
    return round(min(0.7, 0.30 + independent_sources * 0.06 + matched_concepts * 0.02), 2)


def _merge_candidates(items):
    merged = {}
    for item in items:
        skill = merged.get(item["id"])
        if skill is None:
            skill = dict(item)
            skill["sources"] = [item["source"]]
            skill["matched_concepts"] = list(item["source"].get("matched_concepts", []))
            merged[item["id"]] = skill
            continue
        if all(source.get("url") != item["source"].get("url") for source in skill["sources"]):
            skill["sources"].append(item["source"])
        skill["matched_concepts"] = sorted(set(skill["matched_concepts"] +
                                                item["source"].get("matched_concepts", [])))
        refs = sorted(source.get("url", "") for source in skill["sources"])
        skill["version"] = hashlib.sha256(json.dumps([skill["id"], skill["principle"], refs],
                                                     ensure_ascii=False).encode()).hexdigest()[:12]
        skill["confidence"] = _confidence(len(skill["sources"]), len(skill["matched_concepts"]))
        skill["source_count"] = len(skill["sources"])
        skill["evidence_note"] = ("Concept matches appear in multiple openly licensed sources; this is still not outcome evidence.")
        if item["source"].get("license") == "CC0":
            skill["limitations"] = sorted(set(skill.get("limitations", []) + item.get("limitations", [])))
    for skill in merged.values():
        skill.setdefault("source_count", len(skill["sources"]))
        skill["confidence"] = _confidence(skill["source_count"], len(skill["matched_concepts"]))
        skill["confidence_basis"] = "allowlisted source count and matched concepts; not channel outcome evidence"
    return list(merged.values())


def _wikimedia_search(query, *, api_get=_get):
    data = api_get("https://en.wikipedia.org/w/api.php", {
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 0, "gsrlimit": 4, "prop": "info", "inprop": "url",
        "format": "json", "formatversion": 2,
    })
    pages = data.get("query", {}).get("pages", [])
    if isinstance(pages, dict):
        pages = list(pages.values())
    result = []
    for page in pages:
        url = _safe_url(page.get("fullurl"))
        if not url:
            title = str(page.get("title", "")).replace(" ", "_")
            url = "https://en.wikipedia.org/wiki/" + quote(title, safe="()_,-")
        result.append(_record("Wikimedia", page.get("title"), url,
                              license="CC BY-SA 4.0", license_url="https://creativecommons.org/licenses/by-sa/4.0/",
                              attribution_required=True, kind="encyclopedia_lead",
                              status="research_lead_not_fact_checked"))
    return result


def _wikiversity_skills(*, api_get=_get):
    data = api_get("https://en.wikiversity.org/w/api.php", {
        "action": "query", "list": "search",
        "gsrsearch": "documentary storytelling script cinematography editing sound design fact checking",
        "srnamespace": 0, "srlimit": 5,
        "format": "json", "formatversion": 2,
    })
    pages = data.get("query", {}).get("search", [])
    candidates = []
    for page in pages:
        title = str(page.get("title", "")).strip()
        if not title:
            continue
        page_data = api_get("https://en.wikiversity.org/w/rest.php/v1/page/"
                            + quote(title.replace(" ", "_"), safe="()_,-") + "/with_html")
        license_meta = page_data.get("license") or {}
        license_url = _safe_url(license_meta.get("url", ""))
        license_title = " ".join(str(license_meta.get("title", "")).split())
        allowed_licenses = {
            "https://creativecommons.org/licenses/by-sa/3.0/": "CC BY-SA 3.0",
            "https://creativecommons.org/licenses/by-sa/4.0/": "CC BY-SA 4.0",
            "https://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
            "https://creativecommons.org/publicdomain/zero/1.0/": "CC0",
        }
        license_name = allowed_licenses.get(license_url)
        if not license_name:
            continue
        html = str(page_data.get("html", ""))
        text = " ".join(_TextOnly().feed_text(html).split())[:18000]
        if not text:
            continue
        lower = text.casefold()
        for skill_id, terms, principle in SKILL_RULES:
            matched = [term for term in terms if term in lower]
            if not matched:
                continue
            url = _safe_url(page_data.get("html_url"))
            if not url:
                url = "https://en.wikiversity.org/wiki/" + quote(title.replace(" ", "_"), safe="()_,-")
            if not url:
                continue
            latest = page_data.get("latest") or {}
            revision = latest.get("id")
            source = _record("Wikiversity", page_data.get("title", title), url,
                             license=license_name, license_url=license_url,
                             license_label=license_title, attribution_required=True, revision_id=revision,
                             matched_concepts=matched)
            version = hashlib.sha256(json.dumps([skill_id, principle, url, revision],
                                                 ensure_ascii=False).encode()).hexdigest()[:12]
            candidates.append({"id": skill_id, "version": version, "category": skill_id,
                               "principle": principle, "confidence": _confidence(1, len(matched)),
                               "source_count": 1,
                               "status": "candidate", "source": source,
                               "evidence_note": "Source page contains matched educational concepts; this is a candidate heuristic, not outcome evidence.",
                               "limitations": ["Single openly licensed educational source", "Not yet validated on this channel"],
                               "test_result": "source_license_and_concept_match_passed"})
    return _merge_candidates(candidates)


class _TextOnly(HTMLParser):
    """Extract text only; source HTML is never stored or passed to generation providers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        if data and data.strip():
            self.parts.append(data.strip())

    def feed_text(self, value):
        self.feed(value)
        return " ".join(self.parts)


class SourceRateLimitedError(RuntimeError):
    def __init__(self, retry_after=RATE_PAUSE_SECONDS):
        super().__init__("source rate limited; pause and use cached material")
        self.retry_after = max(60, min(24 * 60 * 60, int(retry_after)))


def _abstract_text(index):
    if not isinstance(index, dict):
        return ""
    words = []
    for token, positions in index.items():
        for position in positions if isinstance(positions, list) else []:
            if isinstance(position, int) and position >= 0:
                words.append((position, token))
    return " ".join(token for _, token in sorted(words))[:18000]


def _openalex_skills(*, api_get=_get):
    data = api_get("https://api.openalex.org/works", {
        "search": "documentary storytelling education cinematography editing sound design",
        "filter": "best_oa_location.license:cc-by,cc0,cc-by-sa", "per-page": 5,
        "select": "id,doi,title,publication_year,authorships,best_oa_location,abstract_inverted_index",
    })
    candidates = []
    for work in data.get("results", []):
        location = work.get("best_oa_location") or {}
        license_id = str(location.get("license") or "").lower()
        license_name = _normalize_license(license_id)
        abstract = _abstract_text(work.get("abstract_inverted_index"))
        if not license_name or len(abstract) < 80:
            continue
        link = _safe_url(work.get("doi")) or _safe_url(location.get("landing_page_url"))
        if not link:
            continue
        title = " ".join(str(work.get("title", "")).split())[:240]
        institutions = []
        for author in work.get("authorships", [])[:8]:
            for institution in author.get("institutions", []):
                name = " ".join(str(institution.get("display_name", "")).split())
                if name and name not in institutions:
                    institutions.append(name)
        source = _record("OpenAlex", title, link, license=license_name,
                         license_url=_license_url(license_id), attribution_required=license_name != "CC0",
                         publication_year=work.get("publication_year"), institutions=institutions[:8],
                         matched_concepts=[], kind="open_licensed_educational_research",
                         status="license_metadata_allowlisted")
        lower = abstract.casefold()
        for skill_id, terms, principle in SKILL_RULES:
            matched = [term for term in terms if term in lower]
            if not matched:
                continue
            source_for_skill = dict(source, matched_concepts=matched)
            abstract_hash = hashlib.sha256(abstract.encode("utf-8")).hexdigest()
            version = hashlib.sha256(json.dumps([skill_id, principle, link, license_name, abstract_hash],
                                                 ensure_ascii=False).encode()).hexdigest()[:12]
            candidates.append({"id": skill_id, "version": version, "category": skill_id,
                               "principle": principle, "confidence": _confidence(1, len(matched)), "status": "candidate",
                               "source_count": 1,
                               "source": source_for_skill,
                               "evidence_note": "License-allowlisted open-access abstract contains matched concepts; not evidence of channel performance.",
                               "limitations": ["Abstract-level evidence only", "Not yet validated on this channel"],
                               "test_result": "oa_license_and_concept_match_passed"})
    return _merge_candidates(candidates)


def _openalex_search(query, *, api_get=_get):
    data = api_get("https://api.openalex.org/works", {
        "search": query, "filter": "best_oa_location.license:cc-by,cc0,cc-by-sa",
        "per-page": 4,
        "select": "id,doi,title,publication_year,authorships,best_oa_location,abstract_inverted_index",
    })
    result = []
    for work in data.get("results", []):
        location = work.get("best_oa_location") or {}
        license_id = str(location.get("license") or "").lower()
        license_name = _normalize_license(license_id)
        if not license_name:
            continue
        url = _safe_url(work.get("doi")) or _safe_url(location.get("landing_page_url"))
        if not url:
            continue
        institutions = []
        for auth in work.get("authorships", [])[:8]:
            for inst in auth.get("institutions", []):
                name = " ".join(str(inst.get("display_name", "")).split())
                if name and name not in institutions:
                    institutions.append(name)
        result.append(_record("OpenAlex", work.get("title"), url, license=license_name,
                              license_url=_license_url(license_id), publication_year=work.get("publication_year"),
                              institutions=institutions[:8], kind="open_access_scholarly_lead",
                              status="license_metadata_allowlisted", oa_landing_url=_safe_url(location.get("landing_page_url"))))
    return result


def _license_url(license_id):
    normalized = _normalize_license(license_id)
    slugs = {"CC0": "zero/1.0", "Public Domain": "zero/1.0", "CC BY": "licenses/by/4.0",
             "CC BY 4.0": "licenses/by/4.0", "CC BY-SA": "licenses/by-sa/4.0",
             "CC BY-SA 3.0": "licenses/by-sa/3.0",
             "CC BY-SA 4.0": "licenses/by-sa/4.0"}
    return "https://creativecommons.org/" + slugs[normalized] if normalized in slugs else ""


def _normalize_license(value):
    key = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return OPEN_LICENSES.get(key, "")


def _europe_pmc_search(query, *, api_get=_get):
    data = api_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", {
        "query": f'OPEN_ACCESS:Y AND (TITLE_ABS:"{query}")', "format": "json",
        "resultType": "core", "pageSize": 4,
    })
    result = []
    for item in data.get("resultList", {}).get("result", []):
        license_id = str(item.get("license") or "").lower()
        license_name = _normalize_license(license_id)
        # Open access alone is not proof that a paper is reusable. Keep uncategorized rights as a lead.
        source_url = ("https://europepmc.org/articles/" + quote(str(item.get("pmcid")), safe="")
                      if item.get("pmcid") else "https://europepmc.org/article/MED/" + quote(str(item.get("pmid")), safe=""))
        result.append(_record("Europe PMC", item.get("title"), source_url,
                              publication_date=item.get("firstPublicationDate", ""),
                              license=license_name or "unknown", license_url=_license_url(license_id),
                              attribution_required=bool(license_name), kind="open_access_research_lead",
                              status="license_metadata_allowlisted" if license_name else "rights_review_required"))
    return result


def _loc_search(query, *, api_get=_get):
    data = api_get("https://www.loc.gov/search/", {"q": query, "fo": "json", "c": 4})
    result = []
    for item in data.get("results", []):
        rights = item.get("rights") or item.get("rights_advisory") or item.get("rights_summary")
        if isinstance(rights, list):
            rights = "; ".join(str(part) for part in rights)
        result.append(_record("Library of Congress", item.get("title"), _safe_url(item.get("id")),
                              license="unknown", rights_advisory=str(rights or "not provided in search result")[:500],
                              kind="historical_research_lead", status="item_rights_review_required"))
    return result


def _nasa_search(query, *, api_get=_get):
    data = api_get("https://images-api.nasa.gov/search", {"q": query, "media_type": "image,video", "page_size": 4})
    result = []
    for item in data.get("collection", {}).get("items", []):
        info = (item.get("data") or [{}])[0]
        nasa_id = str(info.get("nasa_id", ""))
        page = "https://images.nasa.gov/details/" + quote(nasa_id, safe="") if nasa_id else ""
        result.append(_record("NASA Images", info.get("title"), page,
                              creator=info.get("center", "NASA"), date=info.get("date_created", ""),
                              license="NASA Media Usage Guidelines; verify item-level and third-party restrictions",
                              license_url="https://www.nasa.gov/nasa-brand-center/images-and-media/",
                              kind="visual_research_lead", status="item_rights_review_required"))
    return result


def discover_topic_sources(topic, *, api_get=_get):
    """Fetch small, cited research/visual leads; APIs failing never invent substitutes."""
    query = " ".join(str(topic or "").split())[:180]
    if not query:
        return []
    cached = _load_json(CACHE, {})
    key = hashlib.sha256(query.casefold().encode()).hexdigest()
    entry = cached.get("topic_queries", {}).get(key, {})
    if time.time() - float(entry.get("saved_at", 0) or 0) < CACHE_SECONDS:
        return list(entry.get("results", []))
    results, successful_sources = [], 0
    pause_map = cached.setdefault("paused_sources", {})
    sources = (("Wikimedia", _wikimedia_search), ("OpenAlex", _openalex_search),
               ("Europe PMC", _europe_pmc_search), ("Library of Congress", _loc_search),
               ("NASA Images", _nasa_search))
    for source_name, source_fn in sources:
        if float(pause_map.get(source_name, 0) or 0) > time.time():
            continue
        try:
            results.extend(source_fn(query, api_get=api_get))
            successful_sources += 1
        except SourceRateLimitedError as exc:
            pause_map[source_name] = time.time() + exc.retry_after
            results.append(_record(source_name, "", "", status="rate_limited_paused",
                                   retry_after_seconds=exc.retry_after))
        except Exception as exc:
            # Store only a safe category, never response bodies or credentials.
            pause_map[source_name] = time.time() + ERROR_PAUSE_SECONDS
            results.append(_record(source_name, "", "", status="temporarily_unavailable",
                                   error_type=type(exc).__name__, retry_after_seconds=ERROR_PAUSE_SECONDS))
    # No duplicates; preserve explicit license-review status.
    deduped, seen = [], set()
    for row in results:
        key_url = row.get("url", "")
        identity = key_url or (row.get("source"), row.get("status"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)
    cache_data = _load_json(CACHE, {"topic_queries": {}, "paused_sources": {}})
    cache_data.setdefault("paused_sources", {}).update(pause_map)
    if successful_sources:
        cache_data.setdefault("topic_queries", {})[key] = {"saved_at": time.time(), "results": deduped[:20]}
    _atomic_json(CACHE, cache_data)
    return deduped[:20]


def refresh_skill_library(*, force=False, api_get=_get):
    """Learn source-linked candidate heuristics from Wikiversity's CC BY-SA text."""
    library = _load_json(LIBRARY, {"schema_version": LIBRARY_VERSION, "skills": {}, "history": []})
    if (not force and time.time() - float(library.get("last_refresh", 0) or 0) < REFRESH_SECONDS):
        return library
    candidates, successful_sources, errors = [], 0, []
    for source_fn in (_wikiversity_skills, _openalex_skills):
        try:
            candidates.extend(source_fn(api_get=api_get))
            successful_sources += 1
        except Exception as exc:
            errors.append(type(exc).__name__)
    if not successful_sources:
        library["last_refresh_status"] = "temporarily_unavailable"
        library["last_error_types"] = sorted(set(errors))
        return library
    skills = library.setdefault("skills", {})
    for candidate in candidates:
        previous = skills.get(candidate["id"])
        if previous and previous.get("version") == candidate["version"]:
            continue
        if previous:
            library.setdefault("history", []).append({"id": candidate["id"], "version": previous.get("version"),
                                                       "status": previous.get("status", "candidate"),
                                                       "snapshot": previous,
                                                       "archived_at": int(time.time())})
        skills[candidate["id"]] = candidate
    library["schema_version"] = LIBRARY_VERSION
    library["last_refresh"] = int(time.time())
    library["last_refresh_status"] = "updated" if candidates else "no_eligible_material"
    library["last_error_types"] = sorted(set(errors))
    _atomic_json(LIBRARY, library)
    return library


def prompt_skills(library=None, limit=5):
    library = library or _load_json(LIBRARY, {"skills": {}})
    output = []
    for skill in library.get("skills", {}).values():
        if skill.get("status") not in ("candidate", "validated"):
            continue
        source = skill.get("source") or {}
        try:
            confidence = max(0.0, min(1.0, float(skill.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        output.append({"id": skill.get("id"), "version": skill.get("version"),
                       "category": skill.get("category"), "principle": skill.get("principle"),
                       "confidence": confidence, "status": skill.get("status"),
                       "source_title": source.get("title"), "source_url": source.get("url"),
                       "license": source.get("license"), "limitations": skill.get("limitations", [])})
        if len(output) >= limit:
            break
    return output


def rollback_skill(skill_id):
    """Roll back a source update to the most recently archived version when available."""
    library = _load_json(LIBRARY, {"schema_version": LIBRARY_VERSION, "skills": {}, "history": []})
    history = library.get("history", [])
    prior = next((row for row in reversed(history) if row.get("id") == skill_id and row.get("snapshot")), None)
    if prior is None:
        return False
    library.setdefault("skills", {})[skill_id] = prior["snapshot"]
    library["history"] = [row for row in history if row is not prior]
    _atomic_json(LIBRARY, library)
    return True
