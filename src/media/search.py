"""Intent-aware, bounded cross-library search and candidate scoring."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
import re

from .cache import SearchCache
from .models import MediaAsset, asset_from_dict
from .providers import PROVIDERS

STOP = set("the and with for from over under into this that were was are his her their its they them have has had "
           "been will would could then than when what which while about after before video footage scene documentary".split())
_SEARCH_CACHE = None
EXPANSIONS = {
    "deep ocean": ["deep sea trench underwater ROV", "abyssal ocean floor marine life", "deep sea research expedition"],
    "ancient civilization": ["archaeological excavation ancient ruins", "ancient map artifacts inscriptions"],
    "climate change": ["satellite earth climate glaciers", "drought forest fire coastal flooding"],
    "space exploration": ["NASA spacecraft rocket launch", "planetary mission satellite orbit"],
    "industrial revolution": ["historic factory machinery archival", "industrial city railway steam engine"],
}


@dataclass
class SearchRequest:
    topic: str = ""
    scene_description: str = ""
    narration: str = ""
    story_beat: str = ""
    location: str = ""
    historical_period: str = ""
    mood: str = ""
    required_visual: str = ""
    scene_duration: float = 0.0
    media_types: tuple = ("video", "image")
    limit_per_source: int = 8
    providers: tuple = ()
    minimum_resolution: int = 720
    allow_review_required: bool = True
    context: dict = field(default_factory=dict)


def _tokens(text):
    return {word for word in re.findall(r"[a-z0-9]{3,}", str(text or "").lower()) if word not in STOP}


def build_queries(request):
    """Make a few scene-grounded queries; do not expand via generic thesaurus spam."""
    primary = " ".join((request.required_visual or request.scene_description or request.topic).split()[:10]).strip()
    location_period = " ".join(x.strip() for x in (request.location, request.historical_period) if x.strip())
    queries = []
    if primary:
        queries.append(" ".join(x for x in (primary, location_period) if x).strip())
    corpus = " ".join((request.topic, request.required_visual, request.scene_description, request.story_beat)).lower()
    for trigger, variants in EXPANSIONS.items():
        if trigger in corpus:
            queries.extend(variants[:2])
            break
    beat = " ".join((request.story_beat or "").split()[:8])
    if beat:
        queries.append(" ".join(x for x in (beat, location_period) if x).strip())
    unique = []
    seen = set()
    for query in queries:
        query = " ".join(query.split())
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            unique.append(query)
    return unique[:3]


def _relevance(asset, request):
    intent = _tokens(" ".join((request.required_visual, request.scene_description,
                                request.story_beat, request.topic, request.location,
                                request.historical_period)))
    text = _tokens(" ".join((asset.title, asset.creator, str(asset.extra.get("tags", "")))))
    if not intent:
        return 0.0
    return min(1.0, len(intent & text) / max(2, min(6, len(intent))))


def _visual_role(asset):
    text = (asset.title + " " + " ".join(map(str, asset.extra.get("tags", [])))).lower()
    for role, terms in (
        ("aerial", ("aerial", "drone", "satellite view")),
        ("map", ("map", "atlas", "chart", "cartography")),
        ("detail", ("detail", "close up", "macro", "artifact", "document")),
        ("wide", ("panorama", "landscape", "wide shot", "establishing")),
        ("environment", ("underwater", "ocean", "forest", "cityscape", "environment")),
        ("archive", ("archival", "historic", "historical", "vintage", "film")),
        ("evidence", ("diagram", "blueprint", "record", "letter", "photograph")),
    ):
        if any(term in text for term in terms):
            return role
    return "general"


def score_asset(asset, request):
    asset.relevance = round(_relevance(asset, request), 4)
    asset.scene_match = asset.relevance
    max_dimension = max(asset.width, asset.height)
    asset.quality = min(1.0, max_dimension / max(1, request.minimum_resolution)) if max_dimension else 0.35
    if asset.duration and request.scene_duration:
        ratio = asset.duration / request.scene_duration
        asset.motion_suitability = 1.0 if 0.5 <= ratio <= 2.0 else max(0.1, 1.0 - abs(ratio - 1.0) / 4)
        asset.cinematic_usefulness = min(1.0, asset.quality * 0.65 + asset.motion_suitability * 0.35)
    elif asset.media_type == "video":
        asset.motion_suitability = 0.8
        asset.cinematic_usefulness = min(1.0, asset.quality * 0.75 + 0.2)
    else:
        asset.motion_suitability = 0.25
        asset.cinematic_usefulness = min(1.0, asset.quality * 0.8 + 0.1)
    asset.license_confidence = {"allowed_public_domain": 1.0, "allowed_attribution": 0.95,
                                "provider_terms": 0.9, "review_required": 0.3,
                                "noncommercial_only": 0.1, "no_derivatives": 0.0}.get(asset.license_status, 0.0)
    authenticity = {"verified_authentic_footage": 1.0, "historical_archive": 0.85,
                    "topic_matched_illustrative": 0.55, "ai_generated_reconstruction": 0.2}.get(asset.authenticity, 0.4)
    duration_fit = 0.6
    if asset.duration and request.scene_duration:
        duration_fit = max(0.0, 1.0 - abs(asset.duration - request.scene_duration) / max(request.scene_duration, 1))
    asset.quality_score = round(100 * (
        0.30 * asset.relevance + 0.18 * asset.scene_match + 0.12 * asset.quality +
        0.08 * duration_fit + 0.10 * asset.cinematic_usefulness +
        0.12 * asset.license_confidence + 0.05 * authenticity +
        0.03 * asset.diversity + 0.02 * asset.download_reliability
    ), 2)
    mood_terms = _tokens(request.mood)
    asset.mood_fit = 0.5 if not mood_terms else (1.0 if mood_terms & _tokens(asset.title) else 0.35)
    asset.visual_role = _visual_role(asset)
    asset.quality_score = round(asset.quality_score * 0.97 + 3 * asset.mood_fit, 2)
    return asset


def _historical_intent(request):
    text = " ".join((request.topic, request.scene_description, request.story_beat,
                     request.historical_period)).lower()
    return any(word in text for word in ("history", "historical", "ancient", "century", "war",
                                          "archive", "archival", "civilization", "revolution", "map"))


def choose_providers(request):
    chosen = set(request.providers or ("pexels", "pixabay", "openverse"))
    corpus = " ".join((request.topic, request.scene_description, request.story_beat,
                       request.required_visual)).lower()
    if request.media_types and any(item in request.media_types for item in ("video", "image")):
        if any(cue in corpus for cue in ("space", "planet", "rocket", "satellite", "astronomy", "climate", "earth")):
            chosen.add("nasa")
    if _historical_intent(request):
        chosen.update(("internet_archive", "wikimedia_commons", "library_of_congress"))
        if __import__("os").getenv("NARA_CATALOG_API_KEY", "").strip():
            chosen.add("national_archives")
    if "audio" in request.media_types:
        chosen.update(("wikimedia_commons", "openverse"))
    return tuple(sorted(name for name in chosen if name in PROVIDERS))


def search(request, cache=None):
    """Search official adapters in bounded parallel; provider errors fall through cleanly."""
    global _SEARCH_CACHE
    if cache is None and _SEARCH_CACHE is None:
        try:
            from ..common import cfg
            settings = cfg().get("media", {})
            _SEARCH_CACHE = SearchCache(
                directory=settings.get("cache_dir"),
                ttl_seconds=settings.get("cache_ttl_seconds", 86400),
                max_bytes=int(settings.get("cache_max_mb", 16)) << 20,
            )
        except Exception:
            _SEARCH_CACHE = SearchCache()
    cache = cache or _SEARCH_CACHE
    provider_names = choose_providers(request)
    all_assets, seen = [], set()
    diagnostics = request.context.setdefault("source_diagnostics", [])
    queries = build_queries(request) or [request.topic]
    for media_type in request.media_types:
        for query in queries:
            tasks = []
            with ThreadPoolExecutor(max_workers=min(6, max(1, len(provider_names)))) as pool:
                for name in provider_names:
                    provider = PROVIDERS[name]
                    cached = cache.get(name, media_type, query) if provider.cacheable else None
                    if cached is not None:
                        tasks.append((name, None, cached))
                    else:
                        tasks.append((name, pool.submit(provider.search, query, media_type,
                                                        request.limit_per_source), None))
                for name, future, cached in tasks:
                    try:
                        results = [asset_from_dict(item) for item in cached] if cached is not None else future.result()
                    except Exception:
                        results = []
                    if (future is not None and PROVIDERS[name].cacheable
                            and getattr(PROVIDERS[name], "last_status", "") == "ok"):
                        cache.put(name, media_type, query, [item.to_dict() for item in results])
                    for asset in results:
                        if not asset.source_url:
                            continue
                        key = (asset.asset_id, asset.direct_url or asset.source_url)
                        if key in seen:
                            continue
                        seen.add(key)
                        score_asset(asset, request)
                        if not request.allow_review_required and not asset.publish_ready:
                            continue
                        all_assets.append(asset)
                    diagnostics.append({"source": name,
                                        "status": "cache_hit" if cached is not None else
                                                 getattr(PROVIDERS[name], "last_status", "no_results"),
                                        "media_type": media_type, "query": query[:120],
                                        "result_count": len(results)})
            # Fetch fallback phrases only when the current phrase did not find enough scene-relevant options.
            if sum(item.relevance >= 0.15 for item in all_assets) >= request.limit_per_source:
                break
    all_assets.sort(key=lambda item: (-item.quality_score, item.source.casefold(), item.asset_id))
    return all_assets


def select_diverse(assets, limit=12, max_per_source=3, allowed_statuses=None):
    """Select useful, non-duplicate candidates while limiting one provider's visual dominance."""
    allowed = set(allowed_statuses or ("provider_terms", "allowed_public_domain", "allowed_attribution"))
    selected, sources, identities = [], {}, set()
    for asset in sorted(assets, key=lambda item: -item.quality_score):
        if asset.license_status not in allowed or not asset.direct_url:
            continue
        identities_for_asset = {value for value in (asset.asset_id, asset.direct_url) if value}
        if identities_for_asset & identities or sources.get(asset.source, 0) >= max_per_source:
            continue
        identities.update(identities_for_asset)
        sources[asset.source] = sources.get(asset.source, 0) + 1
        selected.append(asset)
        if len(selected) >= limit:
            break
    return selected


def search_audio(request, limit=12, providers=("openverse", "wikimedia_commons")):
    """Find license-screened sound options; production soundtrack selection stays opt-in."""
    audio_request = SearchRequest(
        topic=request.topic, scene_description=request.scene_description, narration=request.narration,
        story_beat=request.story_beat, location=request.location, historical_period=request.historical_period,
        mood=request.mood, required_visual=request.required_visual, scene_duration=request.scene_duration,
        media_types=("audio",), limit_per_source=request.limit_per_source, providers=tuple(providers),
        minimum_resolution=0, allow_review_required=True, context=dict(request.context),
    )
    return select_diverse(search(audio_request), limit=limit, max_per_source=limit)


def research_credits(assets):
    """Structured, reviewable credit rows for the render manifest and video description."""
    rows, seen = [], set()
    for asset in assets or []:
        if isinstance(asset, dict):
            row = {
                "asset_id": str(asset.get("asset_id", "")), "title": str(asset.get("title", "")),
                "creator": str(asset.get("creator", "")), "source": str(asset.get("source", "unknown")),
                "original_url": str(asset.get("source_url", "")),
                "direct_media_url": str(asset.get("direct_url", "")),
                "license": str(asset.get("license", "unknown")),
                "license_url": str(asset.get("license_url", "")),
                "license_status": str(asset.get("license_status", "review_required")),
                "attribution_required": bool(asset.get("attribution_required", False)),
                "attribution": str(asset.get("credit", asset.get("attribution", ""))),
                "restrictions": list(asset.get("restrictions", [])),
                "download_date": str(asset.get("download_date", "")),
                "authenticity": str(asset.get("authenticity", "topic_matched_illustrative")),
                "verified_authentic": bool(asset.get("verified_authentic", False)),
                "quality_score": float(asset.get("quality_score", 0) or 0),
                "mood_fit": float(asset.get("mood_fit", 0.5) or 0),
                "visual_role": str(asset.get("visual_role", "general")),
                "file_size_bytes": int(asset.get("file_size_bytes", 0) or 0),
                "sha256": str(asset.get("sha256", "")),
            }
        else:
            row = {
                "asset_id": asset.asset_id, "title": asset.title, "creator": asset.creator,
                "source": asset.source, "original_url": asset.source_url,
                "direct_media_url": asset.direct_url, "license": asset.license_name,
                "license_url": asset.license_url, "license_status": asset.license_status,
                "attribution_required": asset.attribution_required, "attribution": asset.attribution,
                "restrictions": list(asset.restrictions), "download_date": asset.download_date,
                "authenticity": asset.authenticity, "verified_authentic": asset.verified_authentic,
                "quality_score": asset.quality_score,
                "mood_fit": asset.mood_fit, "visual_role": asset.visual_role,
                "file_size_bytes": 0, "sha256": "",
            }
        key = row["asset_id"] or row["original_url"]
        if key and key not in seen:
            seen.add(key)
            rows.append(row)
    return rows
