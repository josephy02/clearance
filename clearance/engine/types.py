"""Data shapes passed between extraction, rule evaluation, and routing."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FieldStatus(StrEnum):
    FOUND = "found"
    MISSING = "missing"        # nothing in the packet looks like this field
    ILLEGIBLE = "illegible"    # the label is there, the value can't be read with confidence


class ExtractedField(BaseModel):
    name: str
    value: Any = None
    confidence: float = 0.0
    status: FieldStatus = FieldStatus.MISSING
    page: int | None = None              # 1-based page in the packet
    evidence: str = ""                   # the text the value was read from
    bbox: list[float] | None = None      # [x0, y0, x1, y1] as fractions of the page
    edited_by: str | None = None         # set when a reviewer corrected the value


class OcrLine(BaseModel):
    text: str
    confidence: float
    bbox: list[float]                    # fractions of page width/height


class Page(BaseModel):
    number: int                          # 1-based
    document_type: str | None = None
    lines: list[OcrLine] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class Extraction(BaseModel):
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    pages: list[Page] = Field(default_factory=list)
    extractor: str = ""                  # e.g. "ocr-rules" or "claude-sonnet-5-5"

    def get(self, name: str) -> ExtractedField:
        return self.fields.get(name) or ExtractedField(name=name)

    def record_text(self) -> str:
        return "\n\n".join(f"[page {p.number}]\n{p.text}" for p in self.pages)


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"        # can't tell from the packet; a human decides
    SKIPPED = "skipped"                  # not needed: a sibling already settled the answer


class Route(StrEnum):
    READY = "ready"
    NEEDS_INFO = "needs_info"
    HUMAN_REVIEW = "human_review"


class Evidence(BaseModel):
    field: str | None = None
    value: Any = None
    confidence: float | None = None
    page: int | None = None
    quote: str = ""
    bbox: list[float] | None = None


class RuleResult(BaseModel):
    rule_id: str
    type: str
    outcome: Outcome
    message: str = ""                    # human-readable reason for the outcome
    source: str | None = None            # pack source id
    citation: str | None = None          # section within the source
    on_fail: Route | None = None
    request: str | None = None           # what to ask the referring office for
    evidence: list[Evidence] = Field(default_factory=list)
    children: list[RuleResult] = Field(default_factory=list)
    llm_involved: bool = False           # an llm_judgment decided this outcome
    detail: dict[str, Any] = Field(default_factory=dict)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()
