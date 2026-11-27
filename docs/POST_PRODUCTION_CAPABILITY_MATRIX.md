# Documentary post-production সক্ষমতা নিরীক্ষা

নিরীক্ষার ভিত্তি: `main`-এ সমন্বিত repository code, workflow এবং এই workspace-এ থাকা unit/media tests। “আংশিক” মানে কার্যকর কিছু সুবিধা আছে; এটি পূর্ণ পেশাদার NLE-এর বিকল্প নয়।

| ক্ষেত্র | অবস্থা | বর্তমানে কার্যকর | প্রধান সীমাবদ্ধতা |
|---|---|---|---|
| A — গল্প ও বর্ণনা | আংশিক | গবেষণা, fact-check, scene role, আবেগের নির্দেশনা, গল্প review এবং shot rhythm | মানুষের মতো পূর্ণ-দৈর্ঘ্য গল্প বিচার বা QC থেকে স্বয়ংক্রিয় rewrite নেই |
| B — Media ingest/assets | আংশিক | Stock/Commons/archive/NASA ingest, byte-hash duplicate শনাক্ত, source label, audio license/hash manifest, অনুপস্থিত media fallback | সর্বজনীন codec catalog বা proxy catalog নেই |
| C — Timeline/editing | আংশিক | নির্ধারিত shot plan, assembly, caption/audio layer, checkpoint ও retry | সম্পাদনাযোগ্য NLE timeline, track revision বা montage UI নেই |
| D — Cinematography/framing | আংশিক | asset query/framing, ভিডিওর আসল motion সংরক্ষণ, still ছবিতে সীমিত pan/zoom | subject tracking, stabilization, নিয়ন্ত্রিত tilt/parallax নেই |
| E — Transition/continuity | আংশিক | পরিষ্কার hard cut, score crossfade, গুরুত্বপূর্ণ গল্প বদলে সীমিত whoosh, source matching | video dissolve, match-cut analysis এবং J/L-cut editing logic নেই |
| F — Color | আংশিক | আবেগভিত্তিক FFmpeg contrast/saturation/tint/vignette preset | shot matching, scopes, LUT pipeline, color-space/HDR management নেই |
| G — Audio post | বাস্তবায়িত অংশ | scene-aware score/SFX, অনুমোদিত local/Openverse asset, গুরুত্বপূর্ণ বক্তব্যে music কমানো, voice ducking, EQ/compression/limiting, loudness/peak/silence/RMS review | perceptual intelligibility model বা acoustic noise classification নেই; মানব শ্রবণ-পর্যালোচনা দরকার |
| H — শিরোনাম ও typography | আংশিক | animated opening title, chapter ও source label | lower-third, location card, outro credits animation বা typography review নেই |
| I — Caption/accessibility | আংশিক | phrase cue, দুই-লাইন wrapping, burned caption এবং উপস্থিতি যাচাই | ASR forced alignment, speaker label, translated subtitle track নেই |
| J — Motion graphics | আংশিক | animated hook text ও তৈরি chronology card | পুনর্ব্যবহারযোগ্য keyframe/easing, animated chart বা tracked callout নেই |
| K — VFX/compositing | অনুপস্থিত | masking, tracking, keying, cleanup বা layer compositor নেই | আলাদা compositor এবং shot-aware review দরকার |
| L — Map/geography | নকশাগতভাবে সীমিত | যাচাইহীন map তৈরি হয় না; checked narration-এর সাল থেকে timeline হতে পারে | verified coordinate ও offline map dataset নেই |
| M — 3D/scene creation | অনুপস্থিত | Blender render path নেই | Blender থাকলে শনাক্ত হয়, কিন্তু 3D adapter যুক্ত নয় |
| N — Documentary visual story | আংশিক | narration-matched footage, context card, date timeline, photo motion, chapter structure | evidence-aware chart বা data-source validation engine নেই |
| O — AI-assisted editing | আংশিক | model-assisted script/shot brief; সীমাবদ্ধ deterministic editorial decision | video understanding model বা human-in-the-loop NLE interface নেই |
| P — Performance | আংশিক | FFmpeg `veryfast`, shot reuse, সীমাবদ্ধ audio download, cache-size guard | GPU/CPU detection, adaptive worker pool বা memory-pressure controller নেই |
| Q — Quality control | আংশিক | full decode, codec/FPS/A-V start/duration/resolution check, sampled dark/repeat review, black/freeze interval, loudness/peak/silence/RMS diagnostics | black/freeze event মানব review-এর জন্য; semantic auto-correction নেই |
| R — Render/export | আংশিক | resumable H.264 MP4 16:9 master ও 9:16 Short | ProRes/HEVC, render queue, color tag বা square/HDR preset নেই |
| S — Sound design | আংশিক | সীমিত generated hit/whoosh, license-aware ambience, score crossfade ও source credit | recorded foley library, surround mix বা dynamic perspective automation নেই |
| T — Toolchain | আংশিক | FFmpeg/ffprobe version/filter check, portable `.work/ffmpeg`, ঐচ্ছিক editor inventory | DaVinci Resolve/After Effects/Blender adapter সংযুক্ত নয় |
| U — Automation/orchestration | বাস্তবায়িত অংশ | GitHub Actions, checkpoint restore/save, retry/resume, asset/render reuse | স্বতন্ত্র job queue বা live progress dashboard নেই |
| V — Version/project management | আংশিক | atomic checkpoint, config fingerprint, reusable intermediate file | timeline history বা one-click rollback UI নেই |
| W — Asset/workflow safety | আংশিক | hash, attribution, safe audio path, bounded download, secret redaction, সীমিত workflow permission | provider license দাবির source-page যাচাই এখনও দরকার |
| X — Extensible architecture | আংশিক | audio/director/toolchain module boundary এবং free FFmpeg fallback | plugin registry, remote worker বা third-party adapter contract নেই |
| Y — YouTube delivery | আংশিক | landscape documentary, portrait Short, YouTube upload ও audio target | square/HDR/বিকল্প frame-rate profile নেই |
| Z — Master review | আংশিক | checkpoint audio/visual/technical QC report, local sample render ও decoder check | পূর্ণ মানব editorial, color ও voice intelligibility sign-off নেই |

## Audio engine যাচাই

`src/audio_director.py` ও `src/edit.py` বিদ্যমান render pipeline-এ যুক্ত। FFmpeg synthesis, অনুমোদিত CC BY music playback, final mux, loudness/true-peak measurement, silence/RMS diagnostics ও audio/video duration validation স্বল্প local smoke media-তে পরীক্ষা হয়েছে। Smoke narration ছিল synthetic test tone—মানুষের কণ্ঠের intelligibility test নয়। Openverse metadata ও licensed music কেবল local test cache-এ ছিল; কোনো track repository-তে যোগ করা হয়নি।

## Editor adapter

শুধু FFmpeg renderer সংযুক্ত। DaVinci Resolve, Adobe After Effects ও Blender ইনস্টল থাকলে inventory-তে দেখা যেতে পারে; তাদের native project/render API integration নেই। Software উপস্থিতি মানেই tested compatibility নয়।
