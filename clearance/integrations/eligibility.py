"""Eligibility: Stedi's JSON API in test mode, or recorded X12 271 fixtures.

Stedi test mode only answers for its documented mock members (for Medicare:
Jane Doe, MBI 1AA2CC3DD45). Every other synthetic patient resolves from a 271
fixture under data/fixtures/eligibility/, decoded with x12_decode.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .base import CheckResult
from .x12_decode import EB_STATUS, read_271

STEDI_URL = "https://healthcare.us.stedi.com/2026-06-01/eligibility-check"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "eligibility"

PAYER_IDS = {"medicare-ffs": "CMS"}

# Stedi's documented test-mode mock members (payerId -> memberIds).
STEDI_MOCK_MEMBERS = {"CMS": {"1AA2CC3DD45"}}
STEDI_MOCK_PROVIDER = {"organization": "Provider Name", "npi": "1999999984"}


def _split_name(name: str | None) -> tuple[str, str]:
    parts = (name or "").replace(",", " ").split()
    if not parts:
        return "", ""
    return parts[0], parts[-1]


class FixtureEligibility:
    source = "x12-fixture"

    def __init__(self, fixture_dir: Path = FIXTURE_DIR):
        self.fixture_dir = fixture_dir

    def check(self, payer: str, member_id: str, name: str | None = None,
              dob: str | None = None) -> CheckResult:
        path = self.fixture_dir / f"{member_id.strip().upper()}.x12"
        if not path.exists():
            return CheckResult(ok=None, source=self.source,
                               message=f"No eligibility response on file for member {member_id}.")
        return from_271(path.read_text(), self.source)


def from_271(raw: str, source: str) -> CheckResult:
    r = read_271(raw)
    detail = {"statuses": [EB_STATUS.get(s, s) for s in r.statuses], "plans": r.plans,
              "rejects": [{"code": x.code, "message": x.message, "action": x.action}
                          for x in r.rejects]}
    if r.rejects:
        why = "; ".join(f"AAA {x.code}: {x.message}" for x in r.rejects)
        return CheckResult(ok=False, source=source, detail=detail,
                           message=f"Payer rejected the eligibility inquiry ({why}).")
    if r.active is True:
        plan = f" ({r.plans[0].title()})" if r.plans else ""
        return CheckResult(ok=True, source=source, detail=detail,
                           message=f"Coverage is active{plan}.")
    if r.active is False:
        return CheckResult(ok=False, source=source, detail=detail,
                           message="Coverage is inactive for this member.")
    return CheckResult(ok=None, source=source, detail=detail,
                       message="The 271 response has no coverage status.")


class StediEligibility:
    source = "stedi-test"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20)

    def handles(self, payer: str, member_id: str) -> bool:
        return member_id.strip().upper() in STEDI_MOCK_MEMBERS.get(PAYER_IDS.get(payer, ""), ())

    def check(self, payer: str, member_id: str, name: str | None = None,
              dob: str | None = None) -> CheckResult:
        first, last = _split_name(name)
        body = {
            "payerId": PAYER_IDS.get(payer, payer),
            "provider": {"name": {"organization": STEDI_MOCK_PROVIDER["organization"]},
                         "npi": STEDI_MOCK_PROVIDER["npi"]},
            "subscriber": {"name": {"person": {"firstName": first, "lastName": last}},
                           "memberId": member_id.strip(), "dateOfBirth": dob},
            "encounter": {"services": [{"value": "30", "system": "STC"}]},
        }
        try:
            resp = self.client.post(STEDI_URL, json=body,
                                    headers={"Authorization": f"Key {self.api_key}"})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as err:
            return CheckResult(ok=None, source=self.source,
                               message=f"Stedi unavailable ({err.__class__.__name__}).")
        return parse_stedi(data, self.source)


def parse_stedi(data: dict, source: str) -> CheckResult:
    errors = data.get("errors") or []
    if errors:
        why = "; ".join(f"AAA {e.get('code')}: {e.get('description')}" for e in errors)
        return CheckResult(ok=False, source=source, detail={"errors": errors},
                           message=f"Payer rejected the eligibility inquiry ({why}).")
    statuses = data.get("planStatus") or []
    codes = [str(s.get("statusCode")) for s in statuses]
    detail = {"planStatus": statuses, "controlNumber": data.get("controlNumber")}
    if any(c in {"1", "2", "3", "4", "5"} for c in codes):
        plan = next((s.get("planDetails") or s.get("status") for s in statuses), "")
        return CheckResult(ok=True, source=source, detail=detail,
                           message=f"Coverage is active ({plan}).")
    if codes:
        return CheckResult(ok=False, source=source, detail=detail,
                           message="Coverage is inactive for this member.")
    return CheckResult(ok=None, source=source, detail=detail,
                       message="Stedi returned no plan status.")


class RoutedEligibility:
    """Stedi for its mock members when a test key is set; fixtures for everyone else."""

    def __init__(self, stedi: StediEligibility | None, fixtures: FixtureEligibility):
        self.stedi, self.fixtures = stedi, fixtures

    def check(self, payer: str, member_id: str, name: str | None = None,
              dob: str | None = None) -> CheckResult:
        if self.stedi and self.stedi.handles(payer, member_id):
            return self.stedi.check(payer, member_id, name, dob)
        return self.fixtures.check(payer, member_id, name, dob)


def default_eligibility() -> RoutedEligibility:
    key = os.environ.get("STEDI_API_KEY")
    return RoutedEligibility(StediEligibility(key) if key else None, FixtureEligibility())
