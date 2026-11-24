# YT Documentary Bot (free, GitHub Actions)

2 documentaries/day (8-10 min, English) for 30 days: topic -> script -> fact-check -> voice -> matched footage ->
mood-aware edit -> quality gate -> distinct thumbnails -> Google Drive -> YouTube (+ story-selected Short) ->
analytics loop. The pipeline uses open-source FFmpeg/Pillow plus existing free APIs; it keeps safe fallbacks and never
uploads media that fails the media probes.

## Setup (about 45 minutes, once)

1. **Repo**: create a new **public** GitHub repo, upload all these files. (Public = unlimited free Actions minutes.
   Never commit keys; they go in Secrets only.)
2. **Gemini keys**: add up to five API keys as GitHub Actions secrets: `GEMINI_API_KEY`,
   `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, `GEMINI_API_KEY_4`, and `GEMINI_API_KEY_5`.
   The code tries configured keys across Gemini requests; Gemini TTS also moves to the next key after a failure.
   Add keys from separate Google Cloud projects if you need separate project quotas; multiple keys in one
   project generally share that project's quota.
3. **Optional Groq text fallback**: add `GROQ_API_KEY` and `GROQ_API_KEY_2` as Actions secrets. It is tried only if Gemini fails for ordinary text/JSON generation; Google Search fact-check stays on Gemini. Groq rate limits are shared by keys in the same organization, so extra keys there do not add quota. Use only while your Groq organization is on the Free tier; upgrading to the paid Developer tier can charge per token.
4. **Pexels key**: https://www.pexels.com/api/ -> secret `PEXELS_API_KEY`.
5. **Google Cloud** (console.cloud.google.com), one project:
   - Enable: YouTube Data API v3, YouTube Analytics API, Google Drive API.
   - OAuth consent screen: External. **Publish it ("In production")**. In "Testing" mode the refresh token
     dies after 7 days and the whole system stops.
   - Credentials -> OAuth client ID -> **Desktop app** -> download as `tools/client_secret.json`.
   - On your PC: `cd tools && pip install google-auth-oauthlib && python get_tokens.py`, sign in with the YouTube
     channel's Google account, then add the printed values as secrets: `GOOGLE_CLIENT_ID`,
     `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_DRIVE_REFRESH_TOKEN` (run `get_tokens.py youtube`
     and `get_tokens.py drive`). The existing YouTube token keeps upload and analytics working. To enable the optional
     Shorts link comment, regenerate `GOOGLE_REFRESH_TOKEN` with `python get_tokens.py youtube` and update that
     GitHub Actions secret. The new token is saved locally in an ignored file; the helper does not print its value.
6. **Optional** Telegram alerts: secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
7. **Optional music**: add only tracks you have rights to use under `assets/music/`. Name tracks with a mood word
   such as `suspense_01.mp3`, `tension_01.mp3`, `emotional_01.mp3`, `wonder_01.mp3`, or `neutral_01.mp3`.
   The editor matches those tracks to scene emotion and synthesizes a free ambient bed for moods with no matching file.
8. Edit `config.yaml` -> `start_date` (day 1). Then Actions tab -> **generate-video -> Run workflow**
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
- Footage results are matched to narration terms. Pexels/Pixabay are labeled illustrative; Wikimedia reference
  images and Internet Archive clips carry source labels. When nothing relevant is available, the video shows a
  generated context card instead of unrelated stock.
- Shorts select a curiosity moment before the final scene from across the documentary, use a 9:16 frame, and link
  to the full video in both description and a top-level comment. The YouTube Data API does not expose comment pinning.
- Captions use animated word highlighting with estimated word timing; they are not forced-aligned to phonemes/audio.
- Run offline checks with `python -m unittest discover -v`; the `documentary-quality` workflow runs these checks
  and FFmpeg smoke tests on feature branches and pull requests. It never publishes videos.
- Memory lives in `memory/` (committed to the repo). Drive holds only the 3-day video/thumbnail buffer.
- CTR metric (`videoThumbnailImpressionsClickRate`) may be unavailable for some channels; the code then
  falls back to view counts.

## Tuning

`config.yaml`: niche/tone, category mix, word counts, voice, privacy, Shorts, retention days, swap threshold.
Run offline checks: `python -m unittest discover -v` (requires FFmpeg/ffprobe). To test generation manually,
`FORCE=1 python -m src.pipeline` still needs API secrets and may upload a video.
