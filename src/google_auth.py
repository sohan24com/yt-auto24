from google.oauth2.credentials import Credentials
from .common import env

# Keep refresh requests compatible with tokens already issued before optional Shorts comments.
# Newly authorized tokens can also carry youtube.force-ssl; do not require that scope from old tokens.
YT_SCOPES = ["https://www.googleapis.com/auth/youtube",
             "https://www.googleapis.com/auth/yt-analytics.readonly"]
YT_COMMENT_SCOPES = YT_SCOPES + ["https://www.googleapis.com/auth/youtube.force-ssl"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _mk(token_env, scopes):
    return Credentials(None, refresh_token=env(token_env),
                       token_uri="https://oauth2.googleapis.com/token",
                       client_id=env("GOOGLE_CLIENT_ID"), client_secret=env("GOOGLE_CLIENT_SECRET"),
                       scopes=scopes)


def creds():            # YouTube
    return _mk("GOOGLE_REFRESH_TOKEN", YT_SCOPES)


def comment_creds():    # YouTube top-level comments
    return _mk("GOOGLE_REFRESH_TOKEN", YT_COMMENT_SCOPES)


def drive_creds():      # Drive
    return _mk("GOOGLE_DRIVE_REFRESH_TOKEN", DRIVE_SCOPES)
