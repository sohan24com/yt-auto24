"""Small, atomic checkpoints for resumable local documentary stages.

Checkpoint records contain relative artifact paths and SHA-256 digests only. They
never store credentials, provider responses, or arbitrary environment values.
"""
import hashlib
import json
import os
import socket
import time
from contextlib import AbstractContextManager
from pathlib import Path


STAGES = ("research", "script", "evidence_validation", "storyboard", "narration",
          "footage", "editing", "render", "quality_control")
CHECKPOINT_VERSION = 2


def fingerprint(value):
    """Stable fingerprint for JSON-compatible build inputs."""
    packed = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


class StageCheckpoints:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / ".stage-checkpoints.json"

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION
                    or not isinstance(data.get("stages"), dict)):
                return {"version": CHECKPOINT_VERSION, "stages": {}}
            return data
        except (OSError, ValueError, AttributeError, TypeError):
            return {"version": CHECKPOINT_VERSION, "stages": {}}

    def _artifact(self, value):
        path = Path(value)
        path = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("checkpoint artifacts must be inside the checkpoint root")
        if not path.is_file():
            raise ValueError(f"checkpoint artifact is missing: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest.hexdigest()}

    def complete(self, stage, artifacts, stage_fingerprint=""):
        if stage not in STAGES:
            raise ValueError(f"unknown checkpoint stage: {stage}")
        records = [self._artifact(p) for p in artifacts]
        if not records:
            raise ValueError("a completed stage must have at least one artifact")
        state = self._read()
        state["stages"][stage] = {"fingerprint": stage_fingerprint, "artifacts": records,
                                   "completed_at": time.time()}
        atomic_json(self.path, state)

    def is_complete(self, stage, stage_fingerprint=None):
        if stage not in STAGES:
            raise ValueError(f"unknown checkpoint stage: {stage}")
        entry = self._read()["stages"].get(stage)
        if not isinstance(entry, dict):
            return False
        if stage_fingerprint is not None and entry.get("fingerprint") != stage_fingerprint:
            return False
        records = entry.get("artifacts")
        if not isinstance(records, list) or not records:
            return False
        for record in records:
            try:
                actual = self._artifact(record["path"])
            except (OSError, ValueError, KeyError, TypeError):
                return False
            if actual["sha256"] != record.get("sha256"):
                return False
        return True

    def stage_token(self, stage):
        """Return a compact token for downstream fingerprints when a stage is valid."""
        if not self.is_complete(stage):
            return None
        entry = self._read()["stages"][stage]
        return fingerprint(entry["artifacts"])

    def artifact_records(self, paths):
        return [self._artifact(path) for path in paths]

    def next_stage(self):
        state = self._read()
        for stage in STAGES:
            entry = state["stages"].get(stage)
            if not isinstance(entry, dict) or not self.is_complete(stage):
                return stage
        return None

    def invalidate_from(self, stage):
        if stage not in STAGES:
            raise ValueError(f"unknown checkpoint stage: {stage}")
        state = self._read()
        index = STAGES.index(stage)
        for later in STAGES[index:]:
            state["stages"].pop(later, None)
        atomic_json(self.path, state)


class BuildLock(AbstractContextManager):
    """Prevent two workers from generating the same persistent build at once."""
    STALE_AFTER_SECONDS = 6 * 60 * 60

    def __init__(self, root):
        self.path = Path(root).resolve() / ".build.lock"
        self.token = f"{socket.gethostname()}:{os.getpid()}:{time.time_ns()}"

    @staticmethod
    def _alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            payload = {"token": self.token, "pid": os.getpid(), "host": socket.gethostname(),
                       "created_at": time.time()}
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                return self
            except FileExistsError:
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    age = time.time() - float(current.get("created_at", 0))
                    same_host_alive = (current.get("host") == socket.gethostname()
                                       and self._alive(current.get("pid")))
                    if same_host_alive and age < self.STALE_AFTER_SECONDS:
                        raise RuntimeError("a build for this job is already running")
                    self.path.unlink()
                except RuntimeError:
                    raise
                except (OSError, ValueError, TypeError):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
        raise RuntimeError("could not acquire the build lock")

    def __exit__(self, exc_type, exc, tb):
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("token") == self.token:
                self.path.unlink()
        except (OSError, ValueError, TypeError):
            pass
        return False
