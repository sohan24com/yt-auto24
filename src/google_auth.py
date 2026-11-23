from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from .common import env

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

_CREDS = None


def creds():
    global _CREDS
    if _CREDS is None:
        _CREDS = Credentials(
            None,
            refresh_token=env("GOOGLE_REFRESH_TOKEN"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=env("GOOGLE_CLIENT_ID"),
            client_secret=env("GOOGLE_CLIENT_SECRET"),
            scopes=SCOPES,
        )
    if not _CREDS.valid or _CREDS.expired:
        _CREDS.refresh(Request())
    return _CREDS
