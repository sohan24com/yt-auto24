from google.oauth2.credentials import Credentials
from .common import env

YT_SCOPES = ["https://www.googleapis.com/auth/youtube",
             "https://www.googleapis.com/auth/youtube.force-ssl",
             "https://www.googleapis.com/auth/yt-analytics.readonly"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _mk(token_env, scopes):
    return Credentials(None, refresh_token=env(token_env),
                       token_uri="https://oauth2.googleapis.com/token",
                       client_id=env("GOOGLE_CLIENT_ID"), client_secret=env("GOOGLE_CLIENT_SECRET"),
                       scopes=scopes)


def creds():            # YouTube
    return _mk("GOOGLE_REFRESH_TOKEN", YT_SCOPES)


def drive_creds():      # Drive
    return _mk("GOOGLE_DRIVE_REFRESH_TOKEN", DRIVE_SCOPES)
