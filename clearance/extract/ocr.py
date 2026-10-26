"""Local OCR: PDF pages -> lines of text with confidence and position.

RapidOCR (ONNX) runs on CPU with no system dependencies, so the offline path
works on a laptop and in CI.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from ..engine.types import OcrLine, Page
from ..synth.fax import rasterize

# Text detection runs at two scales. The default (736px) occasionally merges two
# tightly spaced lines and drops one; native size (2200px) catches those but
# misses others. Lines found only at native size are added to the first pass.
DETECT_SIZES = (736, 2200)


@lru_cache(maxsize=2)
def _engine(limit: int):
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(det_limit_side_len=limit)


def _lines(img: Image.Image, limit: int) -> list[OcrLine]:
    w, h = img.size
    result, _ = _engine(limit)(np.array(img.convert("RGB")))
    out = []
    for quad, text, conf in result or []:
        text = unicodedata.normalize("NFKC", text).strip()
        if not text:
            continue
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        out.append(OcrLine(text=text, confidence=float(conf),
                           bbox=[min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h]))
    return out


def _overlaps(a: OcrLine, b: OcrLine) -> bool:
    ix = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
    iy = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    if ix <= 0 or iy <= 0:
        return False
    ha, hb = a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1]
    return iy > 0.5 * min(ha, hb)


def ocr_image(img: Image.Image, number: int) -> Page:
    lines = _lines(img, DETECT_SIZES[0])
    for extra in DETECT_SIZES[1:]:
        lines += [ln for ln in _lines(img, extra) if not any(_overlaps(ln, k) for k in lines)]
    lines.sort(key=lambda ln: (round(ln.bbox[1] * 120), ln.bbox[0]))  # reading order
    return Page(number=number, lines=lines)


CACHE_DIR = Path(__file__).resolve().parents[2] / "var" / "cache" / "ocr"


def ocr_pdf(path: Path, cache: bool = True) -> tuple[list[Page], list[Image.Image]]:
    """OCR every page. Results are cached by file content hash."""
    images = rasterize(path)
    key = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:20]
    cached = CACHE_DIR / f"{key}.json"
    if cache and cached.exists():
        pages = [Page.model_validate(p) for p in json.loads(cached.read_text())]
        return pages, images
    pages = [ocr_image(img, i + 1) for i, img in enumerate(images)]
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps([p.model_dump() for p in pages]))
    return pages, images
