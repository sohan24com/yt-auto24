import datetime as dt
import hashlib
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from .common import cfg
from .google_auth import drive_creds as creds

_svc = None


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def svc():
    global _svc
    if _svc is None:
        _svc = build("drive", "v3", credentials=creds(), cache_discovery=False)
    return _svc


def folder_id():
    name = cfg()["drive"]["folder_name"]
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name='{escaped}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = svc().files().list(q=q, fields="files(id)").execute().get("files", [])
    if res:
        return res[0]["id"]
    f = svc().files().create(body={"name": name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    return f["id"]


def upload(path, name=None):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Drive upload source does not exist: {path}")
    digest = _file_sha256(path)
    service = svc()
    parent = folder_id()
    # A checkpoint may be interrupted after Drive accepted the upload but before
    # its ID was saved. Hash-based appProperties make the retry idempotent.
    q = (f"'{parent}' in parents and trashed=false and appProperties has "
         f"{{ key='pipeline_sha256' and value='{digest}' }}")
    existing = service.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
    if existing:
        return existing[0]["id"]
    media = MediaFileUpload(str(path), resumable=True, chunksize=16 << 20)
    body = {"name": name or path.name, "parents": [parent],
            "appProperties": {"pipeline_sha256": digest}}
    req = service.files().create(body=body, media_body=media, fields="id")
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    return resp["id"]


def download(file_id, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    req = svc().files().get_media(fileId=file_id)
    try:
        with open(partial, "wb") as f:
            dl = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = dl.next_chunk(num_retries=5)
        if not partial.is_file() or partial.stat().st_size == 0:
            raise IOError("Drive returned an empty download")
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def cleanup(days=None):
    days = cfg()["drive"]["retention_days"] if days is None else days
    days = int(days)
    if days < 1:
        raise ValueError("Drive retention must be at least one day")
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = f"'{folder_id()}' in parents and trashed=false and createdTime < '{cutoff}'"
    deleted, token = 0, None
    while True:
        r = svc().files().list(q=q, fields="nextPageToken, files(id,name)", pageToken=token).execute()
        for f in r.get("files", []):
            # Trash is recoverable; never permanently erase user Drive content.
            svc().files().update(fileId=f["id"], body={"trashed": True}).execute()
            print("moved to Drive trash", f["name"])
            deleted += 1
        token = r.get("nextPageToken")
        if not token:
            return deleted
