import datetime as dt
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


def folder_id():
    name = cfg()["drive"]["folder_name"]
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = svc().files().list(q=q, fields="files(id)").execute().get("files", [])
    if res:
        return res[0]["id"]
    f = svc().files().create(body={"name": name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    return f["id"]


def upload(path, name=None):
    media = MediaFileUpload(str(path), resumable=True, chunksize=16 << 20)
    req = svc().files().create(body={"name": name or path.name, "parents": [folder_id()]}, media_body=media, fields="id")
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    return resp["id"]


def download(file_id, dest):
    import io
    req = svc().files().get_media(fileId=file_id)
    with open(dest, "wb") as f:
        dl = MediaIoBaseDownload(f, req)
        done = False
        while not done:
            _, done = dl.next_chunk()


def cleanup(days=None):
    days = days or cfg()["drive"]["retention_days"]
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = f"'{folder_id()}' in parents and trashed=false and createdTime < '{cutoff}'"
    deleted, token = 0, None
    while True:
        r = svc().files().list(q=q, fields="nextPageToken, files(id,name)", pageToken=token).execute()
        for f in r.get("files", []):
            svc().files().delete(fileId=f["id"]).execute()   # permanent, skips trash
            print("deleted", f["name"])
            deleted += 1
        token = r.get("nextPageToken")
        if not token:
            return deleted
