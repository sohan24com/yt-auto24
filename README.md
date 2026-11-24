# YT Documentary Bot (free, GitHub Actions)

2 documentaries/day (8-10 min, English) for 30 days: topic -> script -> fact-check -> voice -> matched footage ->
mood-aware edit -> quality gate -> distinct thumbnails -> Google Drive -> YouTube (+ story-selected Short) ->
analytics loop. The pipeline uses open-source FFmpeg/Pillow plus existing free APIs; it keeps safe fallbacks and never
uploads media that fails the media probes.

## Setup (about 45 minutes, once)

1. **Repo**: keep the repository public or private according to your needs. GitHub Actions usage and included minutes
   depend on the repository visibility and your account plan; check GitHub's current limits. Never commit keys;
   keep them in Actions Secrets only.
2. **Gemini keys**: add up to five API keys as GitHub Actions secrets: `GEMINI_API_KEY`,
   `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, `GEMINI_API_KEY_4`, and `GEMINI_API_KEY_5`.
   The code tries configured keys across Gemini requests; Gemini TTS also moves to the next key after a failure.
   Add keys from separate Google Cloud projects if you need separate project quotas; multiple keys in one
   project generally share that project's quota.
3. **Optional text fallbacks**: add `GROQ_API_KEY` and `GROQ_API_KEY_2` for Groq,
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` for Cloudflare Workers AI, and
   `OPENROUTER_API_KEY` for OpenRouter. Ordinary text/JSON generation tries Gemini, Groq, Cloudflare,
   then OpenRouter's `openrouter/free` router only; it never selects a paid OpenRouter model. The free
   router and provider limits vary by account and can change. Grounded fact-check tries Gemini Search first,
   then Groq's required browser_search tool. Publishing is held unless structured claim-to-source evidence
   is returned for the documentary; an unverified original script is retained for diagnosis, not published.
   Groq keys in one organization share quota. Cloudflare also has a daily free allocation.
   Keep all credentials only in Actions secrets. Cohere trial keys are not used by this production workflow:
   Cohere says trial keys are not permitted for production or commercial use; a production key is paid.
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

- **Upload privacy**: current YouTube Data API documentation says unverified projects are no longer
  automatically restricted to private uploads. The workflow still uses the explicit `youtube.privacy`
  setting in `config.yaml`; account restrictions and API policy can still affect an upload.
  See [YouTube API revision history](https://developers.google.com/youtube/data/revision_history).
- **Thumbnails** need a phone-verified channel.
- **Monetization risk**: fully automated, repetitive channels can be rejected/demonetized under YouTube's
  "inauthentic content" policy. Unique scripts, real analysis and sources help; they do not guarantee anything.
- **Nobody can guarantee #1.** What the system does is the proven loop: strong hook, retention pacing,
  title/thumbnail testing, learning from analytics. Expect the first 2-4 weeks to have low views.
- **YouTube API quota**: do not use the old `~6,400 / 10,000` estimate. Current docs describe separate
  daily buckets, including up to 100 `videos.insert` calls/day and 10,000 units/day for other endpoints;
  methods such as thumbnail writes and comment inserts consume those other units. Check the project's
  Google Cloud quota page before changing upload volume:
  [official quota calculator](https://developers.google.com/youtube/data/determine_quota_cost).
- **Fact-check** uses Gemini Search and then Groq browser search. Claim links are evidence records, not
  independent human verification; review documentary claims before publishing.
- Voice fallback order is Gemini TTS → Edge TTS → offline `espeak-ng`. The last option is less natural, but it does not need a third-party TTS service or API key.
- Footage is matched to narration terms. Each used asset is written to an asset manifest with its source ID,
  source URL, license label, license URL, and review status. Wikimedia and Internet Archive licensing is
  filtered to the allowed recorded licenses; NASA items without usable rights metadata are skipped.
  Pexels/Pixabay are labeled illustrative and their provider terms are recorded. The manifest is stored with
  the video record and uploaded to Drive.
- Shorts select a curiosity moment before the final scene from across the documentary, use a 9:16 frame, and link
  to the full video in both description and a top-level comment. The YouTube Data API does not expose comment pinning.
- Captions use animated word highlighting with estimated word timing; they are not forced-aligned to phonemes/audio.
- Run offline checks with `python -m unittest discover -v`; the `documentary-quality` workflow runs unit tests,
  FFmpeg full-file decode/audio scans, subtitle, thumbnail, and workflow YAML checks on feature branches and
  pull requests. It never publishes videos.
- Upload jobs save a manifest before YouTube insert and use a private metadata tag to find an upload after
  an interrupted run. A saved pending job is resumed from the Drive copy; Shorts failures are retried on a
  later run without re-uploading the long video.
- Memory lives in `memory/` (committed to the repo). Drive holds only the 3-day video/thumbnail buffer.
- CTR metric (`videoThumbnailImpressionsClickRate`) may be unavailable for some channels; the code then
  falls back to view counts.

## Tuning

`config.yaml`: niche/tone, category mix, word counts, voice, privacy, Shorts, retention days, swap threshold.
Run offline checks: `python -m unittest discover -v` (requires FFmpeg/ffprobe). To test generation manually,
`FORCE=1 python -m src.pipeline` still needs API secrets and may upload a video.
