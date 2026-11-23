import datetime as dt
import io
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from .common import cfg
from .google_auth import creds

_svc = None


def svc():
    global _svc
    if _svc is None:
        _svc = build("drive", "v3", credentials=creds(), cache_discovery=False)
    return _svc


def _escape_q(val):
    return str(val or "").replace("\\", "\\\\").replace("'", "\\'")


def folder_id():
    name = ((cfg().get("drive") or {}).get("folder_name")) or "DocumentaryPipeline"
    safe_name = _escape_q(name)
    q = (
        f"name='{safe_name}' and "
        "mimeType='application/vnd.google-apps.folder' and "
        "trashed=false"
    )
    res = svc().files().list(q=q, fields="files(id)", pageSize=10).execute().get("files", [])
    if res:
        return res[0]["id"]
    f = svc().files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return f["id"]


def upload(path, name=None):
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"drive upload source not found: {src}")
    file_name = name or src.name
    media = MediaFileUpload(str(src), resumable=True, chunksize=16 << 20)
    try:
        req = svc().files().create(
            body={"name": file_name, "parents": [folder_id()]},
            media_body=media,
            fields="id",
        )
        resp = None
        while resp is None:
            _, resp = req.next_chunk(num_retries=5)
        return resp["id"]
    finally:
        if hasattr(media, "stream") and media.stream():
            try:
                media.stream().close()
            except Exception:
                pass


def download(file_id, dest):
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    tmp_path.unlink(missing_ok=True)
    try:
        req = svc().files().get_media(fileId=file_id)
        with open(tmp_path, "wb") as f:
            dl = MediaIoBaseDownload(f, req, chunksize=16 << 20)
            done = False
            while not done:
                _, done = dl.next_chunk(num_retries=5)
        tmp_path.replace(dest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def cleanup(days=None):
    drive_cfg = cfg().get("drive") or {}
    retention_days = int(days if days is not None else drive_cfg.get("retention_days", 30))
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fid = _escape_q(folder_id())
    q = f"'{fid}' in parents and trashed=false and createdTime < '{cutoff}'"
    deleted, token = 0, None
    while True:
        r = svc().files().list(
            q=q,
            fields="nextPageToken, files(id,name)",
            pageToken=token,
            pageSize=100,
        ).execute()
        for f in r.get("files", []):
            svc().files().delete(fileId=f["id"]).execute()   # permanent, skips trash
            print("deleted", f["name"])
            deleted += 1
        token = r.get("nextPageToken")
        if not token:
            return deleted
