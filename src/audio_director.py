"""Scene-aware, license-conscious music and ambience selection for the editor."""
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests


LICENSES = {"cc0": {"1.0"}, "by": {"2.0", "2.5", "3.0", "4.0"}}
AUDIO_EXTENSIONS = {"mp3", "wav", "ogg", "flac", "m4a"}
MAX_DOWNLOAD_BYTES = 24 * 1024 * 1024


def _request_timeout(budget):
    per_phase = max(0.5, min(5.0, float(budget) / 2))
    return (per_phase, per_phase)

MUSIC_PROFILES = {
    "suspense": ("mystery", "suspense", "tension", "dark", "drone", "ominous", "thriller"),
    "historical": ("historical", "history", "orchestral", "classical", "period", "documentary"),
    "nature": ("nature", "organic", "forest", "ocean", "wildlife", "earth", "ambient"),
    "science": ("science", "space", "cosmic", "electronic", "technology", "ambient", "wonder"),
    "emotional": ("emotional", "piano", "strings", "tender", "warm", "reflective", "hopeful"),
    "tension": ("urgent", "danger", "tense", "panic", "frantic", "dread", "action"),
    "wonder": ("awe", "wonder", "discovery", "marvel", "triumph", "uplifting"),
    "cinematic": ("cinematic", "film", "score", "atmospheric", "documentary", "ambient"),
}
SFX_TERMS = {
    "thunder": ("thunder", "thunderstorm", "lightning"),
    "rain": ("rain", "rainfall", "downpour", "thunderstorm"),
    "wind": ("wind", "breeze", "storm", "howling"),
    "water": ("ocean", "waves", "waterfall", "river", "water flowing", "surf"),
    "city": ("city ambience", "traffic", "urban ambience", "street ambience"),
    "nature": ("forest ambience", "birds", "wildlife ambience", "insects", "jungle ambience"),
    "fire": ("fire", "fireplace", "crackling", "flames"),
}
IMPORTANT_CUES = ("the evidence", "study found", "records show", "the key finding",
                  "according to", "the crucial detail", "in fact", "the result was")


def _normal(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def music_profile(scene):
    """Classify story context; avoid inferring natural ambiences from emotion alone."""
    if not isinstance(scene, dict):
        scene = {"emotion": scene}
    emotion = _normal(scene.get("emotion"))
    narration = _normal(scene.get("narration"))
    context = _normal(" ".join((str(scene.get("title", "")), str(scene.get("visuals", "")),
                                 str(scene.get("category", "")), str(scene.get("setting", "")))))
    if any(k in emotion for k in ("suspens", "myster", "ominous", "eerie", "hush", "secret")):
        return "suspense"
    if any(k in emotion for k in ("urgent", "danger", "tense", "panic", "frantic", "dread")):
        return "tension"
    if any(k in emotion for k in ("sad", "somber", "grief", "tragic", "tender", "hope", "warm", "reflect")):
        return "emotional"
    if any(k in context for k in ("space", "planet", "cosmic", "quantum", "science", "laboratory")):
        return "science"
    if any(k in context for k in ("forest", "ocean", "wildlife", "nature", "rainforest", "river")):
        return "nature"
    if any(k in context for k in ("history", "historical", "century", "ancient", "archive", "war")):
        return "historical"
    if any(k in emotion for k in ("awe", "wonder", "discovery", "triumph", "marvel")):
        return "wonder"
    if any(cue in narration for cue in IMPORTANT_CUES):
        return "cinematic"
    return "cinematic"


def narration_priority(scene):
    text = _normal(scene.get("narration"))
    return any(_normal(cue) in text for cue in IMPORTANT_CUES)


def ambience_profile(scene):
    """Select only ambience with an explicit scene-setting cue; never infer from emotion."""
    text = _normal(" ".join((str(scene.get("title", "")), str(scene.get("narration", "")),
                              str(scene.get("visuals", "")))))
    for profile, terms in SFX_TERMS.items():
        if any(_normal(term) in text for term in terms):
            return profile
    return None


def _valid_url(value):
    return urlparse(str(value or "")).scheme == "https" and bool(urlparse(str(value)).netloc)


def _allowed_license(license_slug, version):
    return str(version) in LICENSES.get(_normal(license_slug).replace(" ", ""), set())


def _license_url_matches(license_slug, version, value):
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or parsed.hostname != "creativecommons.org":
        return False
    expected = f"/publicdomain/zero/{version}/" if _normal(license_slug) == "cc0" else \
        f"/licenses/by/{version}/" if _normal(license_slug) == "by" else ""
    return parsed.path.rstrip("/") == expected.rstrip("/") and bool(expected)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_asset(manifest_dir, item):
    """Validate manifest provenance, permissive license and content hash before use."""
    if not isinstance(item, dict):
        return None
    license_slug = item.get("license", "")
    version = item.get("license_version", "")
    if not _allowed_license(license_slug, version):
        return None
    if not _valid_url(item.get("source_url")) or not _valid_url(item.get("license_url")) or \
       not _license_url_matches(license_slug, version, item.get("license_url")) or \
       not item.get("attribution") or not item.get("source"):
        return None
    rel = Path(str(item.get("path", "")))
    if rel.is_absolute() or ".." in rel.parts:
        return None
    base = Path(manifest_dir).resolve()
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or path.suffix.lower().lstrip(".") not in AUDIO_EXTENSIONS:
        return None
    if not path.is_file():
        return None
    digest = _sha256(path)
    if not item.get("sha256") or digest != str(item["sha256"]).lower():
        return None
    return {**item, "path": path, "sha256": digest,
            "tags": [_normal(tag) for tag in item.get("tags", [])],
            "moods": [_normal(mood) for mood in item.get("moods", [])]}


def load_assets(manifest_dirs):
    """Load only hash-verified CC0/CC BY audio from local manifests."""
    assets, diagnostics = [], []
    for folder in manifest_dirs:
        manifest_path = Path(folder) / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            diagnostics.append(f"invalid audio manifest: {manifest_path}")
            continue
        for item in data.get("assets", []) if isinstance(data, dict) else []:
            asset = _safe_asset(folder, item)
            if asset:
                assets.append(asset)
            else:
                diagnostics.append(f"skipped unverified or incompatible audio entry in {manifest_path}")
    # Keep the first copy of each content hash: don't count mirrored paths as variety.
    unique = {}
    for asset in assets:
        unique.setdefault(asset["sha256"], asset)
    return list(unique.values()), diagnostics


def _asset_score(asset, profile):
    searchable = " ".join(asset.get("moods", []) + asset.get("tags", []) +
                           [_normal(asset.get("title")), _normal(asset.get("category"))])
    return sum(2 for token in MUSIC_PROFILES.get(profile, ()) if _normal(token) in searchable)


def _sfx_score(asset, profile, scene):
    searchable = _normal(" ".join((str(asset.get("title", "")), *asset.get("moods", []),
                                    *asset.get("tags", []))))
    scene_text = _normal(" ".join((str(scene.get("title", "")), str(scene.get("narration", "")),
                                   str(scene.get("visuals", "")))))
    terms = SFX_TERMS.get(profile, ())
    directly_cued = [term for term in terms if _normal(term) in scene_text]
    if directly_cued:
        return (sum(3 for term in directly_cued if _normal(term) in searchable) -
                sum(1 for term in terms if _normal(term) in searchable and term not in directly_cued))
    return sum(2 for term in terms if _normal(term) in searchable)


def _manifest_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _search_openverse(query, kind="music", timeout=20):
    """One bounded anonymous Openverse query; no API key or pagination/scraping."""
    response = requests.get("https://api.openverse.org/v1/audio/", params={
        "q": query, "license": "cc0,by", "license_type": "commercial",
        "extension": "mp3,wav,ogg,flac", "page_size": 20, "mature": "false",
    }, timeout=_request_timeout(timeout), headers={"User-Agent": "yt-auto24-documentary-audio/1.0"})
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("results", [])
    def has_vocal_tag(row):
        return any(any(word in _normal(tag.get("name", "")) for word in ("vocal", "spoken", "speech"))
                   for tag in row.get("tags", []) if isinstance(tag, dict))
    return [row for row in rows if isinstance(row, dict) and
            _allowed_license(row.get("license"), row.get("license_version")) and
            _valid_url(row.get("url")) and _valid_url(row.get("foreign_landing_url")) and
            (kind != "music" or not has_vocal_tag(row))]


def _download_openverse(row, library_dir, kind, profile, timeout=30):
    ext = str(row.get("filetype", "")).lower().lstrip(".")
    if ext not in AUDIO_EXTENSIONS or not _allowed_license(row.get("license"), row.get("license_version")):
        return None
    if not _valid_url(row.get("url")) or not _valid_url(row.get("foreign_landing_url")):
        return None
    ident = re.sub(r"[^A-Za-z0-9_-]", "", str(row.get("id", "")))
    if not ident:
        return None
    library_dir = Path(library_dir)
    library_dir.mkdir(parents=True, exist_ok=True)
    dest = library_dir / f"{ident}.{ext}"
    temp = dest.with_suffix(dest.suffix + ".part")
    try:
        deadline = time.monotonic() + max(1, float(timeout))
        with requests.get(row["url"], stream=True, timeout=_request_timeout(timeout),
                          headers={"User-Agent": "yt-auto24-documentary-audio/1.0"}) as response:
            response.raise_for_status()
            if not _valid_url(response.url):
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and "audio" not in content_type and "octet-stream" not in content_type:
                return None
            length = int(response.headers.get("Content-Length", "0") or 0)
            if length > MAX_DOWNLOAD_BYTES:
                return None
            size = 0
            with temp.open("wb") as output:
                for chunk in response.iter_content(256 * 1024):
                    if time.monotonic() >= deadline:
                        return None
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        return None
                    output.write(chunk)
        if temp.stat().st_size < 4096:
            return None
        temp.replace(dest)
        creator = str(row.get("creator", "Unknown creator"))
        title = str(row.get("title", "Untitled audio"))
        license_name = f"CC {str(row['license']).upper()} {row['license_version']}"
        attribution = str(row.get("attribution") or f'"{title}" by {creator}, {license_name}')
        entry = {"path": dest.name, "kind": kind, "profile": profile,
                 "title": title, "creator": creator, "tags": [t.get("name", "") for t in row.get("tags", [])
                                                                   if isinstance(t, dict)],
                 "moods": [profile], "source": "Openverse", "source_url": row["foreign_landing_url"],
                 "download_url": row["url"], "license": str(row["license"]).lower(),
                 "license_version": str(row["license_version"]), "license_url": row.get("license_url"),
                 "attribution": attribution, "sha256": _sha256(dest)}
        manifest_path = library_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {"schema_version": 1, "assets": []}
        entries = [e for e in manifest.get("assets", []) if e.get("path") != entry["path"]]
        entries.append(entry)
        _manifest_write(manifest_path, {"schema_version": 1, "assets": entries})
        return _safe_asset(library_dir, entry)
    except (OSError, ValueError, requests.RequestException):
        return None
    finally:
        temp.unlink(missing_ok=True)


class AudioDirector:
    """Build a deterministic per-scene audio plan from narration, local assets and optional search."""
    def __init__(self, repository_root, workdir, config=None):
        self.repository_root = Path(repository_root)
        self.workdir = Path(workdir)
        self.config = config or {}
        # Keep licensed downloads outside per-video checkpoints so successful runs and
        # checkpoint fingerprint resets cannot remove the reusable offline audio library.
        self.library_dir = self.workdir.parent / "audio_library"
        self.diagnostics = []
        self.assets, errors = load_assets((self.repository_root / "assets" / "audio",
                                          self.library_dir))
        self.diagnostics.extend(errors)

    def _discover(self, scenes, durations):
        if not self.config.get("online_discovery", True):
            return
        max_new = max(0, min(6, int(self.config.get("max_discoveries_per_run", 3))))
        if not max_new:
            return
        deadline = time.monotonic() + max(8, min(90, int(self.config.get("discovery_timeout_seconds", 45))))
        music_needs, sfx_needs = Counter(), Counter()
        for scene, duration in zip(scenes, durations):
            profile = music_profile(scene)
            if not any(a.get("kind") == "music" and _asset_score(a, profile) > 0 for a in self.assets):
                music_needs[profile] += float(duration)
            ambient = ambience_profile(scene)
            if ambient and not any(a.get("kind") == "sfx" and _sfx_score(a, ambient, scene) > 0
                                   for a in self.assets):
                sfx_needs[ambient] += 1
        # Reserve a search opportunity for a clearly requested environment before
        # spending the whole bounded budget on similar music profiles.
        jobs = [("sfx", p) for p, _ in sfx_needs.most_common()]
        jobs += [("music", p) for p, _ in music_needs.most_common()]
        downloaded, searches = 0, 0
        for kind, profile in jobs:
            remaining = deadline - time.monotonic()
            if downloaded >= max_new or searches >= max_new or remaining <= 1:
                break
            searches += 1
            try:
                results = _search_openverse(self._query(profile, kind), timeout=min(12, remaining))
                if not results:
                    self.diagnostics.append(f"Openverse found no compatible {kind} results for {profile}")
                    continue
                def rank(row):
                    tags = " ".join(_normal(tag.get("name", "")) for tag in (row.get("tags") or [])
                                    if isinstance(tag, dict))
                    title = _normal(row.get("title"))
                    terms = SFX_TERMS.get(profile, ()) if kind == "sfx" else MUSIC_PROFILES.get(profile, ())
                    match = sum(1 for term in terms if _normal(term) in title + " " + tags)
                    duration = float(row.get("duration") or 0) / 1000
                    # Ambience is most reusable as a short loop; music needs room to breathe.
                    length_penalty = (abs(duration - 18) if kind == "sfx" else
                                      0 if 60 <= duration <= 600 else abs(duration - 240) / 10)
                    size = int(row.get("filesize") or MAX_DOWNLOAD_BYTES)
                    return (-match, length_penalty, size, str(row.get("id", "")))
                results.sort(key=rank)
                for result in results[:2]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 1:
                        break
                    asset = _download_openverse(result, self.library_dir, kind, profile,
                                                timeout=min(18, remaining))
                    if asset:
                        self.assets.append(asset)
                        downloaded += 1
                        self.diagnostics.append(f"added {kind} asset {asset['title']} ({asset['license']}-{asset['license_version']})")
                        break
                else:
                    self.diagnostics.append(f"Openverse candidates for {kind}/{profile} could not be downloaded or verified")
            except (OSError, ValueError, requests.RequestException) as exc:
                self.diagnostics.append(f"Openverse {kind} discovery unavailable for {profile}: {type(exc).__name__}")

    @staticmethod
    def _query(profile, kind):
        if kind == "sfx":
            return {"rain": "rain rainfall ambience field recording", "wind": "wind ambience field recording",
                    "water": "water waves river ambience field recording", "city": "city traffic street ambience",
                    "nature": "forest birds nature ambience field recording",
                    "fire": "fire crackling ambience field recording"}.get(profile, f"{profile} ambience sound effect")
        return {"suspense": "subtle cinematic suspense instrumental ambient score",
                "historical": "cinematic historical orchestral instrumental score",
                "nature": "cinematic organic nature documentary instrumental ambient score",
                "science": "cinematic science space atmospheric instrumental score",
                "emotional": "soft emotional piano strings instrumental score",
                "tension": "subtle cinematic tension instrumental score",
                "wonder": "cinematic wonder discovery instrumental score"}.get(
                    profile, "cinematic documentary instrumental ambient score")

    def plan(self, scenes, durations):
        self._discover(scenes, durations)
        plan, used_music, used_sfx = [], Counter(), set()
        previous_profile, previous_music = None, None
        for scene, duration in zip(scenes, durations):
            profile = music_profile(scene)
            music = [a for a in self.assets if a.get("kind") == "music"]
            ranked = sorted(music, key=lambda a: (
                -_asset_score(a, profile), used_music[a["sha256"]],
                a.get("sha256", "")))
            # Keep the same cue across same-mood scenes to avoid audible track churn.
            if previous_profile == profile and previous_music and _asset_score(previous_music, profile):
                chosen = previous_music
            else:
                chosen = ranked[0] if ranked and _asset_score(ranked[0], profile) else None
            if chosen:
                used_music[chosen["sha256"]] += 1
            previous_profile, previous_music = profile, chosen
            ambience = ambience_profile(scene)
            candidates = sorted((a for a in self.assets if a.get("kind") == "sfx" and ambience and
                                 _sfx_score(a, ambience, scene) > 0 and
                                 a["sha256"] not in used_sfx),
                                key=lambda a: (-_sfx_score(a, ambience, scene), a["sha256"]))
            sfx = candidates[0] if candidates and _sfx_score(candidates[0], ambience, scene) > 0 else None
            if sfx:
                used_sfx.add(sfx["sha256"])
            gain = float(self.config.get("music_gain", 0.24))
            if narration_priority(scene):
                gain *= float(self.config.get("key_information_music_scale", 0.55))
            plan.append({"profile": profile, "music": chosen, "music_gain": max(0.03, min(0.6, gain)),
                         "ambience_profile": ambience, "ambience": sfx,
                         "duration": float(duration), "key_information": narration_priority(scene)})
        return plan

    @staticmethod
    def credits(plan):
        credits, seen = [], set()
        for row in plan:
            for asset in (row.get("music"), row.get("ambience")):
                if not asset or asset["sha256"] in seen:
                    continue
                seen.add(asset["sha256"])
                credits.append({key: asset.get(key) for key in
                                ("title", "creator", "source", "source_url", "license",
                                 "license_version", "license_url", "attribution", "sha256")})
        return credits
