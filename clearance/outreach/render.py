"""Draft the 'missing items' fax back to the referring office."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..engine.pack import Pack
from ..engine.types import RuleResult

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined,
                   trim_blocks=True, lstrip_blocks=True)


def missing_items(pack: Pack, failed: list[RuleResult]) -> list[str]:
    """One plain-language request per failed rule, deduplicated, in pack order."""
    out: list[str] = []
    for r in failed:
        item = r.request or r.message
        if item and item not in out:
            out.append(item)
    return out


def render_outreach(pack: Pack, decision) -> str:
    f = decision.extraction.fields

    def val(name):
        field = f.get(name)
        return field.value if field and field.value not in (None, "") else None

    findings = []
    for r in decision.results:
        if r.outcome == "fail" and (r.request or r.message):
            src = pack.source(r.source)
            findings.append({"request": r.request or r.message, "why": r.message,
                             "source": src.title if src else None, "cite": r.citation})
    template = _env.get_template(pack.outreach.template)
    return template.render(
        today=date.today().strftime("%B %-d, %Y"),
        patient=val("patient_name") or "(patient name not found)",
        dob=val("patient_dob"), member_id=val("medicare_id"),
        practitioner=val("ordering_provider_name") or "Ordering practitioner",
        item=pack.item, findings=findings, order_id=decision.order_id,
    )
