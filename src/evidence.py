"""Claim-to-source evidence validation for grounded documentary fact-checks.

This records search-linked evidence supplied by a grounded provider. It does not
claim that an automated model independently proved every statement.
"""
from urllib.parse import urlparse


def normalize_evidence(result, scene_count):
    """Return normalized evidence rows or (empty, reason) when evidence is inadequate."""
    raw = result.get("claims") if isinstance(result, dict) else None
    if not isinstance(raw, list) or not raw:
        return [], "no claim evidence returned"

    rows = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        claim = " ".join(str(item.get("claim", "")).split())
        evidence = " ".join(str(item.get("evidence", "")).split())
        source = " ".join(str(item.get("source", "")).split())
        url = str(item.get("source_url", "")).strip()
        scene = item.get("scene")
        try:
            scene_no = int(str(scene).lower().replace("scene", "").replace("_", "").strip())
        except (TypeError, ValueError):
            continue
        parsed = urlparse(url)
        if not (1 <= scene_no <= scene_count and len(claim) >= 12 and len(evidence) >= 20
                and source and parsed.scheme == "https" and parsed.netloc
                and not parsed.username and not parsed.password):
            continue
        key = (scene_no, claim.casefold(), url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "scene": scene_no,
            "claim": claim[:600],
            "evidence": evidence[:1200],
            "source": source[:200],
            "source_url": url[:2000],
            "confidence": str(item.get("confidence", "unrated"))[:40],
        })
    if not rows:
        return [], "no valid HTTPS claim-to-source rows"
    return rows, None


def evidence_record(rows, reason=None):
    """Build a public-safe status record; never call model output independently verified."""
    if rows:
        return {
            "status": "search_linked",
            "claims": rows,
            "source_count": len({r["source_url"] for r in rows}),
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
            "source": str(asset.get("source", "unknown")),
            "source_url": str(asset.get("source_url", "")),
            "license": str(asset.get("license", "unknown")),
            "license_url": str(asset.get("license_url", "")),
            "license_status": str(asset.get("license_status", "review_required")),
            "credit": str(asset.get("credit", "")),
            "illustrative": bool(asset.get("illustrative", True)),
        })
    return rows
