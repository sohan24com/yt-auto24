"""Run on your own PC (needs client_secret.json in the same folder).
   python get_tokens.py youtube     -> YouTube upload/analytics token
   python get_tokens.py drive       -> Google Drive token
Google does not allow Drive and YouTube scopes in one request, so we make two tokens."""
import os
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SC = {
    "youtube": ["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl",
                "https://www.googleapis.com/auth/yt-analytics.readonly"],
    "drive": ["https://www.googleapis.com/auth/drive.file"],
}
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    which = argv[0] if argv else ""
    if which not in SC:
        print("Usage:  python get_tokens.py youtube     or     python get_tokens.py drive")
        return 1

    tools_dir = Path(__file__).resolve().parent
    flow = InstalledAppFlow.from_client_secrets_file(str(tools_dir / "client_secret.json"), SC[which])
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise RuntimeError("Google did not return a refresh token; no credential file was written")
    output = tools_dir / (".youtube_refresh_token" if which == "youtube" else ".drive_refresh_token")
    output.write_text(creds.refresh_token + "\n", encoding="utf-8")
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass  # Windows ACLs are inherited from the private user profile.
    print(f"Refresh token saved locally to {output.name}; its value was not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
