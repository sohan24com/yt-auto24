"""Run on your own PC (needs client_secret.json in the same folder).
   python get_tokens.py youtube     -> YouTube upload/analytics token
   python get_tokens.py drive       -> Google Drive token
Google does not allow Drive and YouTube scopes in one request, so we make two tokens."""
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SC = {
    "youtube": ["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl",
                "https://www.googleapis.com/auth/yt-analytics.readonly"],
    "drive": ["https://www.googleapis.com/auth/drive.file"],
}
which = sys.argv[1] if len(sys.argv) > 1 else ""
if which not in SC:
    print("Usage:  python get_tokens.py youtube     or     python get_tokens.py drive")
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SC[which])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print()
if which == "youtube":
    output = Path(__file__).resolve().parent / ".youtube_refresh_token"
else:
    output = Path(__file__).resolve().parent / ".drive_refresh_token"

output.write_text(creds.refresh_token + "\n", encoding="utf-8")
print(f"Refresh token saved locally to {output.name}; its value was not displayed.")
