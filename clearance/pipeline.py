"""One order, end to end: PDF -> pages -> fields -> rules -> route -> outreach draft."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .engine.judge import Judge, default_judge
from .engine.pack import Pack
from .engine.router import route
from .engine.rules import EvalContext, evaluate_all
from .engine.types import Extraction, Route, RuleResult
from .extract import default_extractor
from .extract.ocr import ocr_pdf
from .integrations.base import EligibilityCheck, ProviderCheck
from .integrations.eligibility import default_eligibility
from .integrations.npi import ChainedProviderCheck, DirectoryProviderCheck, NppesProviderCheck

ROOT = Path(__file__).resolve().parents[1]
ROSTER = ROOT / "data" / "synth" / "providers.json"


class Decision(BaseModel):
    order_id: str
    pack_id: str
    pack_version: str
    route: Route
    reasons: list[str]
    min_confidence: float | None
    threshold: float
    results: list[RuleResult]
    extraction: Extraction
    missing_items: list[str] = Field(default_factory=list)
    outreach: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    timings: dict[str, float] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_file: str | None = None


def default_provider_check() -> ProviderCheck:
    live = NppesProviderCheck() if os.environ.get("CLEARANCE_NPPES_LIVE", "1") == "1" else None
    return ChainedProviderCheck(DirectoryProviderCheck.from_file(ROSTER), live)


class Pipeline:
    def __init__(self, judge: Judge | None = None, provider: ProviderCheck | None = None,
                 eligibility: EligibilityCheck | None = None, extractor=None):
        self.judge = judge or default_judge()
        self.provider = provider or default_provider_check()
        self.eligibility = eligibility or default_eligibility()
        self.extractor = extractor or default_extractor()

    def spent(self) -> float:
        return (getattr(self.judge, "spent_usd", 0.0)
                + getattr(self.extractor, "spent_usd", 0.0))

    def extract(self, pdf: Path, pack: Pack) -> tuple[Extraction, dict[str, float]]:
        t0 = time.perf_counter()
        pages, images = ocr_pdf(pdf)
        t1 = time.perf_counter()
        extraction = self.extractor.extract(pack, pages, images)
        t2 = time.perf_counter()
        return extraction, {"ocr_s": t1 - t0, "extract_s": t2 - t1}

    def decide(self, pack: Pack, extraction: Extraction, order_id: str | None = None,
               threshold: float | None = None, timings: dict | None = None,
               source_file: str | None = None) -> Decision:
        from .outreach.render import missing_items, render_outreach

        t0 = time.perf_counter()
        spent = self.spent()
        ctx = EvalContext(pack, extraction, self.judge, self.provider, self.eligibility)
        results = evaluate_all(ctx)
        routing = route(pack, results, threshold)
        timings = {**(timings or {}), "rules_s": time.perf_counter() - t0,
                   "cost_usd": self.spent() - spent}
        decision = Decision(
            order_id=order_id or uuid.uuid4().hex[:10], pack_id=pack.pack_id,
            pack_version=pack.version, route=routing.route, reasons=routing.reasons,
            min_confidence=routing.min_confidence,
            threshold=pack.routing.auto_ready_min_confidence if threshold is None else threshold,
            results=results, extraction=extraction,
            models={"extractor": extraction.extractor, "judge": self.judge.model},
            timings=timings, source_file=source_file,
        )
        decision.missing_items = missing_items(pack, routing.needs_info)
        if decision.missing_items:
            decision.outreach = render_outreach(pack, decision)
        return decision

    def run(self, pdf: Path, pack: Pack, order_id: str | None = None,
            threshold: float | None = None) -> Decision:
        before = self.spent()
        extraction, timings = self.extract(pdf, pack)
        timings["extract_cost_usd"] = self.spent() - before
        decision = self.decide(pack, extraction, order_id, threshold, timings, str(pdf))
        decision.timings["cost_usd"] += timings["extract_cost_usd"]
        return decision
