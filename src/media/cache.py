"""Small bounded JSON metadata cache. Never store provider credentials or media bytes."""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class SearchCache:
    def __init__(self, directory=None, ttl_seconds=None, max_bytes=None):
        ttl_seconds = (os.getenv("MEDIA_SEARCH_CACHE_TTL_SECONDS", "86400")
                       if ttl_seconds is None else ttl_seconds)
        max_bytes = (int(os.getenv("MEDIA_SEARCH_CACHE_MAX_MB", "16")) << 20
                     if max_bytes is None else max_bytes)
        self.directory = Path(directory or os.getenv("MEDIA_SEARCH_CACHE_DIR", ".media-cache"))
        self.path = self.directory / "search.json"
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_bytes = max(1024, int(max_bytes))
        self._loaded = False
        self._entries = {}

    @staticmethod
    def key(provider, media_type, query):
        raw = "\x1f".join((str(provider), str(media_type), " ".join(str(query).lower().split())))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                self._entries = {}
                return
            now = time.time()
            self._entries = {
                key: value for key, value in data.items()
                if isinstance(value, dict) and now - float(value.get("saved_at", 0)) < self.ttl_seconds
            }
        except (OSError, ValueError, TypeError, AttributeError):
            self._entries = {}

    def get(self, provider, media_type, query):
        self._load()
        entry = self._entries.get(self.key(provider, media_type, query))
        if not entry:
            return None
        return entry.get("results", [])

    def put(self, provider, media_type, query, results):
        if not self.ttl_seconds:
            return
        self._load()
        self._entries[self.key(provider, media_type, query)] = {
            "saved_at": time.time(), "results": results,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._entries, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > self.max_bytes:
            ordered = sorted(self._entries.items(), key=lambda item: item[1].get("saved_at", 0), reverse=True)
            kept, size = {}, 2
            for key, value in ordered:
                encoded = json.dumps({key: value}, ensure_ascii=False, separators=(",", ":"))
                if size + len(encoded.encode("utf-8")) > self.max_bytes:
                    continue
                kept[key] = value
                size += len(encoded.encode("utf-8"))
            self._entries = kept
            payload = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
        fd, temp = tempfile.mkstemp(prefix="media-cache-", dir=str(self.directory))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
