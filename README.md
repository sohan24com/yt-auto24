# YT Documentary Bot (free-first, GitHub Actions)

2 documentaries/day (8-10 min, English) for 30 days: topic -> script -> fact-check -> voice -> matched footage ->
mood-aware edit -> quality gate -> distinct thumbnails -> Google Drive -> YouTube (+ story-selected Short) ->
analytics loop. The pipeline uses open-source FFmpeg/Pillow plus APIs where free tiers are available; it keeps safe fallbacks and never
uploads media that fails the media probes.

## Setup (about 45 minutes, once)

1. **Repo**: keep the repository public or private according to your needs. GitHub Actions usage and included minutes
   depend on the repository visibility and your account plan; check GitHub's current limits. Never commit keys;
   keep them in Actions Secrets only.
2. **Gemini keys**: add up to five API keys as GitHub Actions secrets: `GEMINI_API_KEY`,
   `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, `GEMINI_API_KEY_4`, and `GEMINI_API_KEY_5`.
   Invalid-key errors may try another configured key. A Gemini 403 permission/free-tier error or 429 quota/rate-limit
   error stops Gemini retries for the rest of that workflow run; ordinary generation then uses the configured
   provider fallbacks. Do not add keys to work around an exhausted quota. Gemini TTS falls back to Edge TTS and
   then offline `espeak-ng` when it cannot complete.
   Add keys from separate Google Cloud projects if you need separate project quotas; multiple keys in one
   project generally share that project's quota.
3. **Optional text fallbacks**: add `GROQ_API_KEY` and `GROQ_API_KEY_2` for Groq,
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` for Cloudflare Workers AI, and
   `OPENROUTER_API_KEY` for OpenRouter. Ordinary text/JSON generation tries Gemini, Groq, Cloudflare,
   then OpenRouter's `openrouter/free` router only; it never selects a paid OpenRouter model. The free
   router and provider limits vary by account and can change. Grounded fact-check tries Gemini Search first,
   then Groq's required browser_search tool. A documentary claim is publish-eligible only when every retained
   important claim is linked to at least two distinct domains present in provider-returned machine-readable
   grounding metadata. Missing grounding URLs or declared source conflicts hold publishing; conflicts are
   saved for editorial review. This corroboration is automated evidence, not independent human fact-checking.
   An unverified original script is retained for diagnosis, not published. Groq's chat browser-search response
   currently does not expose the same structured source-URL metadata to this pipeline, so it can draft/search
   but cannot by itself satisfy the strict publish evidence gate.
   Groq keys in one organization share quota. Cloudflare also has a daily free allocation.
   Keep all credentials only in Actions secrets. Cohere trial keys are not used by this production workflow:
   Cohere says trial keys are not permitted for production or commercial use; a production key is paid.
4. **Optional media API keys**: Pexels -> `PEXELS_API_KEY`; Pixabay -> `PIXABAY_API_KEY`; Coverr Free Videos API
   Demo/free tier -> `COVERR_API_KEY`. Keep keys only in Actions secrets. The adapters use Pexels' official
   video/photo endpoints and respect its default 200/hour allowance, cache Pixabay search responses for 24 hours
   as required, and use Coverr only as a bounded fallback with `is_premium:false`, attribution and the 50/hour
   Demo quota. Do not use paid Coverr access or premium clips. An optional read-only NARA key may be stored as
   `NARA_CATALOG_API_KEY`; NARA search results remain review-only, are never cached, and are not a reuse license.
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
     GitHub Actions secret. Comments require the additional OAuth scope
     `https://www.googleapis.com/auth/youtube.force-ssl`; the helper requests it for the YouTube token. The runtime
     uses that extra scope only for comments, so an older token can continue uploads and analytics but cannot post
     comments. The new token is saved locally in an ignored file; the helper does not print its value.
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
- **Fact-check** uses Gemini Search grounding and then Groq browser search as a fallback. Citation URLs must
  match provider-returned source metadata, with two distinct domains per important claim. Search result coverage,
  source dates/reliability and model summaries still need editorial review; no automated system proves truth.
- Voice fallback order is Gemini TTS → Edge TTS → offline `espeak-ng`. The last option is less natural, but it does not need a third-party TTS service or API key.
- Footage search is modular across official Pexels/Pixabay/Coverr APIs, Openverse, Wikimedia Commons, NASA,
  Internet Archive, Library of Congress, and optional NARA. Scene intent, story beat, location/period and mood
  drive focused search phrases and ranking for relevance, resolution, duration, license confidence, authenticity,
  motion suitability, and diversity. API errors and rate limits fall through to other available sources.
- Selected media gets a provenance/licensing manifest with source, creator, direct/original links, attribution,
  restrictions, license status, download date, authenticity label, resolution/duration, and SHA-256. The build
  creates `media_credits.txt` and `media_credits.json`; Drive copy of the text credits is best-effort, while the
  required asset manifest remains part of the saved video record. Review-only, noncommercial, no-derivatives,
  and ShareAlike-review items are not downloaded for publishing. NASA, LOC and archive records with ambiguous
  item-level rights are held for review. Illustrative and AI-generated media are labeled separately from
  verified authentic footage. If no relevant safe visual exists, the editor uses a labeled context card.
- Search metadata uses a configurable 24-hour TTL and size cap; selected asset bytes have a separate configurable
  seven-day TTL and cache size in `.media-cache`. GitHub Actions restores that cache between video jobs. NARA
  responses and Coverr signed URLs are never cached. Downloads require HTTPS, enforce a configurable size
  ceiling, validate the file signature, and record a checksum. No bulk download or website scraping is implemented.
- Optional NARA use displays this notice in generated media documentation: "This product uses the National
  Archives Catalog API but is not endorsed or certified by the National Archives and Records Administration."
- Openverse and Wikimedia Commons provide sound-search metadata, but the editor continues to use its licensed
  local tracks and generated sound bed. Pixabay's documented API supports images/videos, not audio. Freesound's
  free API terms are noncommercial, so it is not enabled for this potentially monetized channel. Mixkit and
  Videvo have no supported public search API in this integration; Unsplash API use requires hotlinked images
  and is intended for non-automated experiences. These sites are not scraped.
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
- CTR metric (`videoThumbnailImpressionsClickRate`) may be unavailable for some channels. The analytics
  loop now leaves packaging unchanged unless at least 100 thumbnail impressions and a CTR value are reported.

## Story and retention engine (Phase 3)

- Script drafts use a fixed three-act, 18-scene blueprint with a one-sentence cold open, a central question,
  a title promise, scheduled reveals, and explicit open-loop/payoff IDs. The final scene is reserved for a
  resolved ending and a short natural channel invitation.
- Every scene stores story purpose, new progression, a cause/effect link, an emotion, a narration-to-visual
  anchor, footage keywords, camera movement, animation, sound effect, music mood, transition, and an estimated
  duration. The full story blueprint and Phase 2 research dossier are saved to Drive and referenced in the video record.
- Before footage search/rendering, the code checks three-act coverage, loop closure, source IDs, storyboard
  completeness, title-promise overlap, repeated narration, and visual-anchor overlap. After TTS, it checks the
  actual first scene audio duration (maximum 5 seconds) and actual scene audio intervals (20-45 seconds) before
  continuing. Script drafts require `hook_text`, an explicit narration minimum/target/maximum and a per-scene
  budget. Invalid drafts receive up to five bounded repair attempts with the prior draft and validator feedback;
  visual anchors may be repaired only from narration terms and existing visual options. This does not create
  evidence or guarantee that a generation provider will meet the contract.
- The structural score is a weighted checklist for hook, acts, retention/payoffs, evidence links, storyboard,
  ending, and repetition. It is not a score of factual truth or a guarantee of audience retention. Source
  statements remain search-assisted and need editorial review; visual-anchor matching is a lexical safety check,
  not proof of semantic accuracy.
- A tested research sample is in
  [samples/antikythera_story_blueprint.json](samples/antikythera_story_blueprint.json). Its 2021 front-dial
  model is labeled as a published reconstruction with theoretical parts, not a proven exact replica.
- No paid story services or new paid APIs are introduced by these code changes. This does not guarantee the full
  operation is free: GitHub runner minutes, provider free-tier availability/quotas, Google Cloud project settings,
  and account eligibility can vary. The pipeline does not switch to a paid tier or evade exhausted quotas. This quality workflow uses the existing free provider
  configuration and offline Python tests; it does not publish or upload videos.

## Open research and learning

- Topic research can add small, cached source leads from official NASA Images, Wikimedia, OpenAlex,
  Europe PMC, and Library of Congress APIs. These are research leads only; generated scripts must
  independently verify factual claims through the existing grounded evidence gate. Open-access status
  does not itself authorize media reuse. NASA and Library of Congress results retain item-level rights
  review flags, and Europe PMC results without an explicit allowlisted license remain review-only.
- Documentary craft suggestions are stored in `memory/open_skills.json` as versioned candidates. The
  updater reads Wikiversity educational pages only after the page API reports an allowlisted CC license,
  and OpenAlex abstracts only when the work's best open-access location declares CC BY, CC BY-SA, or CC0.
  It stores the general principle and source/creator/license/revision metadata, not the source text or
  audiovisual material. Skills carry confidence, limitations, and source-match test status; source
  changes are archived and `rollback_skill(skill_id)` restores the previous version.
- Candidate skills and the existing channel-only lessons from `memory/insights.json` are passed into
  topic and script generation as separate inputs. Candidate suggestions are explicitly hypotheses, not
  measured performance findings. The existing analytics loop remains limited to the owner's channel.
  No YouTube competitor analytics, cross-channel aggregation, creator scraping, or derived creator
  performance scores are used. Automatic skill performance promotion is not enabled; assess candidates
  against the channel's own Analytics before changing their status.
- Topic research metadata is cached for seven days in the ignored `.media-cache/open_learning.json`.
  Educational skill metadata is versioned in `memory/open_skills.json`; source text is discarded after
  deterministic concept matching. Provider requests are bounded and sequential; HTTP 429 creates a
  persistent one-hour (or longer provider-requested) pause for that source, and other provider errors
  pause that source for 15 minutes. A failed provider yields no
  fabricated replacement. The APIs were mock-tested; network availability has not been verified by
  offline tests.
- Source references: [NASA media guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/),
  [Wikimedia APIs](https://www.mediawiki.org/wiki/API:REST_API), [OpenAlex API](https://help.openalex.org/api/),
  [Library of Congress API](https://www.loc.gov/apis/), and [Library of Congress rights guidance](https://www.loc.gov/legal/security-copyright-and-privacy/understanding-copyright/).

## Tuning

`config.yaml`: niche/tone, category mix, word counts, voice, privacy, Shorts, retention days, swap threshold.
Run offline checks with `python -m unittest discover -v`. FFmpeg-backed integration tests run when both
`ffmpeg` and `ffprobe` are on `PATH`; otherwise unittest reports those tests as skipped. The CI quality job
installs FFmpeg before running the suite. To test generation manually, `FORCE=1 python -m src.pipeline`
still needs research/provider and YouTube credentials and can publish; do not use it as an offline or
non-publishing test command.

## Local setup, offline checks, and recovery

Use Python 3.11 (the Actions workflows use 3.11) and install the project dependencies in a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install a trusted Windows FFmpeg build from a provider linked by the [FFmpeg download page](https://www.ffmpeg.org/download.html),
then add its `bin` directory to `PATH`. Verify both executables with `ffmpeg -version` and `ffprobe -version`.
Linux CI installs FFmpeg, DejaVu fonts, and `espeak-ng` through Ubuntu packages. The local environment used for
the stabilization run used a SHA-256-verified FFmpeg 9.1 essentials build in the ignored `.ffmpeg-test/`
directory; no system-wide PATH or production setting was changed. To run the complete suite with that local
copy in PowerShell:

```powershell
$env:PATH = "$(Resolve-Path '.ffmpeg-test/ffmpeg-9.1-essentials_build/bin');$env:PATH"
ffmpeg -version
ffprobe -version
```

Run tests without network/API keys using:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

On Windows PowerShell, if Python writes test diagnostics to stderr and the shell reports a misleading native
command failure, run through `cmd.exe /c` or inspect `%ERRORLEVEL%` from `cmd.exe`; the Python process exit
status is authoritative. The verified full suite was run in batches under `cmd.exe`, each returning 0.

The `samples/antikythera_story_blueprint.json` file is a story-structure fixture, not a full offline research
provider. Production `build_once` intentionally requires real topic research and search-linked evidence; it
fails closed when those services/credentials are unavailable instead of inventing research. A full production
`build_once` cannot operate offline. For isolated edit/render verification, the existing Antikythera research,
8-scene script, offline narration, and original illustrative cards were reused without network/API/publishing
calls, then assembled and freshly rendered to
`artifacts/final-stabilization-20261126/antikythera_offline_reverified.mp4`. This fixture-derived output is
47.67 seconds, H.264 1920×1080 at 30 FPS, AAC 44.1 kHz stereo, with burn-in subtitles. FFprobe and deep FFmpeg
decode/audio, subtitle timing, and story/scene QC all passed. The research/script/narration upstream artifacts
were reused from the earlier fixture run rather than regenerated; fixture validation is not live source verification.

Production stage checkpoints live under `.media-cache/builds/<job-id>`. The workflow caches `.media-cache`,
restores the newest matching run/branch cache with a `main` fallback, and saves even after a failed build so
checkpoint files can be resumed. A successful publish removes that job's scratch workspace. GitHub caches are
best-effort accelerators: the workflow YAML proves the restore/save key configuration, but cross-run restore
was not live-tested. Cache eviction or a missing/corrupt artifact causes a rebuild after its integrity check.

## Workload and free-tier limits

The current config targets 1,150–1,400 words and a 450–660-second (7.5–11-minute) final video, not a 20-minute
video. Two videos/day for 30 days would mean 60 production runs. At the configured 170-minute job timeout, the
upper bound would be 10,200 runner minutes/month; actual runtime and account eligibility must be measured.
Standard GitHub-hosted Actions are free for public repositories, while private repositories use plan-dependent
included compute and may incur charges when exhausted. The system cannot guarantee 30 days at zero cost for a
private repository or for third-party free APIs. GitHub's default Actions cache is 10 GB/repository and cache
entries are normally evicted after seven days without access; this project caps its media-asset cache at 512 MB
and individual downloads at 90 MB. See [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[Actions cache limits](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching),
and [YouTube API quota](https://developers.google.com/youtube/data/determine_quota_cost).

YouTube's current quota documentation lists separate daily buckets of 100 `videos.insert` and 100
`search.list` calls, plus 10,000 units/day for other methods; uploads are not the 1,600-unit operation older
guides described. This is sufficient by call count for 60 monthly uploads in isolation, but comments, thumbnail
updates, analytics, retries, and account/project restrictions also matter. Gemini, Groq, Cloudflare, OpenRouter,
and media-provider free allocations can change or be exhausted; the workflow must pause/fallback rather than
enable billing or evade quotas. Thus 2/day for 30 days at zero spend is **not guaranteed**. A 20-minute target
is currently blocked by config/script length constraints and needs a separate, tested change before use.

## Verification status (2026-11-26)

- Full local unittest suite (run in batches to avoid the Windows runner timeout): 111 tests, 111 passed,
  0 failed, 0 skipped; every batch returned process exit code 0. This includes all FFmpeg/FFprobe-backed tests.
- Compilation, `pip check`, and `git diff --check` passed. `pip check` reported no broken requirements.
- Seven production-checkpoint integration tests and the unit checkpoint tests passed, including restart resume,
  corrupted artifact rebuild, input/config invalidation, interrupted edit recovery, evidence failure retry, and
  duplicate-build lock rejection.
- CI workflow YAML and cache policy were inspected locally; no GitHub Actions run or cross-run cache restore
  was performed. No live research/media/YouTube API was called. No video was published or comment posted.
- The fresh fixture-derived render at `artifacts/final-stabilization-20261126/antikythera_offline_reverified.mp4`
  is 12,914,199 bytes, 47.67 seconds, 1920×1080 at 30 FPS, H.264/AAC, with burn-in captions; deep decode,
  audio peak/balance, subtitle timing, scene timing, and story-media QC returned no issues. It uses eight
  illustrative context cards and was not published.
