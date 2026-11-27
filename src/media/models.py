"""Normalized records shared by the documentary media source adapters."""
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class MediaAsset:
    asset_id: str
    title: str
    source: str
    media_type: str
    source_url: str
    direct_url: str = ""
    creator: str = ""
    license_name: str = "unknown"
    license_url: str = ""
    license_status: str = "review_required"
    attribution_required: bool = True
    attribution: str = ""
    restrictions: list = field(default_factory=list)
    authenticity: str = "topic_matched_illustrative"
    width: int = 0
    height: int = 0
    duration: float = 0.0
    relevance: float = 0.0
    quality: float = 0.0
    scene_match: float = 0.0
    cinematic_usefulness: float = 0.0
    mood_fit: float = 0.5
    license_confidence: float = 0.0
    diversity: float = 1.0
    visual_role: str = "general"
    motion_suitability: float = 0.0
    download_reliability: float = 0.5
    quality_score: float = 0.0
    verified_authentic: bool = False
    download_date: str = ""
    cacheable: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def publish_ready(self):
        return self.license_status in {
            "provider_terms", "allowed_public_domain", "allowed_attribution"
        } and bool(self.source_url and self.license_url)

    def to_dict(self):
        return asdict(self)


def asset_from_dict(value):
    known = {key: value[key] for key in MediaAsset.__dataclass_fields__ if key in value}
    return MediaAsset(**known)
