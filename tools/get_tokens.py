"""Run ONCE on your own computer to get the Google refresh token.
   pip install google-auth-oauthlib ; put client_secret.json (OAuth 'Desktop app') next to this file."""
from pathlib import Path
import sys

sys.path.insert(0, "..")
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main():
    secret_path = Path(__file__).resolve().parent / "client_secret.json"
    if not secret_path.exists():
        raise FileNotFoundError(f"Missing {secret_path}. Download Desktop OAuth client JSON there first.")

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    print("\n--- Add these to GitHub Secrets ---")
    print("GOOGLE_CLIENT_ID     =", creds.client_id)
    print("GOOGLE_CLIENT_SECRET =", creds.client_secret)
    print("GOOGLE_REFRESH_TOKEN =", creds.refresh_token)
    if not creds.refresh_token:
        print("\nWarning: No refresh_token returned. Revoke prior app access in Google Account and re-run.")


if __name__ == "__main__":
    main()
