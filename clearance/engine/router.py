"""Turn per-rule outcomes into one route: READY, NEEDS_INFO, or HUMAN_REVIEW.

Precedence: anything a human must look at wins over a request for more
information, which wins over ready. READY additionally requires every field
the decision actually used to clear the pack's auto-ready confidence bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .pack import Pack
from .types import Outcome, Route, RuleResult


@dataclass
class Routing:
    route: Route
    reasons: list[str] = field(default_factory=list)
    min_confidence: float | None = None    # lowest confidence among fields used
    needs_info: list[RuleResult] = field(default_factory=list)


def used_confidences(results: list[RuleResult]) -> list[float]:
    """Confidence of every extracted field read by a non-skipped rule."""
    out = []
    for top in results:
        for node in top.walk():
            if node.outcome == Outcome.SKIPPED or node.type == "llm_judgment":
                continue
            out.extend(e.confidence for e in node.evidence
                       if e.field and e.confidence is not None)
    return out


def route(pack: Pack, results: list[RuleResult],
          threshold: float | None = None) -> Routing:
    threshold = pack.routing.auto_ready_min_confidence if threshold is None else threshold
    human, info = [], []

    for r in results:
        if r.outcome == Outcome.UNVERIFIABLE:
            human.append(f"{r.rule_id}: {r.message}")
        elif r.outcome == Outcome.FAIL:
            if r.on_fail == Route.HUMAN_REVIEW:
                human.append(f"{r.rule_id}: {r.message}")
            elif r.llm_involved and "llm_judgment_failures" in pack.routing.never_auto:
                human.append(f"{r.rule_id}: a model judged this not documented; "
                             "a reviewer confirms before the office is asked.")
                info.append(r)
            else:
                info.append(r)

    confs = used_confidences(results)
    low = min(confs) if confs else None
    if human:
        return Routing(Route.HUMAN_REVIEW, human, low, info)
    if info:
        return Routing(Route.NEEDS_INFO, [f"{r.rule_id}: {r.message}" for r in info], low, info)
    if low is not None and low < threshold:
        return Routing(Route.HUMAN_REVIEW,
                       [f"All rules pass, but the least certain field read is {low:.2f}, "
                        f"below the auto-ready bar of {threshold:.2f}."], low)
    return Routing(Route.READY, ["Every rule passes and every field clears the "
                                 f"{threshold:.2f} confidence bar."], low)
