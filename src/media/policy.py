"""Conservative rights classification; search-engine presence is not a license."""
from urllib.parse import urlparse

CC_BASE = "https://creativecommons.org/licenses/"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
PDM_URL = "https://creativecommons.org/publicdomain/mark/1.0/"
NASA_RIGHTS = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
PEXELS_RIGHTS = "https://www.pexels.com/license/"
PIXABAY_RIGHTS = "https://pixabay.com/service/license/"


def classify_license(name="", url="", rights=""):
    """Return (status, confidence, restrictions) without inferring rights from host."""
    label = " ".join(str(name or "").lower().replace("_", " ").split())
    link = str(url or "").strip().lower().rstrip("/")
    text = " ".join((label, str(rights or "").lower()))
    if "creativecommons.org/licenses/by-nc" in link or "noncommercial" in text or "non-commercial" in text:
        return "noncommercial_only", 0.95, ["not cleared for monetized/commercial use"]
    if "creativecommons.org/licenses/by-nd" in link or "no derivatives" in text:
        return "no_derivatives", 0.95, ["editing/derivative use is restricted"]
    if "creativecommons.org/licenses/by-sa" in link or "sharealike" in text or "share alike" in text:
        return "review_required", 0.85, ["ShareAlike compatibility needs editorial/legal review"]
    if link == PDM_URL.rstrip("/") or label in {"pdm", "public domain mark"}:
        return "review_required", 0.8, ["Public Domain Mark; verify jurisdiction and provenance"]
    if link == CC0_URL.rstrip("/") or label == "cc0":
        return "allowed_public_domain", 0.95, []
    if link.startswith(CC_BASE + "by/") or label in {"cc by", "attribution"} or label.startswith("cc by "):
        return "allowed_attribution", 0.9, ["retain creator and source attribution"]
    if "public domain" in str(rights or "").lower() and ("government work" in text or "u.s. government" in text):
        return "allowed_public_domain", 0.75, ["verify item-level third-party and personality rights"]
    if not link and not label and not rights:
        return "review_required", 0.0, ["license metadata missing"]
    return "review_required", 0.2, ["rights statement is not a recognized reusable license"]


def safe_https(url):
    try:
        parsed = urlparse(str(url or ""))
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def attribution_text(title, creator, source, license_name, source_url):
    parts = [part for part in (title, f"by {creator}" if creator else "", source, license_name) if part]
    line = " — ".join(parts)
    return f"{line} — {source_url}" if source_url else line
