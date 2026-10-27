"""Rule evaluators: one function per rule type in the pack schema.

Deterministic rules run first. Composite rules settle on their deterministic
children before asking a model or an external service, so an AHI of 32 never
costs an LLM call. A rule that reads a field we couldn't read confidently is
`unverifiable`, never guessed.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from datetime import date

from ..integrations.base import EligibilityCheck, ProviderCheck
from ..integrations.npi import npi_is_valid
from .judge import Judge
from .pack import DETERMINISTIC, Pack, iter_rules
from .types import Evidence, ExtractedField, Extraction, FieldStatus, Outcome, Route, RuleResult


@dataclass
class EvalContext:
    pack: Pack
    extraction: Extraction
    judge: Judge
    provider: ProviderCheck | None = None
    eligibility: EligibilityCheck | None = None


def evaluate_all(ctx: EvalContext) -> list[RuleResult]:
    return [evaluate(rule, ctx) for rule in ctx.pack.rules]


def evaluate(rule, ctx: EvalContext) -> RuleResult:
    result = _EVALUATORS[rule.type](rule, ctx)
    result.rule_id = rule.id or result.rule_id
    result.source = rule.source or result.source
    result.citation = rule.cite
    result.on_fail = Route(rule.on_fail) if rule.on_fail else None
    result.request = rule.request
    if rule.message and result.outcome == Outcome.FAIL:
        result.detail.setdefault("reason", result.message)
        result.message = rule.message
    return result


def is_deterministic(rule) -> bool:
    return all(node.type in DETERMINISTIC | {"any_of", "all_of"} for node in iter_rules(rule))


# --- field access ---------------------------------------------------------------

_ACRONYMS = {"ahi": "AHI", "rdi": "RDI", "npi": "NPI", "mbi": "MBI", "f2f": "F2F",
             "spo2": "SpO2", "po2": "PO2", "dob": "DOB"}


def _label(ctx: EvalContext, name: str) -> str:
    return " ".join(_ACRONYMS.get(w, w) for w in name.split("_"))


def _read(ctx: EvalContext, names: list[str]):
    """Fetch fields; return (fields, early_result_kwargs or None)."""
    floor = ctx.pack.routing.unverifiable_below
    fields = [ctx.extraction.get(n) for n in names]
    evidence = [_evidence(f) for f in fields]
    unreadable = [f for f in fields if f.status == FieldStatus.ILLEGIBLE
                  or (f.status == FieldStatus.FOUND and f.confidence < floor)]
    if unreadable:
        what = ", ".join(_label(ctx, f.name) for f in unreadable)
        return fields, dict(outcome=Outcome.UNVERIFIABLE, evidence=evidence,
                            message=f"Could not read {what} with confidence; "
                                    "a reviewer should check the page.")
    missing = [f for f in fields if f.status == FieldStatus.MISSING or f.value in (None, "")]
    if missing:
        what = ", ".join(_label(ctx, f.name) for f in missing)
        return fields, dict(outcome=Outcome.FAIL, evidence=evidence,
                            message=f"No {what} found in the packet.",
                            detail={"missing_fields": [f.name for f in missing]})
    return fields, None


def _evidence(f: ExtractedField) -> Evidence:
    return Evidence(field=f.name, value=f.value, confidence=f.confidence, page=f.page,
                    quote=f.evidence, bbox=f.bbox)


def _leaf(rule, ctx, names, check) -> RuleResult:
    fields, early = _read(ctx, names)
    if early:
        return RuleResult(rule_id=rule.id or rule.type, type=rule.type, **early)
    ok, message = check(*[f.value for f in fields])
    return RuleResult(rule_id=rule.id or rule.type, type=rule.type,
                      outcome=Outcome.PASS if ok else Outcome.FAIL,
                      message=message, evidence=[_evidence(f) for f in fields])


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# --- leaf rules -------------------------------------------------------------------

def _present(rule, ctx):
    return _leaf(rule, ctx, [rule.field],
                 lambda v: (True, f"{_cap(_label(ctx, rule.field))} is present."))


def _present_true(rule, ctx):
    name = _cap(_label(ctx, rule.field))
    return _leaf(rule, ctx, [rule.field],
                 lambda v: (v is True, f"{name}: {'yes' if v is True else 'no'}."))


def _pattern(rule, ctx):
    def check(v):
        ok = re.fullmatch(rule.regex, str(v).strip()) is not None
        return ok, (f"'{v}' has the expected format." if ok
                    else f"'{v}' does not match the expected {_label(ctx, rule.field)} format.")
    return _leaf(rule, ctx, [rule.field], check)


def _in(rule, ctx):
    def norm(x):
        return x.lower() if isinstance(x, str) else x

    def check(v):
        ok = norm(v) in [norm(x) for x in rule.values]
        return ok, f"{_cap(_label(ctx, rule.field))} is '{v}'."
    return _leaf(rule, ctx, [rule.field], check)


_RELATIONS = {
    "before": operator.lt, "on_or_before": operator.le,
    "after": operator.gt, "on_or_after": operator.ge,
}


def _date_order(rule, ctx):
    def check(a, b):
        da, db = _as_date(a), _as_date(b)
        ok = _RELATIONS[rule.relation](da, db)
        rel = rule.relation.replace("_", " ")
        verdict = "is" if ok else "is not"
        return ok, (f"{_cap(_label(ctx, rule.a))} ({da}) {verdict} {rel} "
                    f"{_label(ctx, rule.b)} ({db}).")
    return _leaf(rule, ctx, [rule.a, rule.b], check)


def _date_window(rule, ctx):
    def check(a, b):
        days = (_as_date(b) - _as_date(a)).days
        ok = ((rule.min_days is None or days >= rule.min_days)
              and (rule.max_days is None or days <= rule.max_days))
        lo = rule.min_days if rule.min_days is not None else "-inf"
        hi = rule.max_days if rule.max_days is not None else "inf"
        return ok, (f"{_cap(_label(ctx, rule.b))} is {days} days after "
                    f"{_label(ctx, rule.a)} (allowed {lo} to {hi}).")
    return _leaf(rule, ctx, [rule.a, rule.b], check)


_OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt,
        "==": operator.eq, "!=": operator.ne}


def _numeric(rule, ctx):
    def check(v):
        ok = _OPS[rule.op](float(v), rule.value)
        return ok, f"{_cap(_label(ctx, rule.field))} = {v} ({'meets' if ok else 'fails'} " \
                   f"{rule.op} {rule.value:g})."
    return _leaf(rule, ctx, [rule.field], check)


def _numeric_range(rule, ctx):
    def check(v):
        ok = rule.min <= float(v) <= rule.max
        return ok, f"{_cap(_label(ctx, rule.field))} = {v} " \
                   f"({'within' if ok else 'outside'} {rule.min:g}-{rule.max:g})."
    return _leaf(rule, ctx, [rule.field], check)


# --- composites -------------------------------------------------------------------

def _skipped(rule, why: str) -> RuleResult:
    return RuleResult(rule_id=rule.id or rule.type, type=rule.type, outcome=Outcome.SKIPPED,
                      message=why)


def _ordered(children):
    """Deterministic children first, original order otherwise."""
    return sorted(range(len(children)), key=lambda i: not is_deterministic(children[i]))


def _any_of(rule, ctx):
    results: dict[int, RuleResult] = {}
    for i in _ordered(rule.options):
        if any(r.outcome == Outcome.PASS for r in results.values()):
            results[i] = _skipped(rule.options[i], "Not needed: another option is met.")
        else:
            results[i] = evaluate(rule.options[i], ctx)
    kids = [results[i] for i in range(len(rule.options))]
    outcomes = {k.outcome for k in kids}
    if Outcome.PASS in outcomes:
        winner = next(k for k in kids if k.outcome == Outcome.PASS)
        return RuleResult(rule_id=rule.id or "any_of", type="any_of", outcome=Outcome.PASS,
                          message=f"Met: {winner.message}", children=kids,
                          llm_involved=winner.llm_involved)
    outcome = Outcome.UNVERIFIABLE if Outcome.UNVERIFIABLE in outcomes else Outcome.FAIL
    reasons = "; ".join(k.message for k in kids if k.outcome == outcome)
    return RuleResult(rule_id=rule.id or "any_of", type="any_of", outcome=outcome,
                      message=f"No option met. {reasons}", children=kids,
                      llm_involved=any(k.llm_involved for k in kids if k.outcome == outcome),
                      detail=_merge_missing(kids))


def _all_of(rule, ctx):
    results: dict[int, RuleResult] = {}
    for i in _ordered(rule.rules):
        if any(r.outcome == Outcome.FAIL for r in results.values()):
            results[i] = _skipped(rule.rules[i], "Not needed: another condition already fails.")
        else:
            results[i] = evaluate(rule.rules[i], ctx)
    kids = [results[i] for i in range(len(rule.rules))]
    outcomes = {k.outcome for k in kids}
    if Outcome.FAIL in outcomes:
        outcome = Outcome.FAIL
    elif Outcome.UNVERIFIABLE in outcomes:
        outcome = Outcome.UNVERIFIABLE
    else:
        return RuleResult(rule_id=rule.id or "all_of", type="all_of", outcome=Outcome.PASS,
                          message=" ".join(k.message for k in kids), children=kids,
                          llm_involved=any(k.llm_involved for k in kids))
    deciding = [k for k in kids if k.outcome == outcome]
    return RuleResult(rule_id=rule.id or "all_of", type="all_of", outcome=outcome,
                      message=" ".join(k.message for k in deciding), children=kids,
                      llm_involved=any(k.llm_involved for k in deciding),
                      detail=_merge_missing(kids))


def _merge_missing(kids: list[RuleResult]) -> dict:
    missing = sorted({m for k in kids for m in k.detail.get("missing_fields", [])})
    return {"missing_fields": missing} if missing else {}


# --- model and external checks -----------------------------------------------------

def _llm_judgment(rule, ctx):
    j = ctx.judge.ask(rule.question, ctx.extraction, rule.keywords)
    evidence = [Evidence(quote=j.quote, page=j.page, confidence=j.confidence)] if j.quote else []
    detail = {"model": j.model, "question": rule.question, "answer": j.answer,
              "grounded": j.grounded, "explanation": j.explanation}
    if j.answer == "yes" and j.confidence >= ctx.pack.routing.unverifiable_below:
        outcome, message = Outcome.PASS, j.explanation
    elif j.answer == "no":
        outcome, message = Outcome.FAIL, f"Not documented: {j.explanation}"
    else:
        outcome, message = Outcome.UNVERIFIABLE, f"Unclear: {j.explanation}"
    return RuleResult(rule_id=rule.id or "llm_judgment", type="llm_judgment", outcome=outcome,
                      message=message, evidence=evidence, llm_involved=True, detail=detail)


def _external(rule, ctx):
    names = ([rule.field] if rule.field else []) + list(rule.inputs.values())
    fields, early = _read(ctx, names)
    rid = rule.id or rule.type
    if early:
        return RuleResult(rule_id=rid, type="external", **early)
    values = {k: ctx.extraction.get(v).value for k, v in rule.inputs.items()}
    evidence = [_evidence(f) for f in fields]

    if rule.check == "npi_registry":
        npi = str(ctx.extraction.get(rule.field).value).strip()
        if not npi_is_valid(npi):
            return RuleResult(rule_id=rid, type="external", outcome=Outcome.FAIL,
                              evidence=evidence, detail={"check": "npi_luhn"},
                              message=f"NPI '{npi}' fails the NPI check-digit rule.")
        if ctx.provider is None:
            return RuleResult(rule_id=rid, type="external", outcome=Outcome.UNVERIFIABLE,
                              evidence=evidence, message="No provider directory configured.")
        res = ctx.provider.check(npi, values.get("name"))
    else:
        if ctx.eligibility is None:
            return RuleResult(rule_id=rid, type="external", outcome=Outcome.UNVERIFIABLE,
                              evidence=evidence, message="No eligibility service configured.")
        res = ctx.eligibility.check(ctx.pack.payer, str(values.get("member_id", "")),
                                    values.get("name"), values.get("dob"))

    outcome = {True: Outcome.PASS, False: Outcome.FAIL, None: Outcome.UNVERIFIABLE}[res.ok]
    return RuleResult(rule_id=rid, type="external", outcome=outcome, message=res.message,
                      evidence=evidence, detail={"check": rule.check, "source": res.source,
                                                 **res.detail})


_EVALUATORS = {
    "present": _present, "present_true": _present_true, "pattern": _pattern, "in": _in,
    "date_order": _date_order, "date_window": _date_window, "numeric": _numeric,
    "numeric_range": _numeric_range, "any_of": _any_of, "all_of": _all_of,
    "llm_judgment": _llm_judgment, "external": _external,
}
