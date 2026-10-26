"""Make a clean PDF look like it came off a fax machine.

200 dpi, black and white, slight rotation, speckle, the occasional clipped
margin. Levels: clean (rasterized only), light, heavy. A named field box can be
smudged to plant an "illegible key field" defect.
"""

from __future__ import annotations

import random
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter

DPI = 200
LEVELS = ("clean", "light", "heavy")


def rasterize(pdf_path: Path, dpi: int = DPI) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return [page.render(scale=dpi / 72).to_pil().convert("L") for page in pdf]
    finally:
        pdf.close()


def smudge(img: Image.Image, box: list[float], rng: random.Random) -> Image.Image:
    """Blot out most of a value: a toner smear plus heavy blur over its box."""
    w, h = img.size
    x0, y0, x1, y1 = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    region = img.crop((int(x0), int(y0), int(x1), int(y1)))
    region = region.filter(ImageFilter.GaussianBlur(radius=5))
    img.paste(region, (int(x0), int(y0)))
    draw = ImageDraw.Draw(img)
    for _ in range(int((x1 - x0) * (y1 - y0) / 40)):
        cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
        r = rng.uniform(1.5, 4)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rng.choice([0, 40, 90]))
    return img


def degrade(img: Image.Image, level: str, rng: random.Random) -> Image.Image:
    if level == "clean":
        return img.point(lambda p: 255 if p > 160 else 0).convert("1").convert("L")
    heavy = level == "heavy"
    angle = rng.uniform(-2, 2) if heavy else rng.uniform(-0.8, 0.8)
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    if heavy:
        img = img.filter(ImageFilter.GaussianBlur(radius=0.7))
    w, h = img.size
    draw = ImageDraw.Draw(img)
    for _ in range(int(w * h * (0.0012 if heavy else 0.0003))):
        x, y = rng.randrange(w), rng.randrange(h)
        draw.point((x, y), fill=0)
    if heavy and rng.random() < 0.35:  # clipped margin
        edge = rng.choice(["left", "top", "right"])
        cut = int(rng.uniform(0.02, 0.05) * (w if edge != "top" else h))
        box = {"left": (0, 0, cut, h), "right": (w - cut, 0, w, h), "top": (0, 0, w, cut)}[edge]
        draw.rectangle(box, fill=255)
    for _ in range(rng.randint(0, 3 if heavy else 1)):  # fax streaks
        y = rng.randrange(h)
        draw.line((0, y, w, y), fill=rng.choice([0, 120]), width=1)
    threshold = 150 if heavy else 165
    return img.point(lambda p: 255 if p > threshold else 0).convert("1").convert("L")


def save_pdf(pages: list[Image.Image], path: Path) -> None:
    first, rest = pages[0].convert("1"), [p.convert("1") for p in pages[1:]]
    first.save(path, "PDF", resolution=DPI, save_all=True, append_images=rest,
               title="SYNTHETIC - TEST DATA")
