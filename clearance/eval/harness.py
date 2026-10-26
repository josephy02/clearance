"""Run the pipeline over fixtures and score it against ground truth.

Rules and extraction run once per order; the threshold sweep only re-routes,
so sweeping costs nothing extra.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..engine.judge import find_quote
from ..engine.pack import Pack, load_pack
from ..engine.router import route
from ..engine.types import FieldStatus, Outcome
from ..extract.fields import _norm
from ..pipeline import ROOT, Decision, Pipeline

FIXTURES = ROOT / "data" / "fixtures"
GROUND_TRUTH = ROOT / "data" / "ground_truth"
ROUTES = ["ready", "needs_info", "human_review"]
SWEEP = [round(0.80 + 0.01 * i, 2) for i in range(20)]

# $ per million tokens (input, output) for cost reporting.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}


@dataclass
class OrderResult:
    order_id: str
    truth: dict
    decision: Decision
    seconds: float
    field_checks: dict[str, bool] = field(default_factory=dict)


def field_correct(name: str, want, got, illegible: bool, floor: float) -> bool:
    if illegible:      # planted smudge: the right answer is "I can't read this"
        return got.status != FieldStatus.FOUND or got.confidence < floor
    if want is None:
        return got.status != FieldStatus.FOUND
    if got.status != FieldStatus.FOUND or got.value is None:
        return False
    if isinstance(want, bool) or isinstance(got.value, bool):
        return want == got.value
    if isinstance(want, int | float):
        try:
            return abs(float(got.value) - float(want)) < 0.05
        except (TypeError, ValueError):
            return False
    w, g = _norm(str(want)), _norm(str(got.value))
    w, g = (_strip(w), _strip(g))
    return w == g or (len(w) >= 4 and w in g)


def _strip(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum())


def flagged(outcome: str) -> bool:
    return outcome in (Outcome.FAIL, Outcome.UNVERIFIABLE, "fail", "unverifiable")


def run_orders(pack: Pack, kind: str, pipeline: Pipeline, limit: int | None = None,
               only: list[str] | None = None) -> list[OrderResult]:
    out = []
    pdfs = sorted((FIXTURES / kind).glob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if p.stem in only]
    for pdf in pdfs[:limit]:
        truth = json.loads((GROUND_TRUTH / f"{pdf.stem}.json").read_text())
        t0 = time.perf_counter()
        decision = pipeline.run(pdf, pack, order_id=pdf.stem)
        res = OrderResult(pdf.stem, truth, decision, time.perf_counter() - t0)
        floor = pack.routing.unverifiable_below
        for name, want in truth["fields"].items():
            got = decision.extraction.get(name)
            res.field_checks[name] = field_correct(name, want, got,
                                                   name in truth.get("illegible_fields", []),
                                                   floor)
        out.append(res)
    return out


def metrics(pack: Pack, results: list[OrderResult]) -> dict:
    # Field accuracy, overall / per field / per degradation level.
    per_field, per_level = defaultdict(list), defaultdict(list)
    for r in results:
        for name, ok in r.field_checks.items():
            per_field[name].append(ok)
            per_level[r.truth["degradation"]].append(ok)
    all_checks = [ok for r in results for ok in r.field_checks.values()]

    # Page classification.
    page_ok = [got == (want if want != "other" else None)
               for r in results
               for got, want in zip([p.document_type for p in r.decision.extraction.pages],
                                    r.truth["page_types"], strict=False)]

    # Rule flags vs planted defects. Positive = the rule should not pass.
    rule_stats = {}
    for rule in [ru.id for ru in pack.rules]:
        tp = fp = fn = tn = 0
        for r in results:
            want = flagged(r.truth["rules"][rule])
            got_r = next(x for x in r.decision.results if x.rule_id == rule)
            got = flagged(got_r.outcome)
            tp += want and got
            fp += got and not want
            fn += want and not got
            tn += not want and not got
        rule_stats[rule] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }

    # Routing confusion matrix.
    confusion = {w: dict.fromkeys(ROUTES, 0) for w in ROUTES}
    for r in results:
        confusion[r.truth["route"]][r.decision.route.value] += 1
    route_acc = sum(confusion[k][k] for k in ROUTES) / len(results)

    # The safety metric that matters: did we call something READY that wasn't?
    unsafe = [r.order_id for r in results
              if r.decision.route.value == "ready" and r.truth["route"] != "ready"]
    missed_info = [r.order_id for r in results
                   if r.truth["route"] == "needs_info" and r.decision.route.value == "ready"]

    # Threshold sweep: % auto-routed READY vs. how many of those were wrong.
    sweep = []
    for t in SWEEP:
        routed = [route(pack, r.decision.results, t).route.value for r in results]
        auto = [r for r, rt in zip(results, routed, strict=True) if rt == "ready"]
        wrong = [r for r in auto if r.truth["route"] != "ready"]
        truly_ready = sum(r.truth["route"] == "ready" for r in results)
        sweep.append({
            "threshold": t, "auto_rate": len(auto) / len(results),
            "error_rate": len(wrong) / len(auto) if auto else 0.0,
            "ready_recall": (len(auto) - len(wrong)) / truly_ready if truly_ready else None,
            "auto": len(auto), "wrong": len(wrong),
        })

    # Quote validity, checked independently of the guard.
    quotes = [(r, ev.quote) for r in results for top in r.decision.results
              for node in top.walk() if node.type == "llm_judgment"
              for ev in node.evidence if ev.quote]
    quote_valid = [find_quote(q, r.decision.extraction) is not None for r, q in quotes]
    judgments = Counter(node.detail.get("answer") for r in results
                        for top in r.decision.results for node in top.walk()
                        if node.type == "llm_judgment")

    secs = sorted(r.seconds for r in results)
    p95 = secs[min(len(secs) - 1, int(round(0.95 * (len(secs) - 1))))]
    usage = [r.decision.timings.get("cost_usd", 0.0) for r in results]

    return {
        "pack_id": pack.pack_id, "pack_version": pack.version, "orders": len(results),
        "threshold": pack.routing.auto_ready_min_confidence,
        "models": results[0].decision.models if results else {},
        "field_accuracy": sum(all_checks) / len(all_checks),
        "field_accuracy_by_level": {k: sum(v) / len(v) for k, v in sorted(per_level.items())},
        "field_accuracy_by_field": {k: sum(v) / len(v) for k, v in per_field.items()},
        "page_classification_accuracy": sum(page_ok) / len(page_ok) if page_ok else None,
        "rules": rule_stats,
        "defect_recall": _overall_recall(rule_stats),
        "routing_accuracy": route_acc, "confusion": confusion,
        "unsafe_ready": unsafe, "missed_needs_info": missed_info,
        "sweep": sweep,
        "llm_judgments": dict(judgments), "quotes": len(quotes),
        "quote_validity": sum(quote_valid) / len(quote_valid) if quote_valid else None,
        "latency_p50_s": statistics.median(secs), "latency_p95_s": p95,
        "cost_per_order_usd": sum(usage) / len(usage) if usage else 0.0,
        "per_order": [{
            "order_id": r.order_id, "defects": r.truth["defects"],
            "degradation": r.truth["degradation"], "want": r.truth["route"],
            "got": r.decision.route.value, "min_confidence": r.decision.min_confidence,
            "field_errors": [k for k, ok in r.field_checks.items() if not ok],
            "rule_mismatches": [
                {"rule": x.rule_id, "want": r.truth["rules"][x.rule_id], "got": x.outcome.value}
                for x in r.decision.results
                if flagged(r.truth["rules"][x.rule_id]) != flagged(x.outcome)
            ],
        } for r in results],
    }


def _overall_recall(rule_stats: dict) -> float | None:
    tp = sum(s["tp"] for s in rule_stats.values())
    fn = sum(s["fn"] for s in rule_stats.values())
    return tp / (tp + fn) if tp + fn else None


SUITES = {"pap": "medicare-pap-osa-initial", "oxygen": "medicare-oxygen-home"}


def available_suites() -> list[str]:
    return [k for k in SUITES if (FIXTURES / k).exists() and any((FIXTURES / k).glob("*.pdf"))]


def evaluate(kind: str, pipeline: Pipeline, limit: int | None = None) -> dict:
    pack = load_pack(SUITES[kind])
    return metrics(pack, run_orders(pack, kind, pipeline, limit))
