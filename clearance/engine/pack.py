"""Criteria packs: the YAML schema, the loader, and validation with readable errors.

A pack is one item/service x one payer. Everything payer- or item-specific lives
in the pack; nothing in this module knows about CPAP or oxygen.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"

FieldType = Literal["string", "date", "boolean", "number", "integer", "enum", "list[string]"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    id: str
    title: str
    url: str
    effective: str | None = None


class DocumentType(_Strict):
    id: str
    title: str
    hints: list[str] = Field(default_factory=list)  # phrases that mark a page as this type


class FieldSpec(_Strict):
    name: str
    type: FieldType
    required: bool = False
    values: list[str] | None = None        # enum members
    labels: list[str] = Field(default_factory=list)  # printed labels the value sits next to
    detect: Literal["text", "ink"] = "text"  # "ink": a signature or checkmark, not printed text
    document_type: str | None = None
    description: str = ""


class Extraction(_Strict):
    document_types: list[DocumentType]
    fields: list[FieldSpec]


# --- Rules ------------------------------------------------------------------------

OnFail = Literal["needs_info", "human_review"]


class _RuleBase(_Strict):
    id: str | None = None                  # required at top level, optional when nested
    source: str | None = None
    cite: str | None = None                # section / paragraph within the source
    message: str | None = None
    request: str | None = None             # outreach wording when this rule fails
    on_fail: OnFail | None = None


class Present(_RuleBase):
    type: Literal["present"]
    field: str


class PresentTrue(_RuleBase):
    type: Literal["present_true"]
    field: str


class Pattern(_RuleBase):
    type: Literal["pattern"]
    field: str
    regex: str


class In(_RuleBase):
    type: Literal["in"]
    field: str
    values: list[str | bool | int | float]


class DateOrder(_RuleBase):
    type: Literal["date_order"]
    a: str
    b: str
    relation: Literal["before", "on_or_before", "after", "on_or_after"]


class DateWindow(_RuleBase):
    type: Literal["date_window"]
    a: str
    b: str
    min_days: int | None = None            # (b - a) in days must fall in [min_days, max_days]
    max_days: int | None = None


class Numeric(_RuleBase):
    type: Literal["numeric"]
    field: str
    op: Literal[">=", ">", "<=", "<", "==", "!="]
    value: float


class NumericRange(_RuleBase):
    type: Literal["numeric_range"]
    field: str
    min: float
    max: float


class AnyOf(_RuleBase):
    type: Literal["any_of"]
    options: list[Rule]


class AllOf(_RuleBase):
    type: Literal["all_of"]
    rules: list[Rule]


class LlmJudgment(_RuleBase):
    type: Literal["llm_judgment"]
    question: str
    require_quote: bool = True
    keywords: list[str] = Field(default_factory=list)  # used only by the offline judge


class External(_RuleBase):
    type: Literal["external"]
    check: Literal["npi_registry", "eligibility"]
    field: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)  # check argument -> extracted field


Rule = Annotated[
    Present | PresentTrue | Pattern | In | DateOrder | DateWindow | Numeric | NumericRange
    | AnyOf | AllOf | LlmJudgment | External,
    Field(discriminator="type"),
]
AnyOf.model_rebuild()
AllOf.model_rebuild()

DETERMINISTIC = {"present", "present_true", "pattern", "in", "date_order", "date_window",
                 "numeric", "numeric_range"}


class Routing(_Strict):
    auto_ready_min_confidence: float = 0.97
    unverifiable_below: float = 0.5
    never_auto: list[Literal["llm_judgment_failures"]] = Field(default_factory=list)


class Outreach(_Strict):
    template: str = "missing_items_fax.md.j2"


class Item(_Strict):
    name: str
    hcpcs: list[str]


class Pack(_Strict):
    pack_id: str
    version: str
    item: Item
    payer: str
    sources: list[Source]
    extraction: Extraction
    rules: list[Rule]
    routing: Routing = Field(default_factory=Routing)
    outreach: Outreach = Field(default_factory=Outreach)

    @model_validator(mode="after")
    def _cross_references(self) -> Pack:
        problems = check_references(self)
        if problems:
            raise ValueError("\n".join(problems))
        return self

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.extraction.fields if f.name == name)

    def source(self, source_id: str | None) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def rule_fields(self) -> set[str]:
        """Every extracted field some rule reads."""
        out: set[str] = set()
        for rule in self.rules:
            for node in iter_rules(rule):
                out.update(fields_of(node))
        return out


def iter_rules(rule):
    yield rule
    for child in getattr(rule, "options", None) or getattr(rule, "rules", None) or []:
        yield from iter_rules(child)


def fields_of(rule) -> list[str]:
    if rule.type in ("date_order", "date_window"):
        return [rule.a, rule.b]
    name = getattr(rule, "field", None)
    return ([name] if name else []) + list(getattr(rule, "inputs", {}).values())


_NUMERIC_RULES = {"numeric", "numeric_range"}
_DATE_RULES = {"date_order", "date_window"}


def check_references(pack: Pack) -> list[str]:
    """Cross-field checks Pydantic can't express. Returns readable problems."""
    problems: list[str] = []
    fields = {f.name: f for f in pack.extraction.fields}
    source_ids = {s.id for s in pack.sources}
    doc_ids = {d.id for d in pack.extraction.document_types}

    def suggest(name: str, options) -> str:
        close = difflib.get_close_matches(name, list(options), n=1)
        return f" (did you mean '{close[0]}'?)" if close else ""

    for f in pack.extraction.fields:
        if f.type == "enum" and not f.values:
            problems.append(f"extraction.fields.{f.name}: enum fields need 'values'")
        if f.document_type and f.document_type not in doc_ids:
            problems.append(f"extraction.fields.{f.name}: unknown document_type "
                            f"'{f.document_type}'{suggest(f.document_type, doc_ids)}")

    seen: set[str] = set()
    for i, top in enumerate(pack.rules):
        where_top = f"rules[{i}]" + (f" ({top.id})" if top.id else "")
        if not top.id:
            problems.append(f"{where_top}: top-level rules need an 'id'")
        elif top.id in seen:
            problems.append(f"{where_top}: duplicate rule id '{top.id}'")
        else:
            seen.add(top.id)
        if not top.source:
            problems.append(f"{where_top}: every top-level rule must cite a 'source'")
        if top.on_fail is None:
            problems.append(f"{where_top}: top-level rules need 'on_fail' "
                            "(needs_info or human_review)")

        for node in iter_rules(top):
            where = where_top if node is top else f"{where_top} > {node.type}"
            if node.source and node.source not in source_ids:
                problems.append(f"{where}: unknown source '{node.source}'"
                                f"{suggest(node.source, source_ids)}")
            for name in fields_of(node):
                spec = fields.get(name)
                if spec is None:
                    problems.append(f"{where}: field '{name}' is not defined in "
                                    f"extraction.fields{suggest(name, fields)}")
                    continue
                if node.type in _NUMERIC_RULES and spec.type not in ("number", "integer"):
                    problems.append(f"{where}: '{name}' is {spec.type}, "
                                    f"but {node.type} needs a number")
                if node.type in _DATE_RULES and spec.type != "date":
                    problems.append(f"{where}: '{name}' is {spec.type}, "
                                    f"but {node.type} needs a date")
                if node.type == "present_true" and spec.type != "boolean":
                    problems.append(f"{where}: present_true needs a boolean field, "
                                    f"'{name}' is {spec.type}")
            if node.type == "external" and node.check == "npi_registry" and not node.field:
                problems.append(f"{where}: npi_registry checks need 'field'")
            if node.type in ("any_of", "all_of"):
                kids = node.options if node.type == "any_of" else node.rules
                if not kids:
                    problems.append(f"{where}: {node.type} needs at least one child rule")
    return problems


class PackError(Exception):
    """A pack failed to load. str() is a readable, line-per-problem report."""


def _format_validation(path: str, err: ValidationError) -> str:
    lines = [f"{path} is not a valid criteria pack:"]
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = e["msg"].removeprefix("Value error, ")
        for part in msg.split("\n"):
            lines.append(f"  - {loc + ': ' if loc else ''}{part}")
    return "\n".join(lines)


def parse_pack(text: str, origin: str = "<pack>") -> Pack:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise PackError(f"{origin} is not valid YAML: {err}") from err
    if not isinstance(data, dict):
        raise PackError(f"{origin} must be a YAML mapping at the top level")
    try:
        return Pack.model_validate(data)
    except ValidationError as err:
        raise PackError(_format_validation(origin, err)) from err


def load_pack(pack_id_or_path: str | Path, packs_dir: Path = PACKS_DIR) -> Pack:
    path = Path(pack_id_or_path)
    if not path.suffix:
        path = packs_dir / f"{pack_id_or_path}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in packs_dir.glob("*.yaml")))
        raise PackError(f"no pack '{pack_id_or_path}' (available: {available})")
    return parse_pack(path.read_text(), origin=path.name)


def list_packs(packs_dir: Path = PACKS_DIR) -> list[Pack]:
    return [load_pack(p) for p in sorted(packs_dir.glob("*.yaml"))]
