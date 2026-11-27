"""Official API adapters. Every adapter returns MediaAsset objects and fails closed."""
import html
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests

from .models import MediaAsset
from .policy import (
    NASA_RIGHTS, PEXELS_RIGHTS, PIXABAY_RIGHTS, attribution_text,
    classify_license, safe_https,
)

UA = {"User-Agent": "yt-auto24-documentary-research/1.0 (https://github.com/sohan24com/yt-auto24)"}
_LAST_CALL = {}


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _plain(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))).strip()


def url_host_is(url, domain):
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    domain = domain.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


class Provider:
    name = "provider"
    cacheable = True
    min_interval = 0.0

    def _json(self, url, *, headers=None, params=None, timeout=20):
        if not safe_https(url):
            self.last_status = "rejected_unsafe_url"
            return {}
        previous = _LAST_CALL.get(self.name, 0.0)
        delay = self.min_interval - (time.monotonic() - previous)
        if delay > 0:
            time.sleep(delay)
        _LAST_CALL[self.name] = time.monotonic()
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout,
                                    allow_redirects=False)
            self.last_status = f"http_{response.status_code}"
            if 300 <= response.status_code < 400:
                self.last_status = "rejected_redirect"
                return {}
            if response.status_code == 429:
                return {}
            response.raise_for_status()
            self.last_status = "ok"
            return response.json()
        except (requests.RequestException, ValueError, TypeError):
            self.last_status = "request_error"
            return {}

    def search(self, query, media_type="video", limit=12):
        return []


class PexelsProvider(Provider):
    name = "pexels"
    # Default public API allowance is 200/hour; this per-process spacing avoids bursts.
    min_interval = 18.1

    def search(self, query, media_type="video", limit=12):
        key = os.getenv("PEXELS_API_KEY", "").strip()
        if not key or media_type not in {"video", "image"}:
            return []
        video = media_type == "video"
        endpoint = "https://api.pexels.com/v1/videos/search" if video else "https://api.pexels.com/v1/search"
        data = self._json(endpoint, headers={"Authorization": key},
                          params={"query": query, "per_page": min(20, max(1, limit)), "orientation": "landscape"})
        out = []
        for row in data.get("videos" if video else "photos", []):
            files = row.get("video_files", []) if video else []
            if video:
                files = [f for f in files if f.get("file_type") == "video/mp4" and safe_https(f.get("link"))]
                files.sort(key=lambda f: (int(f.get("width", 0)) < 1280,
                                          abs(int(f.get("width", 0)) - 1920)))
                direct = files[0].get("link", "") if files else ""
                width, height = int((files[0] if files else {}).get("width", 0)), int((files[0] if files else {}).get("height", 0))
                duration = float(row.get("duration", 0) or 0)
            else:
                sizes = row.get("src", {})
                direct = sizes.get("large2x") or sizes.get("large") or ""
                width, height, duration = int(row.get("width", 0)), int(row.get("height", 0)), 0.0
            if not direct:
                continue
            title = _plain(row.get("alt") or row.get("url", ""))
            creator = _plain((row.get("user") or {}).get("name", ""))
            page = row.get("url", "")
            credit = attribution_text(title, creator, "Pexels", "Pexels License", page)
            out.append(MediaAsset(
                asset_id=f"pexels-{media_type}-{row.get('id', '')}", title=title, source="Pexels",
                media_type=media_type, source_url=page, direct_url=direct, creator=creator,
                license_name="Pexels License", license_url=PEXELS_RIGHTS,
                license_status="provider_terms", attribution_required=True, attribution=credit,
                restrictions=["Show a prominent Pexels link; credit photographer where possible"],
                width=width, height=height, duration=duration, download_date=_stamp(),
            ))
        return out


class PixabayProvider(Provider):
    name = "pixabay"
    min_interval = 0.65

    def search(self, query, media_type="video", limit=12):
        key = os.getenv("PIXABAY_API_KEY", "").strip()
        if not key or media_type not in {"video", "image"}:
            return []
        video = media_type == "video"
        endpoint = "https://pixabay.com/api/videos/" if video else "https://pixabay.com/api/"
        params = {"key": key, "q": query, "per_page": min(20, max(1, limit))}
        if not video:
            params.update({"image_type": "photo", "orientation": "horizontal", "min_width": 1280})
        data = self._json(endpoint, params=params)
        out = []
        for row in data.get("hits", []):
            files = row.get("videos", {}) if video else {}
            available = [files.get(size) for size in ("large", "medium", "small")
                         if files.get(size, {}).get("url")] if video else []
            available.sort(key=lambda item: (int(item.get("width", 0)) < 1280,
                                             abs(int(item.get("width", 0)) - 1920)))
            pick = available[0] if available else {}
            direct = pick.get("url", "") if video else row.get("largeImageURL", "")
            if not safe_https(direct):
                continue
            page = row.get("pageURL") or (f"https://pixabay.com/videos/id-{row.get('id')}/" if video
                                           else f"https://pixabay.com/images/id-{row.get('id')}/")
            title = _plain(row.get("tags", ""))
            creator = _plain(row.get("user", ""))
            credit = attribution_text(title, creator, "Pixabay", "Pixabay Content License", page)
            out.append(MediaAsset(
                asset_id=f"pixabay-{media_type}-{row.get('id', '')}", title=title, source="Pixabay",
                media_type=media_type, source_url=page, direct_url=direct, creator=creator,
                license_name="Pixabay Content License", license_url=PIXABAY_RIGHTS,
                license_status="provider_terms", attribution_required=False, attribution=credit,
                restrictions=["API results must be cached for at least 24 hours", "No systematic mass downloads"],
                width=int(pick.get("width", row.get("imageWidth", 0)) or 0),
                height=int(pick.get("height", row.get("imageHeight", 0)) or 0),
                duration=float(row.get("duration", 0) or 0), download_date=_stamp(),
            ))
        return out


class CoverrProvider(Provider):
    """Coverr's official free-video API; premium/sponsored entries are excluded."""
    name = "coverr"
    cacheable = False  # responses contain signed URLs associated with the API key
    min_interval = 73.0  # free Demo quota: 50 requests/hour

    def search(self, query, media_type="video", limit=8):
        key = os.getenv("COVERR_API_KEY", "").strip()
        self.last_status = "disabled_no_key" if not key else "no_results"
        if not key or media_type != "video":
            return []
        data = self._json("https://api.coverr.co/videos", headers={"x-api-key": key, **UA}, params={
            "query": query, "page_size": min(10, max(1, limit)), "urls": "true",
            "filter": "is_premium:false,is_ai_generated:false",
        })
        rows = data.get("hits") or data.get("videos") or []
        out = []
        for row in rows:
            if row.get("is_premium") is True or row.get("is_sponsored") or row.get("sponsored"):
                continue
            urls = row.get("urls", {})
            direct = urls.get("mp4_download") or ""
            if not safe_https(direct) or "storage.coverr.co/" not in direct:
                continue
            title = _plain(row.get("title", ""))
            creator = _plain(row.get("creator") or row.get("user", {}).get("name", ""))
            asset_id = str(row.get("id", ""))
            slug = row.get("slug") or asset_id
            page = row.get("url") or f"https://coverr.co/videos/{quote(str(slug))}"
            if not safe_https(page):
                continue
            ai_generated = bool(row.get("is_ai_generated", False))
            authenticity = "ai_generated_reconstruction" if ai_generated else "topic_matched_illustrative"
            restrictions = ["Free API apps must credit Coverr and listed creators",
                            "Do not use Coverr content or metadata for AI training"]
            if row.get("is_premium"):
                restrictions.append("Premium content excluded")
            credit = attribution_text(title, creator, "Coverr", "Coverr License", page)
            out.append(MediaAsset(
                asset_id=f"coverr-{asset_id}", title=title, source="Coverr",
                media_type="video", source_url=page, direct_url=direct, creator=creator,
                license_name="Coverr Stock Library License", license_url="https://coverr.co/license",
                license_status="provider_terms", attribution_required=True, attribution=credit,
                restrictions=restrictions, authenticity=authenticity,
                width=int(row.get("max_width", 0) or 0), height=int(row.get("max_height", 0) or 0),
                duration=float(row.get("duration", 0) or 0), download_date=_stamp(),
                cacheable=False, extra={"tags": row.get("tags", []), "is_ai_generated": ai_generated},
            ))
        return out


class CommonsProvider(Provider):
    name = "wikimedia_commons"
    min_interval = 1.0

    def search(self, query, media_type="video", limit=12):
        expected = {"video": ("video/mp4", "video/webm"),
                    "image": ("image/jpeg", "image/png", "image/webp"),
                    "audio": ("audio/mpeg", "audio/ogg", "audio/wav", "audio/flac")}.get(media_type)
        if not expected:
            return []
        data = self._json("https://commons.wikimedia.org/w/api.php", headers=UA, params={
            "action": "query", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"filetype:{media_type} {query}", "gsrlimit": min(20, limit),
            "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1920,
            "format": "json", "formatversion": 2,
        })
        out = []
        for row in (data.get("query") or {}).get("pages", []):
            info = (row.get("imageinfo") or [{}])[0]
            mime = str(info.get("mime", "")).lower()
            direct = (info.get("thumburl") if media_type == "image" else info.get("url", "")) or info.get("url", "")
            if mime not in expected or not safe_https(direct):
                continue
            metadata = info.get("extmetadata", {})
            title = row.get("title", "").removeprefix("File:")
            creator = _plain((metadata.get("Artist") or {}).get("value"))
            license_name = _plain((metadata.get("LicenseShortName") or {}).get("value"))
            license_url = _plain((metadata.get("LicenseUrl") or {}).get("value"))
            status, _, restrictions = classify_license(license_name, license_url)
            if media_type == "video" and not mime.startswith("video/") or media_type == "image" and not mime.startswith("image/"):
                continue
            page = (metadata.get("ObjectName") or {}).get("value") or ""
            page = _plain(page)
            if not page.startswith("https://commons.wikimedia.org/"):
                page = "https://commons.wikimedia.org/wiki/" + quote(row.get("title", ""))
            credit = attribution_text(title, creator, "Wikimedia Commons", license_name, page)
            out.append(MediaAsset(
                asset_id=f"commons-{row.get('pageid', '')}", title=title, source="Wikimedia Commons",
                media_type=media_type, source_url=page, direct_url=direct, creator=creator,
                license_name=license_name or "unknown", license_url=license_url,
                license_status=status, attribution_required=True, attribution=credit,
                restrictions=restrictions,
                width=int(info.get("width", 0) or 0), height=int(info.get("height", 0) or 0),
                download_date=_stamp(), extra={"mime": info.get("thumbmime") or mime,
                                              "sha1": info.get("sha1", "")},
            ))
        return out


class OpenverseProvider(Provider):
    name = "openverse"
    min_interval = 0.5

    def search(self, query, media_type="image", limit=12):
        if media_type not in {"image", "audio"}:
            return []
        data = self._json(f"https://api.openverse.org/v1/{media_type}s/", headers=UA,
                          params={"q": query, "page_size": min(20, limit), "mature": "false"})
        out = []
        for row in data.get("results", []):
            direct = row.get("url", "")
            page = row.get("foreign_landing_url") or row.get("detail_url", "")
            if not safe_https(direct) or not safe_https(page):
                continue
            license_name = str(row.get("license", "unknown")).upper()
            version = str(row.get("license_version", "")).strip()
            license_url = str(row.get("license_url", ""))
            status, _, restrictions = classify_license(license_name, license_url)
            title = _plain(row.get("title", ""))
            creator = _plain(row.get("creator", ""))
            credit = _plain(row.get("attribution", "")) or attribution_text(
                title, creator, "Openverse", f"{license_name} {version}".strip(), page)
            out.append(MediaAsset(
                asset_id=f"openverse-{row.get('id', '')}", title=title, source="Openverse",
                media_type=media_type, source_url=page, direct_url=direct, creator=creator,
                license_name=f"{license_name} {version}".strip(), license_url=license_url,
                license_status=status, attribution_required=license_name not in {"CC0", "PDM"},
                attribution=credit, restrictions=restrictions + ["Verify license and original source record before use"],
                width=int(row.get("width", 0) or 0), height=int(row.get("height", 0) or 0),
                duration=float(row.get("duration", 0) or 0), download_date=_stamp(),
                extra={"provider": row.get("provider", ""), "filetype": row.get("filetype", "")},
            ))
        return out


class NasaProvider(Provider):
    name = "nasa"
    min_interval = 0.3

    def search(self, query, media_type="video", limit=10):
        if media_type not in {"video", "image"}:
            return []
        data = self._json("https://images-api.nasa.gov/search", headers=UA,
                          params={"q": query, "media_type": media_type, "page_size": min(20, limit)})
        out = []
        for row in ((data.get("collection") or {}).get("items") or []):
            fields = (row.get("data") or [{}])[0]
            nasa_id = str(fields.get("nasa_id", ""))
            title = _plain(fields.get("title", ""))
            description = _plain(fields.get("description", ""))
            page = f"https://images.nasa.gov/details/{quote(nasa_id)}" if nasa_id else ""
            third_party = bool(re.search(r"copyright|courtesy of|©", description, re.I))
            # General NASA guidance is not item-level copyright clearance.
            status = "review_required"
            restrictions = ["No NASA endorsement implied", "Check identifiable-person/privacy rights"]
            if third_party:
                restrictions.append("Possible third-party copyright: obtain rights-holder permission")
            direct = ""
            asset_manifest_url = row.get("href", "")
            if (not third_party and safe_https(asset_manifest_url)
                    and url_host_is(asset_manifest_url, "nasa.gov")):
                files = self._json(asset_manifest_url, headers=UA)
                suffix = ".jpg" if media_type == "image" else ".mp4"
                candidates = [url for url in files if str(url).lower().endswith(suffix)
                              and safe_https(url) and not any(mark in str(url).lower()
                                                             for mark in ("~thumb", "~small"))]
                candidates.sort(key=lambda url: 0 if "~orig" in url or "~large" in url else
                                1 if "~medium" in url else 2)
                direct = candidates[0] if candidates else ""
            out.append(MediaAsset(
                asset_id=f"nasa-{nasa_id}", title=title, source="NASA",
                media_type=media_type, source_url=page, direct_url=direct,
                creator=_plain(fields.get("photographer") or fields.get("center") or "NASA"),
                license_name="NASA media usage guidelines", license_url=NASA_RIGHTS,
                license_status=status, attribution_required=True,
                attribution=attribution_text(title, "NASA", "NASA", "NASA media usage guidelines", page),
                restrictions=restrictions, authenticity="topic_matched_illustrative",
                duration=float(fields.get("duration", 0) or 0), download_date=_stamp(),
                extra={"center": fields.get("center", ""), "date_created": fields.get("date_created", ""),
                       "description_excerpt": description[:500], "rights_checked": not third_party},
            ))
        return out


class InternetArchiveProvider(Provider):
    name = "internet_archive"
    cacheable = False
    min_interval = 0.5

    def search(self, query, media_type="video", limit=10):
        if media_type not in {"video", "image", "audio"}:
            return []
        mediatype = {"video": "movies", "image": "image", "audio": "audio"}[media_type]
        data = self._json("https://archive.org/advancedsearch.php", headers=UA, params={
            "q": f"({query}) AND mediatype:{mediatype}",
            "fl[]": ["identifier", "title", "creator", "license", "licenseurl", "rights", "year"],
            "rows": min(20, limit), "output": "json", "sort[]": "downloads desc",
        })
        out = []
        for row in ((data.get("response") or {}).get("docs") or []):
            ident = str(row.get("identifier", ""))
            if not ident:
                continue
            metadata = self._json(f"https://archive.org/metadata/{quote(ident)}", headers=UA)
            meta = metadata.get("metadata", {})
            license_name = _plain(meta.get("license") or row.get("license") or meta.get("rights") or row.get("rights"))
            license_url = str(meta.get("licenseurl") or row.get("licenseurl") or "")
            status, _, restrictions = classify_license(license_name, license_url)
            files = metadata.get("files", [])
            extensions = {"video": (".mp4", ".webm"), "image": (".jpg", ".jpeg", ".png"),
                          "audio": (".mp3", ".wav", ".ogg", ".flac")}[media_type]
            eligible = [item for item in files if str(item.get("name", "")).lower().endswith(extensions)
                        and str(item.get("size", "")).isdigit()
                        and 1024 < int(item["size"]) <= 90 << 20]
            eligible.sort(key=lambda item: int(item.get("size", 0)), reverse=True)
            filename = eligible[0].get("name", "") if eligible else ""
            direct = f"https://archive.org/download/{quote(ident)}/{quote(filename)}" if filename else ""
            if not direct or not safe_https(direct):
                continue
            title = _plain(meta.get("title") or row.get("title") or ident)
            page = f"https://archive.org/details/{quote(ident)}"
            creator = _plain(meta.get("creator") or row.get("creator") or "")
            credit = attribution_text(title, creator, "Internet Archive", license_name, page)
            out.append(MediaAsset(
                asset_id=f"ia-{ident}", title=title, source="Internet Archive",
                media_type=media_type, source_url=page, direct_url=direct, creator=creator,
                license_name=license_name or "unknown", license_url=license_url,
                license_status=status, attribution_required=status == "allowed_attribution",
                attribution=credit, restrictions=restrictions,
                authenticity="historical_archive", download_date=_stamp(), cacheable=False,
                extra={"year": meta.get("year") or row.get("year"), "file_size": int(eligible[0]["size"]),
                       "collection": meta.get("collection", [])},
            ))
        return out


class LocProvider(Provider):
    name = "library_of_congress"
    min_interval = 0.5

    def search(self, query, media_type="image", limit=10):
        data = self._json("https://www.loc.gov/search/", headers=UA,
                          params={"fo": "json", "q": query, "c": min(20, limit)})
        out = []
        for row in data.get("results", []):
            formats = " ".join(str(item) for item in row.get("online_format", [])).lower()
            if media_type == "video" and not any(word in formats for word in ("video", "film")):
                continue
            if media_type == "audio" and "audio" not in formats and "sound" not in formats:
                continue
            if media_type == "image" and any(word in formats for word in ("video", "film", "audio", "sound")):
                continue
            page = row.get("url", "")
            images = row.get("image_url", [])
            # LOC search results can expose a poster image for film/audio records;
            # never label that thumbnail as a downloadable video/audio asset.
            direct = images[0] if media_type == "image" and isinstance(images, list) and images else ""
            title = _plain(row.get("title", ""))
            rights = _plain(" ".join(row.get("rights", []) if isinstance(row.get("rights"), list)
                                       else [row.get("rights", "")]))
            status, _, restrictions = classify_license(rights=rights)
            creator = _plain(row.get("contributor", ""))
            license_url = page if safe_https(page) else ""
            out.append(MediaAsset(
                asset_id=f"loc-{row.get('id', page)}", title=title, source="Library of Congress",
                media_type=media_type, source_url=page, direct_url=direct if safe_https(direct) else "",
                creator=creator, license_name=rights or "Rights statement missing", license_url=license_url,
                license_status=status, attribution_required=True,
                attribution=attribution_text(title, creator, "Library of Congress", rights, page),
                restrictions=restrictions, authenticity="historical_archive",
                download_date=_stamp(), extra={"date": row.get("date", ""), "format": row.get("format", [])},
            ))
        return out


class NaraProvider(Provider):
    """Read-only catalog lookup. NARA terms prohibit caching/storing API response content."""
    name = "national_archives"
    cacheable = False
    min_interval = 0.3

    def search(self, query, media_type="video", limit=8):
        key = os.getenv("NARA_CATALOG_API_KEY", "").strip()
        if not key:
            return []
        data = self._json("https://catalog.archives.gov/api/v2/records/search", headers={**UA, "x-api-key": key},
                          params={"q": query, "rows": min(10, limit)})
        hits = ((data.get("body") or {}).get("hits") or {}).get("hits", [])
        out = []
        for hit in hits:
            row = hit.get("_source", hit)
            naid = str(row.get("naId") or row.get("naid") or row.get("id") or "")
            if not naid:
                continue
            page = f"https://catalog.archives.gov/id/{quote(naid)}"
            title = _plain(row.get("title", ""))
            rights = _plain(row.get("rights", ""))
            status, _, restrictions = classify_license(rights=rights)
            # Catalog search metadata alone cannot certify a file or authorize its download.
            out.append(MediaAsset(
                asset_id=f"nara-{naid}", title=title, source="National Archives Catalog",
                media_type=media_type, source_url=page, direct_url="",
                creator=_plain(row.get("creator", "")), license_name=rights or "Item-level rights review required",
                license_url=page, license_status="review_required", attribution_required=True,
                attribution=attribution_text(title, "", "National Archives Catalog", rights, page),
                restrictions=restrictions + ["NARA metadata is not a reuse license; inspect item-level rights"],
                authenticity="historical_archive", download_date=_stamp(), cacheable=False,
            ))
        return out


PROVIDERS = {
    provider.name: provider for provider in (
        PexelsProvider(), PixabayProvider(), CoverrProvider(), CommonsProvider(), OpenverseProvider(),
        NasaProvider(), InternetArchiveProvider(), LocProvider(), NaraProvider(),
    )
}
