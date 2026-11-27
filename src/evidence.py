"""Claim-to-source evidence validation for grounded documentary fact-checks.

This records search-linked evidence supplied by a grounded provider. It does not
claim that an automated model independently proved every statement.
"""
from urllib.parse import urldefrag, urlparse


_COMPOUND_SUFFIXES = {"co.uk", "org.uk", "gov.uk", "com.au", "net.au", "co.nz", "co.jp",
                      "com.br", "com.bd", "co.in", "com.sg", "com.mx", "com.tr"}


def _canonical_url(value):
    parsed = urlparse(str(value or "").strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password):
        return ""
    return urldefrag(parsed.geturl()).url.rstrip("/").casefold()


def _domain(value):
    host = (urlparse(value).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) < 2:
        return host
    suffix = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if suffix in _COMPOUND_SUFFIXES and len(parts) >= 3 else suffix


def normalize_evidence(result, scene_count, min_sources=2, min_scenes=8):
    """Accept only corroborated claims whose URLs came from provider grounding metadata."""
    raw = result.get("claims") if isinstance(result, dict) else None
    if not isinstance(raw, list) or not raw:
        return [], "no claim evidence returned"

    grounded = {
        _canonical_url(source.get("url", source.get("uri", "")))
        for source in result.get("_grounding_sources", []) if isinstance(source, dict)
    } if isinstance(result.get("_grounding_sources"), list) else set()
    grounded.discard("")
    if not grounded:
        return [], "provider returned no machine-readable grounded source URLs"

    rows = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        claim = " ".join(str(item.get("claim", "")).split())
        evidence = " ".join(str(item.get("evidence", "")).split())
        source = " ".join(str(item.get("source", "")).split())
        url = _canonical_url(item.get("source_url", ""))
        source_rows = item.get("sources", [])
        if not isinstance(source_rows, list):
            source_rows = []
        if source and url:
            source_rows = [{"source": source, "url": url, "evidence": evidence,
                            "publication_date": item.get("publication_date", ""),
                            "reliability": item.get("reliability", "unknown")}] + source_rows
        scene = item.get("scene")
        try:
            scene_no = int(str(scene).lower().replace("scene", "").replace("_", "").strip())
        except (TypeError, ValueError):
            continue
        if not (1 <= scene_no <= scene_count and len(claim) >= 12):
            continue
        normalized_sources, domains = [], set()
        for supporting in source_rows:
            if not isinstance(supporting, dict):
                continue
            source_name = " ".join(str(supporting.get("source", supporting.get("name", ""))).split())
            source_url = _canonical_url(supporting.get("url", supporting.get("source_url", "")))
            support = " ".join(str(supporting.get("evidence", "")).split())
            if (not source_name or source_url not in grounded or len(support) < 20):
                continue
            domain = _domain(source_url)
            if not domain or domain in domains:
                continue
            domains.add(domain)
            normalized_sources.append({
                "source": source_name[:200], "url": source_url[:2000], "evidence": support[:1200],
                "publication_date": " ".join(str(supporting.get("publication_date", "")).split())[:80],
                "reliability": " ".join(str(supporting.get("reliability", "unknown")).split())[:80],
            })
        if len(normalized_sources) < max(2, int(min_sources)):
            continue
        primary = normalized_sources[0]
        conflicts = item.get("conflicts", [])
        if not isinstance(conflicts, list):
            conflicts = []
        key = (scene_no, claim.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "scene": scene_no,
            "claim": claim[:600],
            "evidence": primary["evidence"],
            "source": primary["source"],
            "source_url": primary["url"],
            "sources": normalized_sources,
            "conflicts": [" ".join(str(conflict).split())[:600] for conflict in conflicts[:8]
                          if str(conflict).strip()],
            "confidence": str(item.get("confidence", "unrated"))[:40],
        })
    if not rows:
        return [], "no claims had two independent machine-grounded source domains"
    required_scenes = min(int(scene_count), max(1, int(min_scenes)))
    if len({row["scene"] for row in rows}) < required_scenes:
        return [], f"claim evidence covers fewer than {required_scenes} story scenes"
    return rows, None


def evidence_record(rows, reason=None):
    """Build a public-safe status record; never call model output independently verified."""
    if rows:
        conflicts = [row for row in rows if row.get("conflicts")]
        source_domains = {_domain(source["url"]) for row in rows for source in row.get("sources", [])}
        return {
            "status": "search_linked_conflict" if conflicts else "search_linked",
            "claims": rows,
            "source_count": len(source_domains),
            "corroborated_claim_count": len(rows),
            "conflict_count": len(conflicts),
            "warning": "Search-linked model evidence; editorial review is still required.",
        }
    return {
        "status": "not_verified",
        "claims": [],
        "source_count": 0,
        "warning": "Automated search evidence is unavailable or incomplete; review claims before publishing.",
        "reason": str(reason or "insufficient evidence")[:240],
    }


def manifest_rows(assets):
    """Keep asset provenance/licensing fields in a JSON-serializable manifest."""
    rows = []
    for asset in assets or []:
        rows.append({
            "asset_id": str(asset.get("asset_id", "")),
            "title": str(asset.get("title", "")),
            "creator": str(asset.get("creator", "")),
            "source": str(asset.get("source", "unknown")),
            "source_url": str(asset.get("source_url", "")),
            "direct_media_url": str(asset.get("direct_media_url", asset.get("direct_url", ""))),
            "license": str(asset.get("license", "unknown")),
            "license_url": str(asset.get("license_url", "")),
            "license_status": str(asset.get("license_status", "review_required")),
            "credit": str(asset.get("credit", "")),
            "attribution_required": bool(asset.get("attribution_required", False)),
            "restrictions": list(asset.get("restrictions", [])),
            "authenticity": str(asset.get("authenticity", "topic_matched_illustrative")),
            "verified_authentic": bool(asset.get("verified_authentic", False)),
            "quality_score": float(asset.get("quality_score", 0) or 0),
            "download_date": str(asset.get("download_date", "")),
            "width": int(asset.get("width", 0) or 0),
            "height": int(asset.get("height", 0) or 0),
            "duration": float(asset.get("duration", 0) or 0),
            "file_size_bytes": int(asset.get("file_size_bytes", 0) or 0),
            "sha256": str(asset.get("sha256", "")),
            "illustrative": bool(asset.get("illustrative", True)),
        })
    return rows
