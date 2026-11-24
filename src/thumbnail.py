import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
W, H = 1280, 720


def _font(size):
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def grab_frame(video, t, out):
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)],
                   check=True, capture_output=True)


def _wrap(text):
    w = text.upper().split()
    if len(w) <= 2:
        return [" ".join(w)]
    k = (len(w) + 1) // 2
    return [" ".join(w[:k]), " ".join(w[k:])]


def _fit(draw, lines, maxw, start=190):
    size = start
    while size > 50:
        f = _font(size)
        if max(draw.textlength(l, font=f) for l in lines) <= maxw:
            return f, size
        size -= 6
    return _font(50), 50


def _base(frame):
    im = Image.open(frame).convert("RGB")
    r = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * r), int(im.height * r)))
    l, t = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((l, t, l + W, t + H))


def _text(draw, lines, font, size, x, y, fill, stroke=10):
    for i, l in enumerate(lines):
        draw.text((x, y + i * int(size * 1.08)), l, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def make_thumbs(frame, texts, out_dir):
    out_dir = Path(out_dir)
    paths = []
    # A: dark, yellow text bottom-left
    im = ImageEnhance.Brightness(_base(frame)).enhance(0.55)
    im = ImageEnhance.Contrast(im).enhance(1.25)
    d = ImageDraw.Draw(im)
    lines = _wrap(texts[0])
    f, s = _fit(d, lines, 1100)
    _text(d, lines, f, s, 60, H - 60 - int(s * 1.08) * len(lines), (255, 214, 0))
    paths.append(out_dir / "thumb_a.jpg"); im.save(paths[-1], quality=90)
    # B: left gradient, white text with red bar
    im = ImageEnhance.Color(_base(frame)).enhance(1.4)
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for x in range(W):
        gd.line([(x, 0), (x, H)], fill=max(0, int(235 * (1 - x / (W * 0.75)))))
    im.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), grad)
    d = ImageDraw.Draw(im)
    lines = _wrap(texts[1])
    f, s = _fit(d, lines, 720, 170)
    y = (H - int(s * 1.08) * len(lines)) // 2
    d.rectangle([40, y, 58, y + int(s * 1.08) * len(lines)], fill=(220, 30, 30))
    _text(d, lines, f, s, 85, y, (255, 255, 255), 8)
    paths.append(out_dir / "thumb_b.jpg"); im.save(paths[-1], quality=90)
    # C: blurred edges, text on top band
    im = ImageEnhance.Contrast(_base(frame)).enhance(1.35)
    band = Image.new("RGBA", (W, 260), (0, 0, 0, 170))
    im = im.convert("RGBA"); im.alpha_composite(band, (0, 0)); im = im.convert("RGB")
    d = ImageDraw.Draw(im)
    lines = [" ".join(texts[2].upper().split())]
    f, s = _fit(d, lines, 1180, 150)
    _text(d, lines, f, s, (W - int(d.textlength(lines[0], font=f))) // 2, 65, (255, 255, 255), 6)
    paths.append(out_dir / "thumb_c.jpg"); im.save(paths[-1], quality=90)
    return paths
