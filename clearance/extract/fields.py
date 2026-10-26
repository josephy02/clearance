"""Offline extractor: classify OCR'd pages, then read each pack field by its labels.

Everything it knows comes from the pack: document-type hints, field labels,
types, and whether a field is printed text or ink (a signature). Adding a pack
needs no code here.

Confidence is the OCR engine's line confidence, discounted when the value had
to be found in a neighbouring text box or parsed loosely. A label with no
readable value is ILLEGIBLE, not MISSING: the page is there, the value isn't.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import numpy as np
from PIL import Image

from ..engine.pack import FieldSpec, Pack
from ..engine.types import ExtractedField, Extraction, FieldStatus, OcrLine, Page

# --- OCR-tolerant matching ---------------------------------------------------------

_CONFUSABLE = str.maketrans({"l": "i", "1": "i", "|": "i", "!": "i", "0": "o"})


_DROP = set(" \t._,'`·-")


def _norm_with_map(s: str) -> tuple[str, list[int]]:
    """Lowercase, drop spaces/stray punctuation, fold OCR-confusable glyphs; map back to `s`."""
    out, idx = [], []
    for i, ch in enumerate(s.lower()):
        if ch in _DROP:
            continue
        out.append(ch.translate(_CONFUSABLE))
        idx.append(i)
    return "".join(out), idx


def _norm(s: str) -> str:
    return _norm_with_map(s)[0]


def classify(page: Page, pack: Pack) -> str | None:
    text = _norm(page.text)
    best, best_score = None, 0
    for dt in pack.extraction.document_types:
        score = sum(1 for h in dt.hints if _norm(h) in text)
        if score > best_score:
            best, best_score = dt.id, score
    return best


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _find_label(line: OcrLine, label: str) -> tuple[int, float] | None:
    """(index in line.text just after 'label:', match quality), or None.

    Exact matches score 1.0. Labels of 8+ characters also match with one OCR
    edit ("Date Sigmed", "Item n Ordered") at a small confidence penalty.
    A colon stuck inside a label ("Date:of Visit") is tolerated.
    """
    text = line.text
    norm, idx = _norm_with_map(text)
    target = _norm(label)
    flat = norm.replace(":", "")
    flat_idx = [idx[i] for i, ch in enumerate(norm) if ch != ":"]

    def boundary(orig_start: int) -> bool:
        return orig_start == 0 or not text[orig_start - 1].isalpha()

    def after_colon(orig_end: int) -> int | None:
        rest = text[orig_end:]
        m = re.match(r"[\s._,'`·-]*[:;]", rest)
        if m:
            return orig_end + m.end()
        if not rest.strip(" ._,'`·-"):
            return len(text)
        return None

    start = 0
    while (pos := flat.find(target, start)) != -1:
        if boundary(flat_idx[pos]):
            cut = after_colon(flat_idx[pos + len(target) - 1] + 1)
            if cut is not None:
                return cut, 1.0
        start = pos + 1

    if len(target) >= 8:
        for m in re.finditer(r"[:;]", text):
            head = _norm(text[: m.start()]).replace(":", "")
            for n in (len(target) - 1, len(target), len(target) + 1):
                if n <= len(head) and _edit_distance(head[-n:], target) <= 1:
                    return m.end(), 0.95
    return None


_NEXT_LABEL = re.compile(r"\s+[A-Z][A-Za-z0-9 ()/#+-]{1,28}:")


def _clip_value(s: str) -> str:
    s = s.strip(" :;\t.,'`·")
    m = _NEXT_LABEL.search(s)
    return (s[: m.start()] if m else s).strip(" ,;")


def rows(page: Page) -> list[OcrLine]:
    """Merge OCR boxes that sit on the same text row and nearly touch.

    Rotated faxes make the detector split 'Study' from 'Type: PSG'; reading a
    row as one line puts labels back next to their values.
    """
    merged: list[OcrLine] = []
    for ln in sorted(page.lines, key=lambda b: b.bbox[0]):
        h = ln.bbox[3] - ln.bbox[1]
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        for m in merged:
            mh = m.bbox[3] - m.bbox[1]
            mcy = (m.bbox[1] + m.bbox[3]) / 2
            gap = ln.bbox[0] - m.bbox[2]
            if abs(cy - mcy) < 0.6 * min(h, mh) and -0.02 < gap < 0.04:
                m.text = f"{m.text} {ln.text}"
                m.confidence = min(m.confidence, ln.confidence)
                m.bbox = [m.bbox[0], min(m.bbox[1], ln.bbox[1]), ln.bbox[2],
                          max(m.bbox[3], ln.bbox[3])]
                break
        else:
            merged.append(OcrLine(text=ln.text, confidence=ln.confidence, bbox=list(ln.bbox)))
    merged.sort(key=lambda b: (round(b.bbox[1] * 120), b.bbox[0]))
    return merged


def _right_neighbour(line: OcrLine, page: Page) -> OcrLine | None:
    y0, y1 = line.bbox[1], line.bbox[3]
    cands = [ln for ln in page.lines if ln is not line and ln.bbox[0] >= line.bbox[2] - 0.01
             and min(y1, ln.bbox[3]) - max(y0, ln.bbox[1]) > 0.4 * (y1 - y0)]
    return min(cands, key=lambda ln: ln.bbox[0], default=None)


# --- value parsing -------------------------------------------------------------------

_DATE_FORMATS = ["%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y",
                 "%B %d %Y", "%m/%d/%y"]
_DATE_RE = re.compile(
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s*\d{1,2},?\s*\d{4}"
    r"|\d{1,2}-[A-Za-z]{3}-\d{4}")


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_WORD_DATE = re.compile(r"([A-Za-z][A-Za-z10]{2,8})\W*(\d{1,2})\W+(\d{4})")


def _word_date(s: str) -> date | None:
    """'Apri1.25,2026', 'May9.2026', 'August:2,. 2026' -> date. OCR-tolerant month names."""
    for m in _WORD_DATE.finditer(s):
        word = m.group(1).lower().translate(str.maketrans({"1": "l", "0": "o"}))
        month = next((i + 1 for i, name in enumerate(_MONTHS) if word.startswith(name)), None)
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                continue
    return None


def parse_date(s: str) -> date | None:
    s = re.sub(r"(?<=[A-Za-z])[:;.,]+\s*(?=\d)", " ", s)      # "August:2" -> "August 2"
    s = re.sub(r"(?<=\d),[\s.:;]*(?=\d{4})", ", ", s)          # "2,. 2026" -> "2, 2026"
    for m in _DATE_RE.finditer(s):
        token = re.sub(r"\s+", " ", m.group(0).replace(".", "")).strip()
        token = re.sub(r"([A-Za-z])(\d)", r"\1 \2", token)
        token = re.sub(r",(\d)", r", \1", token)
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(token, fmt).date()
            except ValueError:
                continue
    return _word_date(s)


def _fix_digits(s: str) -> str:
    return s.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"}))


def parse_value(spec: FieldSpec, raw: str):
    """Returns (value, quality 0-1) or (None, 0) if the text can't be parsed as this type."""
    raw = raw.strip()
    if not raw:
        return None, 0.0
    if spec.type == "date":
        d = parse_date(raw)
        return (d.isoformat(), 1.0) if d else (None, 0.0)
    if spec.type in ("number", "integer"):
        for tok in raw.split():
            if not re.search(r"\d", tok):
                continue
            fixed = _fix_digits(tok)
            m = re.match(r"\d+(?:\.\d+)?", fixed)
            if m:
                quality = 1.0 if fixed[: m.end()] == tok[: m.end()] else 0.85
                num = float(m.group(0))
                return (int(num) if spec.type == "integer" else num), quality
        return None, 0.0
    if spec.type == "enum":
        hits = [v for v in spec.values or [] if re.search(rf"\b{re.escape(v)}\b", raw, re.I)]
        return (hits[0], 1.0) if len(hits) == 1 else (None, 0.0)
    if spec.type == "boolean":
        low = raw.lower()
        if re.search(r"\b(yes|true|signed|x)\b", low):
            return True, 0.95
        if re.search(r"\b(no|false|unsigned)\b", low):
            return False, 0.95
        return None, 0.0
    if spec.name.endswith(("npi", "_id")) or "id" in spec.name.split("_"):
        tokens = [re.sub(r"[^A-Za-z0-9]", "", t) for t in raw.split()]
        token = next((t for t in tokens if len(t) >= 4), "")
        return (token.upper(), 1.0) if token else (None, 0.0)
    return _clean_text(raw), 1.0


def _clean_text(s: str) -> str:
    s = re.sub(r"(?<=[a-z])\.(?=[A-Z])", " ", s)        # "Delphine.Halvorsen"
    s = re.sub(r"\s*([,;])[.\s]*", r"\1 ", s)           # "Marchetti,.MD"
    return re.sub(r"\s+", " ", s).strip(" .")


# --- ink detection (signatures, check marks) -------------------------------------------

INK_THRESHOLD = 0.008
LONG_RUN = 30            # px at 200 dpi: ruled lines, not handwriting


def _strip_long_runs(mask: np.ndarray) -> np.ndarray:
    """Remove horizontal runs of ink longer than LONG_RUN (underlines, fax streaks)."""
    out = mask.copy()
    for r, row in enumerate(mask):
        padded = np.concatenate(([0], row.astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(padded))
        for a, b in zip(edges[::2], edges[1::2], strict=True):
            if b - a >= LONG_RUN:
                out[r, max(0, a - 1):b + 1] = False
                if r + 1 < len(out):
                    out[r + 1, a:b] = False
                if r > 0:
                    out[r - 1, a:b] = False
    return out


def ink_to_right(img: Image.Image, line: OcrLine) -> tuple[bool, float]:
    """Is there handwriting-like ink right of a label? Ignores ruled lines and speckle."""
    w, h = img.size
    x0, y0, x1, y1 = line.bbox
    lh = (y1 - y0) * h
    box = (int(x1 * w + 6), int(y0 * h - 1.6 * lh), int(min(w, x1 * w + 0.42 * w)), int(y1 * h))
    region = np.asarray(img.crop(box).convert("L")) < 128
    if region.size == 0:
        return False, 0.0
    dark = float(_strip_long_runs(region).mean())
    margin = abs(dark - INK_THRESHOLD) / INK_THRESHOLD
    conf = min(0.99, 0.7 + 0.29 * min(margin, 1.0))
    return dark > INK_THRESHOLD, conf


# --- the extractor ---------------------------------------------------------------------

class OcrFieldExtractor:
    name = "ocr-rules"

    def extract(self, pack: Pack, pages: list[Page],
                images: list[Image.Image] | None = None) -> Extraction:
        for page in pages:
            page.document_type = classify(page, pack)
        fields = {spec.name: self._field(spec, pack, pages, images)
                  for spec in pack.extraction.fields}
        return Extraction(fields=fields, pages=pages, extractor=self.name)

    def _field(self, spec: FieldSpec, pack: Pack, pages: list[Page],
               images: list[Image.Image] | None) -> ExtractedField:
        scoped = [p for p in pages if spec.document_type in (None, p.document_type)]
        best: ExtractedField | None = None
        label_seen: ExtractedField | None = None
        for label in spec.labels:
            for page in scoped:
                for line in rows(page):
                    hit = _find_label(line, label)
                    if hit is None:
                        continue
                    cand = self._read(spec, page, line, hit[0], images, hit[1])
                    if cand.status == FieldStatus.FOUND:
                        if best is None or cand.confidence > best.confidence:
                            best = cand
                    elif label_seen is None:
                        label_seen = cand
            if best is not None:           # labels are in priority order
                return best
        return label_seen or ExtractedField(name=spec.name, status=FieldStatus.MISSING)

    def _read(self, spec, page, line, cut, images, label_q=1.0) -> ExtractedField:
        base = dict(name=spec.name, page=page.number, bbox=line.bbox)
        if spec.detect == "ink":
            if not images:
                return ExtractedField(**base, status=FieldStatus.ILLEGIBLE, confidence=0.0)
            signed, conf = ink_to_right(images[page.number - 1], line)
            conf = round(conf * line.confidence * label_q, 3)
            return ExtractedField(**base, value=signed, confidence=conf,
                                  status=FieldStatus.FOUND, evidence=line.text)
        raw, factor, evidence = _clip_value(line.text[cut:]), 1.0, line.text
        if not raw:
            nb = _right_neighbour(line, page)
            if nb is not None:
                raw, factor, evidence = _clip_value(nb.text), 0.92, f"{line.text} {nb.text}"
                base["bbox"] = [line.bbox[0], min(line.bbox[1], nb.bbox[1]), nb.bbox[2],
                                max(line.bbox[3], nb.bbox[3])]
        value, quality = parse_value(spec, raw)
        if value is None:
            return ExtractedField(**base, status=FieldStatus.ILLEGIBLE, confidence=0.1,
                                  evidence=evidence)
        conf = round(line.confidence * factor * quality * label_q, 3)
        return ExtractedField(**base, value=value, confidence=conf, status=FieldStatus.FOUND,
                              evidence=evidence)
