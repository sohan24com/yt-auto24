# Main শাখায় সমন্বয় ও যাচাই

## উৎপাদন প্রবাহ

`src.pipeline` একক production entry point। এটি গল্প ও evidence validation বজায় রেখে scene role ও shot rhythm নির্ধারণ করে, narration/visual/audio render করে, তারপর ভিডিও, subtitle, audio, asset license এবং thumbnail যাচাই করে। সফল render-এর পরে Drive/YouTube/Short ধাপের জন্য idempotent job state সংরক্ষিত হয়।

## পুনরুদ্ধার ও পুনরায় চালানো

`CHECKPOINT_DIR`-এর অধীনে প্রতিটি job-এর checkpoint আলাদা রাখা হয়। Checkpoint-এ topic, যাচাইকৃত script, voice duration, shot geometry এবং footage asset path/hash থাকে। একই config, তারিখ ও job ID-তে run retry হলে পূর্বের সম্পূর্ণ artifact পুনর্ব্যবহার করা যায়; config বা job বদলালে নতুন directory তৈরি হয়। অসম্পূর্ণ/অপাঠ্য checkpoint উপেক্ষা করে নতুন state তৈরি হয়।

GitHub Actions-এ checkpoint ও audio library cache restore/save হয়। Cache কেবল ephemeral build data; credentials checkpoint-এ রাখা হয় না।

Script generation-এ configured word target midpoint এবং scene-wise narration budget দেওয়া হয়। Validation-এ শব্দসংখ্যা বেশি হলে retry-তে measured count ও hard maximum স্পষ্ট করে দিয়ে repeated exposition সংকুচিত করতে বলা হয়; ৩টি draft-এর পরও invalid থাকলে render/publish বন্ধ থাকে।

## Audio, visual এবং thumbnail

- `src/director.py` scene role, shot length, camera move ও সীমিত sound cue পরিকল্পনা করে।
- `src/audio_director.py` mood-ভিত্তিক licensed local/online music ও scene-explicit ambience নির্বাচন করে; voice ducking ও loudness target `config.yaml`-এর `audio` section নিয়ন্ত্রণ করে।
- `src.edit` normalized render, captions, source label, restrained scene grade ও audio report তৈরি করে। Explicit `visual_plan` reveal থাকলে সীমিত animated callout দেয়।
- `src.thumbnail` actual render frame থেকে concept candidate বানায়; score একটি heuristic, CTR prediction নয়। নির্বাচিত export-এর image integrity/dimensions যাচাই করে package-এ রাখে।

## সীমাবদ্ধতা ও নিরাপদ ব্যবহার

পূর্ণ production render এই integration check-এ চালানো হয়নি, কারণ সেটি external research/provider, Drive ও YouTube credentials ব্যবহার এবং publish করতে পারে। FFmpeg sample render ও media QC আলাদা synthetic fixture-এ পরীক্ষা করা হয়েছে। Optional Resolve/After Effects/cloud editor সংযোগ inventory/future adapter পর্যায়ে; স্বয়ংক্রিয় session export দাবি করা হয় না।

Actions workflow-এ পূর্ব থেকেই memory save-এর জন্য commit/push ধাপ রয়েছে। এই local কাজ সেই workflow চালায়নি এবং কোনো push/deploy/upload করেনি। Repository owner চাইলে workflow trigger/secrets আলাদাভাবে পর্যালোচনা করবেন।
