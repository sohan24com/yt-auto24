"""Bounded downloads for selected HTTPS media candidates."""
import hashlib
import ipaddress
import os
import tempfile
import time
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


MAX_DOWNLOAD_BYTES = max(1, int(os.getenv("MEDIA_MAX_DOWNLOAD_MB", "90"))) << 20


def _safe_target(url):
    try:
        parsed = urlparse(str(url or ""))
        port = parsed.port
    except ValueError:
        return False
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or port not in (None, 443)):
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(row[4][0])
                         for row in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)}
        except (OSError, ValueError, IndexError):
            return False
    # Reject mixed public/private DNS answers as well as internal and reserved IPs.
    return bool(addresses) and all(address.is_global for address in addresses)


def _open_response(url, headers, timeout, max_redirects=5):
    """Follow only a few individually checked HTTPS redirects; never auto-follow blindly."""
    current = str(url)
    for _ in range(max_redirects + 1):
        if not _safe_target(current):
            return None
        response = requests.get(current, stream=True, timeout=timeout, headers=headers,
                                allow_redirects=False)
        status = int(getattr(response, "status_code", 200))
        if status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            response.close()
            if not location:
                return None
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        if not _safe_target(getattr(response, "url", current)):
            response.close()
            return None
        return response
    return None


def _signature_ok(prefix, suffix):
    if suffix in {".jpg", ".jpeg"}:
        return prefix.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return prefix.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"
    if suffix == ".mp4":
        return len(prefix) >= 12 and prefix[4:8] == b"ftyp"
    if suffix == ".webm":
        return prefix.startswith(bytes.fromhex("1a45dfa3"))
    if suffix in {".mp3", ".mpga"}:
        return prefix.startswith(b"ID3") or len(prefix) > 1 and prefix[0] == 0xff and prefix[1] & 0xe0 == 0xe0
    if suffix in {".ogg", ".oga"}:
        return prefix.startswith(b"OggS")
    if suffix == ".wav":
        return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE"
    return False


def _asset_cache_path(cache_key, suffix, directory=None):
    root = Path(directory or os.getenv("MEDIA_ASSET_CACHE_DIR", ".media-cache/assets"))
    key = hashlib.sha256(str(cache_key).encode("utf-8")).hexdigest()
    return root / f"{key}{suffix}"


def _load_cached(cache_path, destination, max_bytes, ttl_days=None):
    try:
        ttl_days = max(0, int(os.getenv("MEDIA_ASSET_CACHE_TTL_DAYS", "7") if ttl_days is None else ttl_days))
        if ttl_days == 0 or time.time() - cache_path.stat().st_mtime > ttl_days * 86400:
            cache_path.unlink(missing_ok=True)
            return None
        size = cache_path.stat().st_size
        if size <= 0 or size > max_bytes:
            cache_path.unlink(missing_ok=True)
            return None
        with cache_path.open("rb") as stream:
            prefix = stream.read(32)
        if not _signature_ok(prefix, cache_path.suffix.lower()):
            cache_path.unlink(missing_ok=True)
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("rb") as source, destination.open("wb") as target:
            digest = hashlib.sha256()
            while chunk := source.read(1 << 20):
                target.write(chunk)
                digest.update(chunk)
        return {"size_bytes": size, "sha256": digest.hexdigest(), "cache_hit": True}
    except OSError:
        return None


def _store_cached(cache_path, source, max_bytes, ttl_days=None, max_total_bytes=None):
    try:
        size = source.stat().st_size
        max_total = (int(os.getenv("MEDIA_ASSET_CACHE_MAX_MB", "512")) << 20
                     if max_total_bytes is None else max(0, int(max_total_bytes)))
        if not max_total or size <= 0 or size > max_bytes or size > max_total:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        ttl_days = max(0, int(os.getenv("MEDIA_ASSET_CACHE_TTL_DAYS", "7") if ttl_days is None else ttl_days))
        cached_files = list(cache_path.parent.glob("*.*"))
        for item in cached_files:
            try:
                if ttl_days == 0 or time.time() - item.stat().st_mtime > ttl_days * 86400:
                    item.unlink(missing_ok=True)
            except OSError:
                continue
        cached_files = [item for item in cache_path.parent.glob("*.*") if item != cache_path]
        total = sum(item.stat().st_size for item in cached_files if item.exists())
        for item in sorted(cached_files, key=lambda path: path.stat().st_mtime):
            if total + size <= max_total:
                break
            try:
                total -= item.stat().st_size
                item.unlink(missing_ok=True)
            except OSError:
                pass
        fd, temporary = tempfile.mkstemp(prefix=".asset-", suffix=cache_path.suffix, dir=str(cache_path.parent))
        os.close(fd)
        try:
            with source.open("rb") as src, open(temporary, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            os.replace(temporary, cache_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError:
        return


def download(url, destination, max_bytes=MAX_DOWNLOAD_BYTES, headers=None, timeout=60, cache_key=None,
             cache_directory=None, cache_ttl_days=None, cache_max_total_bytes=None):
    """Stream to a temporary file, verify HTTPS/redirect, size and basic media signature."""
    destination = Path(destination)
    suffix = destination.suffix.lower()
    if not _safe_target(url) or suffix not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm",
                                               ".mp3", ".mpga", ".ogg", ".oga", ".wav"}:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache_path = _asset_cache_path(cache_key or url, suffix, cache_directory)
    cached = _load_cached(cache_path, destination, max_bytes, cache_ttl_days)
    if cached:
        return cached
    fd, temporary = tempfile.mkstemp(prefix=".media-", suffix=suffix, dir=str(destination.parent))
    digest, total, prefix = hashlib.sha256(), 0, bytearray()
    try:
        with os.fdopen(fd, "wb") as output:
            response = _open_response(url, headers, timeout)
            if response is None:
                return None
            with response:
                if not _safe_target(getattr(response, "url", url)):
                    return None
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    return None
                for chunk in response.iter_content(1 << 20):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        return None
                    if len(prefix) < 32:
                        prefix.extend(chunk[:32 - len(prefix)])
                    digest.update(chunk)
                    output.write(chunk)
        if not total or not _signature_ok(bytes(prefix), suffix):
            return None
        os.replace(temporary, destination)
        _store_cached(cache_path, destination, max_bytes, cache_ttl_days, cache_max_total_bytes)
        return {"size_bytes": total, "sha256": digest.hexdigest(), "cache_hit": False}
    except (OSError, ValueError, requests.RequestException):
        return None
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
