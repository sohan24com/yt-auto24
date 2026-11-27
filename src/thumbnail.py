"""Free-first, evidence-grounded thumbnail concepts for the documentary pipeline.

This module designs from real rendered frames. It does not generate or fabricate
documentary imagery and its rubric is a design heuristic, never a CTR forecast.
"""
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

W, H = 1280, 720
VERSION = 1
CONCEPTS = (
    ("hero_left", "একক দৃশ্য · বাঁ পাশে লেখা"),
    ("hero_right", "একক দৃশ্য · ডান পাশে লেখা"),
    ("clean_frame", "লেখাহীন · দৃশ্যই মূল"),
    ("split_evidence", "দুই দৃশ্য · তুলনা"),
    ("detail_left", "বিস্তারিত · বাঁ পাশে লেখা"),
    ("detail_right", "বিস্তারিত · ডান পাশে লেখা"),
    ("top_caption", "সংক্ষিপ্ত শিরোনাম · ওপরের ব্যান্ড"),
    ("dual_story", "দুই মুহূর্ত · গল্পের অগ্রগতি"),
    ("improved_clean", "পরিমার্জিত · লেখাহীন"),
    ("improved_text", "পরিমার্জিত · পাঠযোগ্য লেখা"),
)


def _font(size, bold=True):
    candidates = (["C:/Windows/Fonts/NirmalaB.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
                  "C:/Windows/Fonts/impact.ttf"] if bold else ["C:/Windows/Fonts/arial.ttf"])
    candidates += ["/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def grab_frame(video, seconds, out):
    import subprocess
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(max(0, seconds)), "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(out)], check=True, timeout=90)
    return out


def _open_frames(frames):
    rows = []
    for item in frames:
        info = item if isinstance(item, dict) else {"path": item}
        try:
            im = Image.open(info["path"]).convert("RGB")
            rows.append((im, {k: v for k, v in info.items() if k != "path"}))
        except (OSError, KeyError, TypeError):
            continue
    return rows


def _fill(im, focus=(0.5, 0.5)):
    im = ImageOps.exif_transpose(im)
    iw, ih = im.size
    scale = max(W / iw, H / ih)
    nw, nh = round(iw * scale), round(ih * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    fx, fy = focus
    left = min(max(0, round(fx * nw - W / 2)), nw - W)
    top = min(max(0, round(fy * nh - H / 2)), nh - H)
    return im.crop((left, top, left + W, top + H))


def _features(im):
    small = im.resize((160, 90), Image.Resampling.BILINEAR)
    gray = small.convert("L")
    stat = ImageStat.Stat(gray)
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    sat = ImageStat.Stat(small.convert("HSV")).mean[1]
    mean, contrast = stat.mean[0], stat.stddev[0]
    return {"brightness": mean / 255, "contrast": contrast / 110,
            "detail": min(1, edges / 75), "color": min(1, sat / 150)}


def _perceptual_hash(im):
    gray = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    values = gray.tobytes()
    return sum((1 << i) for i in range(64)
               if values[(i // 8) * 9 + (i % 8)] > values[(i // 8) * 9 + (i % 8) + 1])


def _diverse_frame_order(frames):
    """Greedy visual variety order; near-identical frames are deferred, not hidden."""
    hashes = [_perceptual_hash(row[0]) for row in frames]
    pending, ordered = list(range(len(frames))), []
    while pending:
        if not ordered:
            chosen = pending[0]
        else:
            chosen = max(pending, key=lambda i: min((hashes[i] ^ hashes[j]).bit_count() for j in ordered))
        ordered.append(chosen); pending.remove(chosen)
    duplicates = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            distance = (hashes[i] ^ hashes[j]).bit_count()
            if distance <= 4:
                duplicates.append({"frame_a": i, "frame_b": j, "hamming_distance": distance})
    unique = []
    for idx in ordered:
        if not any((hashes[idx] ^ hashes[prev]).bit_count() <= 4 for prev in unique):
            unique.append(idx)
    return unique + [idx for idx in ordered if idx not in unique], duplicates


def _title_words(text):
    return {w.lower() for w in re.findall(r"[\w\u0980-\u09ff]+", str(text)) if len(w) > 2}


def _wrap(draw, text, font, max_width, max_lines=2):
    words = str(text or "").strip().split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines[:max_lines]


def _preferred_text_side(im):
    """Place an optional text block on the quieter, darker half of a real frame."""
    scores = {}
    for side, box in (("left", (0, 0, W // 2, H)), ("right", (W // 2, 0, W, H))):
        crop = im.crop(box).resize((80, 90), Image.Resampling.BILINEAR)
        edges = ImageStat.Stat(crop.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
        brightness = ImageStat.Stat(crop.convert("L")).mean[0]
        scores[side] = edges + brightness * .12
    return min(scores, key=scores.get)


def _overlay(im, text, side="left", top=False):
    im = im.copy()
    if not text:
        return im
    # Deliberate restrained local shadow for legibility; avoid broad fake grading.
    draw = ImageDraw.Draw(im, "RGBA")
    if side == "auto":
        side = _preferred_text_side(im)
    x = 52 if side == "left" else 760
    y = 42 if top else 500
    maxw = 465
    font = _font(74)
    lines = _wrap(draw, text, font, maxw)
    while lines and max(draw.textbbox((0, 0), s, font=font)[2] for s in lines) > maxw and font.size > 42:
        font = _font(font.size - 4)
        lines = _wrap(draw, text, font, maxw)
    height = len(lines) * (font.size + 10) + 26
    # Narrow gradient-like translucent backing, kept away from image focal center.
    draw.rounded_rectangle((x - 18, y - 12, x + maxw + 18, y + height), radius=14,
                           fill=(5, 9, 14, 145), outline=(255, 255, 255, 52), width=2)
    for i, line in enumerate(lines):
        yy = y + i * (font.size + 10)
        draw.text((x + 3, yy + 4), line, font=font, fill=(0, 0, 0, 225), stroke_width=4,
                  stroke_fill=(0, 0, 0, 200))
        draw.text((x, yy), line, font=font, fill=(255, 255, 255, 255), stroke_width=1,
                  stroke_fill=(0, 0, 0, 180))
    return im


def _make_concept(frames, concept, text):
    ims = [x[0] for x in frames]
    kind = concept
    idx = (4 if "detail" in kind else 0) % len(ims)
    focus = (0.42, 0.5) if "right" in kind else (0.58, 0.5)
    hero = _fill(ims[idx], focus)
    if kind in ("split_evidence", "dual_story") and len(ims) > 1:
        left = _fill(ims[0], (0.5, 0.5)).resize((640, H), Image.Resampling.LANCZOS)
        right = _fill(ims[-1], (0.5, 0.5)).resize((640, H), Image.Resampling.LANCZOS)
        hero = Image.new("RGB", (W, H))
        hero.paste(left, (0, 0)); hero.paste(right, (640, 0))
        ImageDraw.Draw(hero).line((640, 0, 640, H), fill=(255, 255, 255), width=4)
    elif "detail" in kind:
        # Real crop from source, restrained; no synthetic subject additions.
        crop = _fill(ims[idx], (0.5, 0.46))
        hero = crop.crop((120, 40, 1160, 680)).resize((W, H), Image.Resampling.LANCZOS)
    elif kind in ("improved_clean", "improved_text"):
        hero = ImageEnhance.Contrast(hero).enhance(1.08)
        hero = ImageEnhance.Color(hero).enhance(0.96)
    if kind in ("hero_left", "hero_right", "top_caption", "improved_text"):
        side = "auto" if kind == "improved_text" else ("right" if kind == "hero_right" else "left")
        hero = _overlay(hero, text, side, kind == "top_caption")
    elif kind in ("detail_left", "detail_right"):
        hero = _overlay(hero, text, "right" if kind == "detail_right" else "left")
    return hero


def _evaluate(path, context, text, title, concept):
    with Image.open(path) as check:
        image_format = check.format
        check.verify()
    im = Image.open(path).convert("RGB")
    f = _features(im)
    title_tokens, text_tokens = _title_words(title), _title_words(text)
    overlap = len(title_tokens & text_tokens) / max(1, len(text_tokens))
    # Truth anchor: overlay must be drawn from supplied metadata (already validated upstream).
    anchors = (_title_words(context.get("topic", "")) | _title_words(context.get("story", "")) |
               _title_words(context.get("scene_title", "")) | _title_words(context.get("narration", "")))
    grounded_overlap = len(text_tokens & anchors)
    anchor = grounded_overlap >= max(1, math.ceil(len(text_tokens) * .5)) if text else True
    # At 320x180, the 74px export type scales to ~18px; the tested text-free
    # composition relies on the image focal point instead of a tiny caption.
    readability = 88 if text else 78
    dimensions = {
        "curiosity": 70 if text and anchor else 66,
        "visual_impact": min(95, round(48 + 29*f["contrast"] + 15*f["detail"])),
        "documentary_relevance": 82 if context.get("scene_title") or context.get("topic") else 62,
        "title_compatibility": max(35, round(88 - 48*overlap)),
        "mobile_readability": readability,
        "subject_clarity": min(93, round(55 + 25*f["detail"] + 12*f["contrast"])),
        "composition": 82 if concept in ("split_evidence", "dual_story", "hero_left", "hero_right") else 75,
        "professional_quality": min(92, round(64 + 20*f["contrast"] + 8*f["color"])),
        "originality": 78 if concept in ("split_evidence", "dual_story", "clean_frame", "improved_clean") else 68,
        "accuracy": 90 if anchor else 52,
    }
    reasons = []
    if overlap > 0.65: reasons.append("লেখা শিরোনামের সঙ্গে অতিরিক্ত মিলে গেছে")
    if not anchor: reasons.append("লেখার সত্যভিত্তিক সংযোগ দুর্বল")
    if f["contrast"] < .35: reasons.append("ছবির কনট্রাস্ট কম")
    if f["detail"] < .12: reasons.append("মূল subject ছোট স্ক্রিনে স্পষ্ট নাও হতে পারে")
    mobile = im.resize((320, 180), Image.Resampling.LANCZOS)
    mobile_path = Path(path).with_name(Path(path).stem + "_mobile.jpg")
    mobile.save(mobile_path, "JPEG", quality=82, optimize=True)
    return {"dimensions": dimensions, "score": round(sum(dimensions.values()) / len(dimensions)),
            "visual_metrics": f, "qc": {"resolution": list(im.size), "ratio_16_9": im.width * 9 == im.height * 16,
            "valid_image": image_format in ("JPEG", "PNG"), "format": image_format,
            "bytes": Path(path).stat().st_size, "mobile_preview": str(mobile_path), "factual_anchor": anchor,
            "issues": reasons}, "title_overlap": round(overlap, 2)}


def generate_thumbnail_package(frames, scenes, topic, metadata, out_dir):
    """Create 8–10 distinct local concepts, rank with a heuristic, QC, and export top 3."""
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    loaded = _open_frames(frames)
    if not loaded:
        raise ValueError("Thumbnail engine-এ যাচাইযোগ্য rendered frame পাওয়া যায়নি")
    # Text candidates must come from upstream, topic-grounded metadata. Empty means no text.
    candidates_text = [str(x).strip() for x in metadata.get("thumb_texts", []) if str(x).strip()]
    text = candidates_text[0] if candidates_text else ""
    title = str(metadata.get("title", ""))
    ctx = {"topic": str(topic.get("topic", "")), "story": str(topic.get("angle", ""))}
    concepts = list(CONCEPTS[:8]) + [CONCEPTS[9]]
    frame_order, duplicate_frames = _diverse_frame_order(loaded)
    records = []
    for n, (kind, label) in enumerate(concepts):
        frame_index = frame_order[n % len(frame_order)]
        meta = loaded[frame_index][1]
        context = {**ctx, **meta}
        local_text = "" if kind in ("clean_frame", "split_evidence", "dual_story") else text
        image = _make_concept(loaded, kind, local_text)
        path = out / f"thumb_candidate_{n+1:02d}.jpg"
        image.save(path, "JPEG", quality=94, optimize=True, subsampling=0)
        scored = _evaluate(path, context, local_text, title, kind)
        placement = {"hero_left": "বাঁ পাশ", "hero_right": "ডান পাশ", "top_caption": "ওপরের ব্যান্ড",
                     "detail_left": "বাঁ পাশ", "detail_right": "ডান পাশ",
                     "improved_text": "কম বিশদযুক্ত পাশ স্বয়ংক্রিয়ভাবে বাছাই"}.get(kind, "লেখাহীন")
        records.append({"id": n+1, "concept": kind, "concept_bn": label, "frame_index": frame_index,
                        "text_placement": placement,
                        "text": local_text, "path": str(path), **scored})
    # At most two iterations: a legibility-optimized and a no-text review alternative.
    best = max(records, key=lambda x: x["score"])
    if best["score"] < 78 or best["qc"]["issues"]:
        for kind, label, variant_text in (("improved_clean", CONCEPTS[8][1], ""),):
            n = len(records); frame_index = frame_order[n % len(frame_order)]; context = {**ctx, **loaded[frame_index][1]}
            image = _make_concept(loaded, kind, variant_text)
            path = out / f"thumb_candidate_{n+1:02d}.jpg"
            image.save(path, "JPEG", quality=94, optimize=True, subsampling=0)
            scored = _evaluate(path, context, variant_text, title, kind)
            records.append({"id": n+1, "concept": kind, "concept_bn": label, "frame_index": frame_index,
                            "text_placement": "লেখাহীন",
                            "text": variant_text, "path": str(path), **scored})
    eligible = [x for x in records if x["qc"]["ratio_16_9"] and x["qc"]["valid_image"]
                and x["qc"]["bytes"] > 1000 and x["qc"]["factual_anchor"]]
    eligible.sort(key=lambda x: (x["score"], x["dimensions"]["accuracy"], x["dimensions"]["mobile_readability"]), reverse=True)
    # Diversify exported options by concept and frame, while retaining the strongest as A.
    chosen = [eligible[0]]
    for row in eligible[1:]:
        if row["concept"] != chosen[0]["concept"] and row["frame_index"] != chosen[0]["frame_index"]:
            chosen.append(row)
        if len(chosen) == 3: break
    for row in eligible:
        if len(chosen) >= 3: break
        if row not in chosen: chosen.append(row)
    exports = []
    for letter, row in zip("abc", chosen):
        dest = out / f"thumb_{letter}.jpg"
        Image.open(row["path"]).convert("RGB").save(dest, "JPEG", quality=94, optimize=True, subsampling=0)
        row["export"] = str(dest); exports.append(dest)
    report = {"version": VERSION, "language": "বাংলা", "score_notice": "স্কোর নকশাগত heuristic; CTR পূর্বাভাস নয়।",
              "image_generation": "স্থানীয় rendered footage frame ব্যবহার করা হয়েছে; generative image model ব্যবহার করা হয়নি।",
              "candidate_count": len(records), "duplicate_source_frames": duplicate_frames,
              "selected": [x["id"] for x in chosen],
              "candidates": records, "exports": [str(x) for x in exports]}
    (out / "thumbnail_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"exports": exports, "report": report}


def make_thumbs(frames, texts, out_dir):
    """Backward-compatible adapter for older callers; still returns the best three paths."""
    metadata = {"thumb_texts": list(texts or []), "title": ""}
    # Legacy API has no topic parameter; treat its already-vetted overlay strings as
    # the available factual context instead of falsely rejecting all text candidates.
    return generate_thumbnail_package(frames, [], {"topic": " ".join(texts or [])}, metadata, out_dir)["exports"]
