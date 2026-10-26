import pytest

from clearance.engine.judge import KeywordJudge
from clearance.engine.pack import load_pack
from clearance.engine.rules import EvalContext
from clearance.engine.types import ExtractedField, Extraction, FieldStatus, OcrLine, Page
from clearance.integrations.base import CheckResult


class StubCheck:
    def __init__(self, ok=True, message="stub"):
        self.ok, self.message, self.calls = ok, message, []

    def check(self, *args, **kwargs):
        self.calls.append(args)
        return CheckResult(ok=self.ok, message=self.message, source="stub")


GOOD = {
    "patient_name": "Jane Doe", "patient_dob": "1955-05-05", "medicare_id": "1AA2CC3DD45",
    "ordering_provider_name": "Dana Whitfield, MD", "ordering_npi": "1234567893",
    "item_ordered": "E0601 CPAP device", "order_date": "2026-03-10", "order_signed": True,
    "f2f_date": "2026-02-01", "sleep_test_date": "2026-02-20", "sleep_test_type": "PSG",
    "ahi_or_rdi": 22.4, "total_events": 140,
}


def make_extraction(values=None, conf=0.99, overrides=None, note=""):
    values = {**GOOD, **(values or {})}
    fields = {}
    for name, value in values.items():
        if value is None:
            fields[name] = ExtractedField(name=name, status=FieldStatus.MISSING)
        else:
            fields[name] = ExtractedField(name=name, value=value, confidence=conf,
                                          status=FieldStatus.FOUND, page=1)
    for name, f in (overrides or {}).items():
        fields[name] = f
    lines = [OcrLine(text=t, confidence=0.99, bbox=[0, 0, 1, 1]) for t in note.split("\n") if t]
    return Extraction(fields=fields, pages=[Page(number=1, lines=lines)])


@pytest.fixture
def pap():
    return load_pack("medicare-pap-osa-initial")


@pytest.fixture
def ctx_factory(pap):
    def make(extraction, provider=None, eligibility=None, judge=None, pack=None):
        return EvalContext(pack=pack or pap, extraction=extraction, judge=judge or KeywordJudge(),
                           provider=provider or StubCheck(), eligibility=eligibility or StubCheck())
    return make
