# YT Documentary Bot (free, GitHub Actions)

2 documentaries/day (8-10 min, English) for 30 days: topic -> script -> fact-check -> voice -> footage ->
edit -> quality gate -> thumbnails -> Google Drive (auto-delete after 3 days) -> YouTube (+1 Short each) ->
analytics loop that re-weights topics and swaps weak thumbnails/titles.

## Setup (about 45 minutes, once)

1. **Repo**: create a new **public** GitHub repo, upload all these files. (Public = unlimited free Actions minutes.
   Never commit keys; they go in Secrets only.)
2. **Gemini key**: https://aistudio.google.com/apikey -> secret `GEMINI_API_KEY`.
3. **Pexels key**: https://www.pexels.com/api/ -> secret `PEXELS_API_KEY`.
4. **Google Cloud** (console.cloud.google.com), one project:
   - Enable: YouTube Data API v3, YouTube Analytics API, Google Drive API.
   - OAuth consent screen: External. **Publish it ("In production")**. In "Testing" mode the refresh token
     dies after 7 days and the whole system stops.
   - Credentials -> OAuth client ID -> **Desktop app** -> download as `tools/client_secret.json`.
   - On your PC: `cd tools && pip install google-auth-oauthlib && python get_tokens.py`, sign in with the YouTube
     channel's Google account, then add the 3 printed values as secrets: `GOOGLE_CLIENT_ID`,
     `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_DRIVE_REFRESH_TOKEN` (run get_tokens.py twice: youtube, drive).
5. **Optional** Telegram alerts: secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
6. **Optional but recommended**: put 5-10 royalty-free tracks (YouTube Audio Library, no attribution needed)
   in `assets/music/*.mp3`. Without them the system synthesizes a plain ambient drone.
7. Edit `config.yaml` -> `start_date` (day 1). Then Actions tab -> **generate-video -> Run workflow**
   with force = 1 for a first test. Check the video before trusting the schedule.

Schedule: `generate.yml` runs 05:30 and 13:30 UTC (edit the cron), `analytics.yml` every 12 h,
`cleanup.yml` daily. The system stops by itself after `days: 30`.

## Read this before expecting results

- **Upload lock**: videos uploaded through a *new, unaudited* API project are forced to **Private**.
  Submit Google's "YouTube API Services - Audit and Quota Extension" form. Until approved, publish manually
  (the files stay on Drive for 3 days) or accept private uploads.
- **Thumbnails** need a phone-verified channel.
- **Monetization risk**: fully automated, repetitive channels can be rejected/demonetized under YouTube's
  "inauthentic content" policy. Unique scripts, real analysis and sources help; they do not guarantee anything.
- **Nobody can guarantee #1.** What the system does is the proven loop: strong hook, retention pacing,
  title/thumbnail testing, learning from analytics. Expect the first 2-4 weeks to have low views.
- **Quotas**: 2 videos + 2 Shorts = ~6,400 of 10,000 daily API units.
- **Fact-check** uses Gemini with Google Search grounding. It reduces errors, it does not eliminate them.
  Review the first videos by hand, especially for the "mystery" category.
- **edge-tts** calls Microsoft's online voices; if they ever block GitHub IPs, swap `voice.py` for another TTS.
- Free footage is Pexels + NASA (free licences). Do not add other people's clips.
- Memory lives in `memory/` (committed to the repo). Drive holds only the 3-day video/thumbnail buffer.
- CTR metric (`videoThumbnailImpressionsClickRate`) may be unavailable for some channels; the code then
  falls back to view counts.

## Tuning

`config.yaml`: niche/tone, category mix, word counts, voice, privacy, Shorts, retention days, swap threshold.
Test locally: `FORCE=1 python -m src.pipeline` (needs ffmpeg + env vars).
