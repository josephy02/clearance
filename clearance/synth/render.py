"""Small drawing layer over reportlab for synthetic documents.

Every page carries the SYNTHETIC watermark. Values drawn with `kv` record their
bounding box so the fax step can smudge a specific field (the "illegible key
field" defect) and the eval can check where evidence was found.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

W, H = letter
WATERMARK = "SYNTHETIC – TEST DATA"


@dataclass
class Style:
    font: str = "Helvetica"
    bold: str = "Helvetica-Bold"
    size: float = 10.5
    margin: float = 54

    @classmethod
    def pick(cls, rng: random.Random) -> Style:
        return rng.choice([
            cls("Helvetica", "Helvetica-Bold", 10.5),
            cls("Times-Roman", "Times-Bold", 11.5),
            cls("Courier", "Courier-Bold", 10),
        ])


@dataclass
class PageDraw:
    """One page being drawn. Coordinates are reportlab points, origin bottom-left."""

    canvas: Canvas
    style: Style
    rng: random.Random
    y: float = H - 60
    boxes: dict[str, list[float]] = field(default_factory=dict)  # name -> fractional bbox

    # --- primitives --------------------------------------------------------------
    def text(self, x, y, s, size=None, bold=False):
        c = self.canvas
        font = self.style.bold if bold else self.style.font
        c.setFont(font, size or self.style.size)
        c.drawString(x, y, s)
        return c.stringWidth(s, font, size or self.style.size)

    def _record(self, name, x0, y0, x1, y1):
        if name:
            self.boxes[name] = [x0 / W, 1 - y1 / H, x1 / W, 1 - y0 / H]

    def heading(self, s, size=15):
        self.text(self.style.margin, self.y, s, size=size, bold=True)
        self.y -= size + 10

    def subheading(self, s):
        self.y -= 4
        self.text(self.style.margin, self.y, s, size=self.style.size + 1, bold=True)
        self.y -= self.style.size + 8

    def kv(self, label, value, name=None, x=None, gap=8, bold_label=True):
        """'Label: value' on one line. Records the value's bbox under `name`."""
        x = self.style.margin if x is None else x
        lw = self.text(x, self.y, f"{label}:", bold=bold_label)
        vx = x + lw + gap
        vw = self.text(vx, self.y, str(value))
        self._record(name, vx - 2, self.y - 3, vx + vw + 2, self.y + self.style.size + 1)
        self.y -= self.style.size + 8
        return vx, vw

    def kv_row(self, pairs, names=None, col_width=None):
        """Several label/value pairs on one line."""
        names = names or [None] * len(pairs)
        col_width = col_width or (W - 2 * self.style.margin) / len(pairs)
        y = self.y
        for i, ((label, value), name) in enumerate(zip(pairs, names, strict=True)):
            self.y = y
            self.kv(label, value, name=name, x=self.style.margin + i * col_width)
        self.y = y - (self.style.size + 8)

    def para(self, s, width=None, size=None, leading=None):
        size = size or self.style.size
        leading = leading or size + 4
        width = width or W - 2 * self.style.margin
        c = self.canvas
        for raw in s.split("\n"):
            words, line = raw.split(), ""
            for w in words:
                trial = f"{line} {w}".strip()
                if c.stringWidth(trial, self.style.font, size) > width and line:
                    self.text(self.style.margin, self.y, line, size=size)
                    self.y -= leading
                    line = w
                else:
                    line = trial
            if line:
                self.text(self.style.margin, self.y, line, size=size)
            self.y -= leading
        self.y -= 4

    def rule_line(self):
        c = self.canvas
        c.setLineWidth(0.8)
        c.line(self.style.margin, self.y + 6, W - self.style.margin, self.y + 6)
        self.y -= 10

    def checkbox(self, label, checked, x=None):
        x = self.style.margin if x is None else x
        c = self.canvas
        c.setLineWidth(1)
        c.rect(x, self.y - 1, 10, 10)
        if checked:  # hand-drawn check mark
            c.setLineWidth(1.8)
            j = self.rng.uniform(-1, 1)
            p = c.beginPath()
            p.moveTo(x + 1, self.y + 4 + j)
            p.lineTo(x + 4, self.y + 0.5)
            p.lineTo(x + 11 + j, self.y + 11)
            c.drawPath(p, stroke=1, fill=0)
        self.text(x + 16, self.y, label)
        self.y -= self.style.size + 8

    def signature(self, label, signed, name=None, printed=None):
        """Label, then an ink scribble (signed) or an empty line (unsigned)."""
        c = self.canvas
        x = self.style.margin
        lw = self.text(x, self.y, f"{label}:", bold=True)
        sx = x + lw + 12
        c.setLineWidth(0.6)
        c.line(sx, self.y - 2, sx + 190, self.y - 2)
        if signed:
            self._scribble(sx + 6, self.y + 2)
        self._record(name, sx, self.y - 4, sx + 190, self.y + 22)
        self.y -= 26
        if printed:
            self.text(sx, self.y + 8, printed, size=self.style.size - 1.5)
            self.y -= 8

    def _scribble(self, x, y):
        c, r = self.canvas, self.rng
        c.setLineWidth(1.3)
        p = c.beginPath()
        p.moveTo(x, y + r.uniform(0, 8))
        cx = x
        for _ in range(r.randint(5, 8)):
            nx = cx + r.uniform(14, 28)
            p.curveTo(cx + r.uniform(0, 10), y + r.uniform(8, 22),
                      nx - r.uniform(0, 10), y + r.uniform(-4, 6), nx, y + r.uniform(0, 14))
            cx = nx
        c.drawPath(p, stroke=1, fill=0)

    def skip(self, pts=10):
        self.y -= pts


class Document:
    """A multi-page PDF built page by page; remembers each page's field boxes."""

    def __init__(self, path, rng: random.Random, style: Style | None = None):
        self.path = str(path)
        self.canvas = Canvas(self.path, pagesize=letter)
        self.canvas.setTitle(WATERMARK)
        self.rng = rng
        self.style = style or Style.pick(rng)
        self.page_boxes: list[dict[str, list[float]]] = []
        self._current: PageDraw | None = None

    def new_page(self, style: Style | None = None) -> PageDraw:
        if self._current is not None:
            self._finish_page()
        self._current = PageDraw(self.canvas, style or self.style, self.rng)
        return self._current

    def _finish_page(self):
        c = self.canvas
        c.saveState()
        c.setFont("Helvetica-Bold", 9)
        c.setFillGray(0.35)
        c.drawCentredString(W / 2, 22, WATERMARK + " · not a real patient, provider, or order")
        c.restoreState()
        self.page_boxes.append(self._current.boxes)
        c.showPage()

    def save(self):
        if self._current is not None:
            self._finish_page()
            self._current = None
        self.canvas.save()
