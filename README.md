# YT Documentary Bot (free, GitHub Actions)

2 documentaries/day (8-10 min, English) for 30 days: topic -> script -> fact-check -> voice -> matched footage ->
mood-aware edit -> quality gate -> distinct thumbnails -> Google Drive -> YouTube (+ story-selected Short) ->
analytics loop. The pipeline uses open-source FFmpeg/Pillow plus existing free APIs; it keeps safe fallbacks and never
uploads media that fails the media probes.

বাংলা thumbnail engine architecture, scoring limits এবং QC details: [`docs/THUMBNAIL_INTELLIGENCE_BN.md`](docs/THUMBNAIL_INTELLIGENCE_BN.md)।

## Setup (about 45 minutes, once)

1. **Repo**: create a new **public** GitHub repo, upload all these files. (Public = unlimited free Actions minutes.
   Never commit keys; they go in Secrets only.)
2. **Gemini keys**: add up to five API keys as GitHub Actions secrets: `GEMINI_API_KEY`,
   `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, `GEMINI_API_KEY_4`, and `GEMINI_API_KEY_5`.
   The code tries configured keys across Gemini requests; Gemini TTS also moves to the next key after a failure.
   Add keys from separate Google Cloud projects if you need separate project quotas; multiple keys in one
   project generally share that project's quota.
3. **Optional text fallbacks**: add `GROQ_API_KEY` and `GROQ_API_KEY_2` for Groq,
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` for Cloudflare Workers AI, and
   `OPENROUTER_API_KEY` for OpenRouter. Ordinary text/JSON generation tries Gemini, Groq, Cloudflare,
   then OpenRouter's `openrouter/free` router only; it never selects a paid OpenRouter model. The free
   router is rate limited (currently 50 requests/day); provider availability can still vary. Google Search
   grounded fact-check remains Gemini-only and keeps its existing safe behavior if Gemini is unavailable.
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
7. **Optional music / ambience**: add audio files under `assets/audio/music/` or `assets/audio/sfx/` and register
   each file in `assets/audio/manifest.json` as described in `assets/audio/README.md`. The director verifies the
   file hash and accepts only CC0 or CC BY licenses that permit adaptations and commercial reuse. Scene-specific
   local tracks take precedence. If enabled, anonymous Openverse search can find and cache a small number of CC0/CC
   BY commercial-compatible tracks in the resumable work cache; if the network is unavailable, the editor uses the
   existing generated score and any verified local library. Audio credits and hashes are added to the run report and
   audio credits are included in the video description.
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
- Captions are phrase-level, two-line cues estimated from narration text and scene duration. They are not forced-aligned
  to the generated speech waveform; inspect timing on the rendered master when exact synchronization matters.
- Run offline checks with `python -m unittest discover -v`; the `documentary-quality` workflow runs these checks
  and FFmpeg smoke tests on feature branches and pull requests. It never publishes videos.
- The generation workflow caches a secret-free checkpoint between runs. If research, voice, footage, or rendering
  fails, rerun the workflow on the same branch to resume completed stages and valid rendered shots. Checkpoints are
  isolated by production job ID and config fingerprint, preventing a later daily slot from reusing an earlier video.
  The workflow skips cache saves above 5 GiB; those oversized runs must rebuild
  after an interruption. GitHub can also evict caches when storage limits are reached. Credentials stay in Actions
  secrets and are never written to the checkpoint.
- Exact byte-identical downloads from different stock providers are detected and not counted as new footage.
  The editor also applies a restrained emotion-aware color grade and reuses complete shot renders after an
  interrupted run. Subtitle timings remain estimated from narration length; they are not forced-aligned.
- The editorial pass assigns each scene a story role, varies shot lengths from scene energy and narration density,
  constrains camera movement suggestions, preserves real movement in video footage, and limits synthetic movement
  to restrained treatment of stills. A final review fully decodes the master, measures integrated loudness/true peak,
  samples the visual timeline for near-black frames and repeated-looking shots, and records a review report in the
  secret-free checkpoint. Music is ducked under a single program voice; short transition effects are reserved for
  selected act/emotion changes. The default loudness target is a configurable web-delivery choice, not a universal
  streaming or broadcast standard. Set `audio.target_lufs` and `audio.true_peak_dbtp` for the intended outlet.
- The audio director uses scene emotion, title, category, setting and narration cues to select score profiles. It holds
  a score across adjacent scenes with the same profile, crossfades score changes, lowers music further under key
  information, and applies voice-keyed score/ambience ducking. Environmental SFX are selected only when the scene
  text names a matching environment, are mixed quietly, and are not reused within a video. The generated whoosh is
  limited to one major story pivot per video. Final audio QC measures
  loudness, true peak, long low-level intervals and one-second RMS jumps; perceptual speech-intelligibility review
  still needs a human listen.
- Final QC also checks H.264/AAC codecs, expected 30 fps delivery, stream start-time alignment and sustained
  black/freeze intervals. Black/freeze flags are review warnings because a deliberate black beat or locked-off shot
  can be editorially correct; they are not auto-cut from the story.
- Date graphics are generated only from years stated in the checked narration. The project does not yet have verified
  geographic coordinates or a licensed offline map dataset, so it does not fabricate maps or location paths.
- Memory lives in `memory/` (committed to the repo). Drive holds only the 3-day video/thumbnail buffer.
- Expired Drive files are moved to recoverable Trash instead of being permanently deleted. Repeated uploads of identical
  content reuse the existing Drive file ID, which prevents duplicate media if a run resumes after an interrupted checkpoint.
- CTR metric (`videoThumbnailImpressionsClickRate`) may be unavailable for some channels; the code then
  falls back to view counts.

## Tuning

`config.yaml`: niche/tone, category mix, word counts, voice, privacy, Shorts, retention days, swap threshold.
Run offline checks: `python -m unittest discover -v` (requires FFmpeg/ffprobe). To test generation manually,
`FORCE=1 python -m src.pipeline` still needs API secrets and may upload a video.

See [the post-production coverage audit](docs/POST_PRODUCTION_CAPABILITY_MATRIX.md) for the current A–Z capability
status and real integration gaps.

<!-- CI validation for hook/title-promise repair -->
