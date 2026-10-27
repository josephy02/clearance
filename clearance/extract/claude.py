"""Claude vision extractor, cross-checked against local OCR.

Claude reads the page images against the pack's field schema. The OCR extractor
reads the same pages independently. Two readers that agree earn high
confidence; a disagreement drops the field below the auto-ready bar (or makes
it unverifiable), so a person looks. OCR also supplies the bounding boxes the
review UI highlights.
"""

from __future__ import annotations

import base64
import io
import json
import os

from PIL import Image

from ..engine.judge import DEFAULT_MODEL, PRICES
from ..engine.pack import Pack
from ..engine.types import ExtractedField, Extraction, FieldStatus, Page
from .fields import OcrFieldExtractor, _norm, parse_value

SINGLE_READER_CAP = 0.9     # only one reader found it: never auto-ready on that alone
DISAGREE_CONF = 0.4         # readers disagree: below unverifiable_below, a person decides

_PROMPT = """You are reading a faxed referral packet for durable medical equipment.
Extract the fields below exactly as written in the packet. The pages are in order (page 1 first).

Fields:
{fields}

Document types to label each page with: {doc_types}, or "other" if none fits.

Return one entry in "fields" for every field listed, with:
- status: "found", "missing" (nothing in the packet gives this), or "illegible"
  (the label is there but the value can't be read with confidence; do not guess).
- value: the value as printed (dates as printed; booleans "true"/"false"), or "" if none.
- page: 1-based page number where you read it, or 0 if none.
- evidence: the printed text you read it from, copied exactly (e.g. "AHI: 22.4 /hr").
- confidence: 0.0-1.0.
For signature fields: "true" only if there is a handwritten or electronic signature, not just
a blank signature line."""


def _schema(pack: Pack) -> dict:
    names = [f.name for f in pack.extraction.fields]
    field_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": names},
            "status": {"type": "string", "enum": ["found", "missing", "illegible"]},
            "value": {"type": "string"},
            "page": {"type": "integer"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["name", "status", "value", "page", "evidence", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "pages": {"type": "array", "items": {
                "type": "object",
                "properties": {"page": {"type": "integer"},
                               "document_type": {"type": "string"}},
                "required": ["page", "document_type"], "additionalProperties": False}},
            "fields": {"type": "array", "items": field_schema},
        },
        "required": ["pages", "fields"],
        "additionalProperties": False,
    }


def _png_b64(img: Image.Image, max_side: int = 1400) -> str:
    img = img.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert("L").save(buf, "PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _same(spec, a, b) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) < 1e-6
    x, y = _norm(str(a)), _norm(str(b))
    x, y = "".join(c for c in x if c.isalnum()), "".join(c for c in y if c.isalnum())
    return x == y or (len(x) >= 4 and (x in y or y in x))


class ClaudeExtractor:
    def __init__(self, model: str | None = None, client=None):
        import anthropic

        self.model = model or os.environ.get("CLEARANCE_MODEL") or DEFAULT_MODEL
        self.name = f"{self.model}+ocr"
        self._client = client or anthropic.Anthropic()
        self.ocr = OcrFieldExtractor()
        self.spent_usd = 0.0
        self.last_error: str | None = None

    def _ask(self, pack: Pack, images: list[Image.Image]) -> dict | None:
        fields = "\n".join(
            f"- {f.name} ({f.type}{': ' + ', '.join(f.values) if f.values else ''})"
            f"{' printed as: ' + ' / '.join(f.labels) if f.labels else ''}"
            f"{' [signature or check mark]' if f.detect == 'ink' else ''}"
            for f in pack.extraction.fields)
        doc_types = ", ".join(d.id for d in pack.extraction.document_types)
        content = []
        for i, img in enumerate(images):
            content.append({"type": "text", "text": f"Page {i + 1}:"})
            content.append({"type": "image", "source": {"type": "base64",
                            "media_type": "image/png", "data": _png_b64(img)}})
        content.append({"type": "text", "text": _PROMPT.format(fields=fields,
                                                                doc_types=doc_types)})
        try:
            msg = self._client.messages.create(
                model=self.model, max_tokens=16000,
                output_config={"format": {"type": "json_schema", "schema": _schema(pack)}},
                messages=[{"role": "user", "content": content}],
            )
        except Exception as err:  # noqa: BLE001 - fall back to OCR alone; reason is recorded
            self.last_error = f"{type(err).__name__}: {err}"[:300]
            return None
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        self.spent_usd += (msg.usage.input_tokens * price_in
                           + msg.usage.output_tokens * price_out) / 1e6
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            self.last_error = f"unparseable reply ({err}); stop_reason={msg.stop_reason}"
            return None

    def extract(self, pack: Pack, pages: list[Page],
                images: list[Image.Image] | None = None) -> Extraction:
        local = self.ocr.extract(pack, pages, images)
        reply = self._ask(pack, images or []) if images else None
        if reply is None:
            local.extractor = f"{self.ocr.name} (claude unavailable)"
            return local

        known = {d.id for d in pack.extraction.document_types}
        for p in reply.get("pages", []):
            page = next((x for x in local.pages if x.number == p.get("page")), None)
            if page and p.get("document_type") in known:
                page.document_type = p["document_type"]

        read = {f.get("name"): f for f in reply.get("fields", [])}
        merged = {}
        for spec in pack.extraction.fields:
            o = local.fields[spec.name]
            c = read.get(spec.name) or {}
            merged[spec.name] = self._reconcile(spec, o, c)
        return Extraction(fields=merged, pages=local.pages, extractor=self.name)

    def _reconcile(self, spec, o: ExtractedField, c: dict) -> ExtractedField:
        status = c.get("status", "missing")
        cconf = min(1.0, max(0.0, float(c.get("confidence") or 0.0)))
        value = None
        if status == "found" and c.get("value") not in (None, ""):
            raw = str(c["value"])
            if spec.type == "boolean":
                value = raw.strip().lower() in ("true", "yes", "signed", "1")
            else:
                value, _ = parse_value(spec, raw)
            if value is None:
                status = "illegible"
        evidence = c.get("evidence") or o.evidence
        page = c.get("page") or o.page

        if status != "found":
            if o.status == FieldStatus.FOUND and status == "missing":
                # Only OCR saw it.
                return o.model_copy(update={"confidence": min(o.confidence, SINGLE_READER_CAP)})
            return ExtractedField(name=spec.name, status=FieldStatus(status), page=page,
                                  evidence=evidence, bbox=o.bbox, confidence=cconf * 0.5)
        if o.status != FieldStatus.FOUND:
            return ExtractedField(name=spec.name, value=value, status=FieldStatus.FOUND,
                                  page=page, evidence=evidence,
                                  confidence=round(min(cconf, SINGLE_READER_CAP), 3))
        if _same(spec, o.value, value):
            return o.model_copy(update={"value": value,
                                        "confidence": round(max(o.confidence, cconf), 3)})
        return ExtractedField(name=spec.name, value=value, status=FieldStatus.FOUND, page=page,
                              evidence=f"{evidence} (OCR read: {o.value})", bbox=o.bbox,
                              confidence=DISAGREE_CONF)
