# Documentary Thumbnail Intelligence Engine

## অবস্থা ও সীমা

এই ইঞ্জিন existing `src/thumbnail.py` এবং `src/pipeline.py`-এর অংশ। সম্পাদিত shot থেকে বাস্তব frame নেয়, এক run-এ ৯টি মৌলিক layout/concept তৈরি করে এবং প্রাথমিক ফল দুর্বল বা QC-তে সমস্যা থাকলে আরও ১টি পরিমার্জিত concept যোগ করে। শীর্ষ ৩টি JPG (`thumb_a.jpg`, `thumb_b.jpg`, `thumb_c.jpg`) বিদ্যমান Drive/YouTube pipeline-এ যায়; প্রথমটি YouTube upload-এ ব্যবহৃত হয়। পুরনো `make_thumbs()` caller-ও রাখা হয়েছে।

কোনো generative image model বা paid API ব্যবহার করা হয়নি। ফলে ইঞ্জিন কেবল repository-র সম্পাদিত footage frame, crop, split composition, সীমিত contrast adjustment ও typography দিয়ে কাজ করে; নতুন/অদেখা ছবি বানায় না। বাংলা font থাকলে তা ব্যবহার করে, না থাকলে স্থানীয় font fallback নেয়। Font-এর glyph coverage পরিবেশভেদে ভিন্ন হতে পারে।

## কাজের ধাপ

1. pipeline timeline-এর shot-গুলোকে documentary জুড়ে ছড়িয়ে নেয়, সংশ্লিষ্ট scene-এর title, narration, emotion এবং উপলভ্য asset source/match metadata যুক্ত করে।
2. ছোট grayscale perceptual hash দিয়ে প্রায় একই frame শনাক্ত করে; concept তৈরির সময় বেশি দৃশ্যগতভাবে আলাদা frame আগে নেয় এবং duplicate pair রিপোর্টে দেখায়। আসল frame থেকে একক hero, বাঁ/ডান text placement, text-free image, split evidence, detail crop, top-caption, story-pair এবং কম বিশদযুক্ত পাশে স্বয়ংক্রিয় text placement-সহ concept তৈরি হয়। একই frame থাকলেও layout/visual approach আলাদা থাকে; কৃত্রিম subject, arrow বা ভুয়া ঘটনা যোগ হয় না।
3. text কেবল metadata-র আগেই topic/angle/chapter-এ যাচাইকৃত `thumb_texts` থেকে আসে। একটি candidate লেখাহীন থাকে। title-এর সঙ্গে অতিরিক্ত পুনরাবৃত্তি scoring-এ কমে।
4. candidate-গুলো ১০টি design dimension-এ ০–১০০ heuristic score পায়: curiosity, visual impact, documentary relevance, title compatibility, mobile readability, subject clarity, composition, professional quality, originality এবং accuracy। এগুলো সম্পাদকীয় সহায়ক স্কোর; বাস্তব CTR prediction নয়।
5. brightness, contrast, detail ও colorfulness-এর সীমিত local image metric দেখে সমস্যা চিহ্নিত হয়। প্রয়োজন হলে contrast/text placement উন্নত ও text-free বিকল্প তৈরি করে সর্বোচ্চ ২ বার পুনরায় বিচার করা হয়।
6. প্রতিটি candidate-এ 1280×720 মাপ, 16:9 অনুপাত, JPG/PNG format, decode/integrity, file size, source-frame index, factual anchor ও 320×180 mobile preview সংরক্ষিত হয়। সেরা ৩টি আলাদা concept export হয়।

## রিপোর্ট ও checkpoint

`thumbnail_report.json`-এ version, candidate সংখ্যা, concept, frame index, ব্যবহৃত লেখা, প্রতিটি rubric score, image metric, QC issue, mobile preview path, নির্বাচিত candidate ID এবং export path থাকে। pipeline checkpoint-এ report ও engine version রাখা হয়। ভিডিও record-এ exported concept, heuristic score ও Drive ID-র mapping থাকে; analytics কম CTR হলে সেই mapping ধরে পরের ranked option বেছে এবং active concept/score সংরক্ষণ করে। পুরনো record-এ আগের `thumb_ids[1]` fallback কাজ করে। নতুন engine version এলে পুরনো uploaded thumbnail ID-কে নতুন variant-এর সঙ্গে ভুলভাবে মেলানো হয় না; Drive-এ আগের ফাইল মুছে ফেলা হয় না। একই version-এ পুনরায় run হলে বিদ্যমান ID পুনর্ব্যবহার করা যায়।

## বাস্তব সীমাবদ্ধতা

- local heuristic ছবির subject-কে মানুষ/বস্তু হিসেবে শনাক্ত করে না; তাই object detection, face/emotion reading, semantic accuracy verification বা composition-aware subject masking নেই।
- mobile preview তৈরি হয় এবং font-size/layout নিয়ম দিয়ে readability আনুমানিক বিচার হয়; OCR-ভিত্তিক glyph contrast verification বা বাস্তব ফোনে মানব review নেই।
- QC documentary-র সমস্ত factual assertion ছবির বিরুদ্ধে যাচাই করতে পারে না। Factual overlay শুধু upstream metadata validation-এ নির্ভরশীল; visual evidence-কে narration-এর সঙ্গে AI দিয়ে semantically cross-check করা হয় না।
- style score গবেষণাভিত্তিক CTR বা দর্শক-পরীক্ষার ফল নয়; A/B result ছাড়া click performance দাবি করা যাবে না।
- online image search বা image generation করে নতুন thumbnail asset সংগ্রহ করা হয় না। এতে license provenance ও ভুয়া visual এড়ানো যায়; বৈচিত্র্য বিদ্যমান footage-এর উপর নির্ভর করে।

## যাচাই

`tests/test_thumbnail_intelligence.py` ধারণা সংখ্যা, 1280×720 JPEG export, mobile preview, report persistence, title-text overlap scoring, text-free option এবং backward-compatible API যাচাই করে। সম্পূর্ণ suite-এর ফল run report-এ আলাদা করে জানাতে হবে; এই নথি একা test pass হওয়ার দাবি করে না।
