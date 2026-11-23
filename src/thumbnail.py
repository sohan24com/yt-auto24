import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
W, H = 1280, 720


def _font(size):
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def grab_frame(video, t, out):
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".part.jpg")
    seek_t = max(0.0, float(t))
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{seek_t:.2f}",
            "-i", str(video),
            "-frames:v", "1",
            "-q:v", "2",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    if not tmp_path.exists() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("failed to grab thumbnail frame")
    tmp_path.replace(out_path)


def _wrap(text):
    w = str(text or "").upper().split()
    if not w:
        return [""]
    if len(w) <= 2:
        return [" ".join(w)]
    k = (len(w) + 1) // 2
    return [" ".join(w[:k]), " ".join(w[k:])]


def _fit(draw, lines, maxw, start=190):
    size = start
    while size > 52:
        f = _font(size)
        if max(draw.textlength(l, font=f) for l in lines) <= maxw:
            return f, size
        size -= 6
    return _font(52), 52


def _base(frame):
    im = Image.open(frame).convert("RGB")
    r = max(W / max(1, im.width), H / max(1, im.height))
    im = im.resize((int(im.width * r), int(im.height * r)), Image.Resampling.LANCZOS)
    l, t = (im.width - W) // 2, (im.height - H) // 2
    cropped = im.crop((l, t, l + W, t + H))
    return cropped.filter(ImageFilter.UnsharpMask(radius=1.5, percent=135, threshold=3))


def _vignette(im, strength=0.45):
    """Darken outer corners/edges so the subject and headline pop on mobile feeds."""
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    pad_x = int(W * 0.14)
    pad_y = int(H * 0.14)
    md.ellipse([pad_x, pad_y, W - pad_x, H - pad_y], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=95))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    blended = Image.composite(im, dark, mask)
    return Image.blend(im, blended, max(0.0, min(1.0, strength)))


def _text(draw, lines, font, size, x, y, fill, stroke=11, shadow_offset=(4, 5)):
    line_step = int(size * 1.08)
    for i, l in enumerate(lines):
        ly = y + i * line_step
        if shadow_offset:
            sx, sy = shadow_offset
            draw.text(
                (x + sx, ly + sy),
                l,
                font=font,
                fill=(0, 0, 0),
                stroke_width=max(2, stroke // 2),
                stroke_fill=(0, 0, 0),
            )
        draw.text(
            (x, ly),
            l,
            font=font,
            fill=fill,
            stroke_width=stroke,
            stroke_fill=(0, 0, 0),
        )


def _normalize_texts(texts):
    cleaned = [" ".join(str(t or "").split()).strip() for t in (texts or [])]
    cleaned = [t for t in cleaned if t]
    if not cleaned:
        cleaned = ["WHY NO ONE KNEW"]
    while len(cleaned) < 3:
        cleaned.append(cleaned[-1])
    return cleaned[:3]


def _blurred_edges_base(frame):
    sharp = ImageEnhance.Contrast(_base(frame)).enhance(1.38)
    soft = sharp.filter(ImageFilter.GaussianBlur(radius=16))
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle([75, 55, W - 75, H - 55], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=48))
    return Image.composite(sharp, soft, mask)


def make_thumbs(frame, texts, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_texts = _normalize_texts(texts)
    paths = []

    # A: dramatic dark vignette, bold yellow hook text bottom-left + bottom accent bar
    im = _vignette(ImageEnhance.Brightness(_base(frame)).enhance(0.52), strength=0.55)
    im = ImageEnhance.Contrast(im).enhance(1.30)
    d = ImageDraw.Draw(im)
    lines = _wrap(safe_texts[0])
    f, s = _fit(d, lines, 1120)
    block_h = int(s * 1.08) * len(lines)
    text_y = H - 68 - block_h
    d.rectangle([60, H - 42, 60 + min(340, max(140, int(d.textlength(lines[0], font=f) * 0.55))), H - 32],
                 fill=(255, 214, 0))
    _text(d, lines, f, s, 60, text_y, (255, 214, 0), stroke=11)
    p_a = out_dir / "thumb_a.jpg"
    tmp_a = p_a.with_suffix(".part.jpg")
    im.save(tmp_a, quality=93, optimize=True)
    tmp_a.replace(p_a)
    paths.append(p_a)

    # B: rich color + strong left gradient, crisp white text with crimson power bar
    im = _vignette(ImageEnhance.Color(_base(frame)).enhance(1.42), strength=0.35)
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for x in range(W):
        gd.line([(x, 0), (x, H)], fill=max(0, min(255, int(242 * (1 - x / (W * 0.76))))))
    im.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), grad)
    d = ImageDraw.Draw(im)
    lines = _wrap(safe_texts[1])
    f, s = _fit(d, lines, 740, 172)
    block_h = int(s * 1.08) * len(lines)
    y = (H - block_h) // 2
    d.rectangle([38, y - 6, 58, y + block_h + 6], fill=(230, 32, 32))
    _text(d, lines, f, s, 84, y, (255, 255, 255), stroke=9)
    p_b = out_dir / "thumb_b.jpg"
    tmp_b = p_b.with_suffix(".part.jpg")
    im.save(tmp_b, quality=93, optimize=True)
    tmp_b.replace(p_b)
    paths.append(p_b)

    # C: blurred edges + high-contrast top headline band + subtle gold divider
    im = _blurred_edges_base(frame)
    band = Image.new("RGBA", (W, 250), (0, 0, 0, 188))
    im = im.convert("RGBA")
    im.alpha_composite(band, (0, 0))
    im = im.convert("RGB")
    d = ImageDraw.Draw(im)
    line_c = safe_texts[2].upper()
    f, s = _fit(d, [line_c], 1180, 152)
    text_w = int(d.textlength(line_c, font=f))
    x = max(24, (W - text_w) // 2)
    _text(d, [line_c], f, s, x, 62, (255, 255, 255), stroke=7)
    bar_w = min(260, max(90, text_w // 2))
    bar_x = (W - bar_w) // 2
    d.rectangle([bar_x, 224, bar_x + bar_w, 232], fill=(255, 214, 0))
    p_c = out_dir / "thumb_c.jpg"
    tmp_c = p_c.with_suffix(".part.jpg")
    im.save(tmp_c, quality=93, optimize=True)
    tmp_c.replace(p_c)
    paths.append(p_c)

    return paths
