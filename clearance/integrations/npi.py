"""Ordering-provider checks: NPI check digit, then a provider directory.

`NppesProviderCheck` calls the public NPPES NPI Registry (no key) and caches
responses on disk. `DirectoryProviderCheck` answers from a local roster, which
is how synthetic providers resolve without attaching clinical content to a real
clinician's NPI. `ChainedProviderCheck` asks each in turn.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import httpx

from .base import CheckResult

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
CACHE_DIR = Path(__file__).resolve().parents[2] / "var" / "cache" / "nppes"


def npi_is_valid(npi: str) -> bool:
    """The NPI check-digit rule: Luhn over the 9 digits prefixed with 80840.

    Ported from prior-auth-completeness-gate (MIT).
    """
    if not re.fullmatch(r"\d{10}", npi):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi[:9])):
        digit = int(ch)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10 == int(npi[9])


def npi_check_digit(first9: str) -> str:
    return next(str(d) for d in range(10) if npi_is_valid(first9 + str(d)))


def _tokens(name: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z]+", plain)
    titles = {"dr", "md", "do", "np", "pa", "rn", "aprn", "fnp", "c", "jr", "sr", "ii", "iii"}
    return {w for w in words if w not in titles}


def names_match(order_name: str | None, registry_name: str) -> bool:
    """Rough match: the registry's last name appears on the order, plus a first name or initial."""
    if not order_name:
        return True
    got, want = _tokens(order_name), registry_name.lower().split()
    if not want:
        return False
    last, first = want[-1], want[0]
    return last in got and (first in got or any(t[0] == first[0] for t in got if t != last))


def _verdict(npi: str, name: str | None, record: dict | None, source: str) -> CheckResult:
    if record is None:
        return CheckResult(ok=False, source=source,
                           message=f"NPI {npi} was not found in the NPI Registry.")
    reg_name = record["name"]
    detail = {"registry_name": reg_name, "status": record["status"],
              "taxonomy": record.get("taxonomy"), "enumeration_type": record.get("type")}
    if record["status"] != "A":
        return CheckResult(ok=False, source=source, detail=detail,
                           message=f"NPI {npi} ({reg_name}) is not active in the registry.")
    if record.get("type") == "NPI-2":
        return CheckResult(ok=False, source=source, detail=detail,
                           message=f"NPI {npi} belongs to an organization ({reg_name}), "
                                   "not an individual ordering practitioner.")
    if not names_match(name, reg_name):
        return CheckResult(ok=False, source=source, detail=detail,
                           message=f"NPI {npi} is registered to {reg_name}, "
                                   f"but the order names {name}.")
    return CheckResult(ok=True, source=source, detail=detail,
                       message=f"NPI {npi} is active and registered to {reg_name}.")


class DirectoryProviderCheck:
    """Answers from a JSON roster: {npi: {name, status, taxonomy, type}}."""

    source = "directory"

    def __init__(self, roster: dict[str, dict]):
        self.roster = roster

    @classmethod
    def from_file(cls, path: Path) -> DirectoryProviderCheck:
        return cls(json.loads(path.read_text()) if path.exists() else {})

    def knows(self, npi: str) -> bool:
        return npi in self.roster

    def check(self, npi: str, name: str | None = None) -> CheckResult:
        return _verdict(npi, name, self.roster.get(npi), self.source)


class NppesProviderCheck:
    source = "nppes"

    def __init__(self, cache_dir: Path = CACHE_DIR, client: httpx.Client | None = None):
        self.cache_dir = cache_dir
        self.client = client or httpx.Client(timeout=10)

    def lookup(self, npi: str) -> dict | None:
        """Raw NPPES result for one NPI, cached on disk. None = not enumerated."""
        cached = self.cache_dir / f"{npi}.json"
        if cached.exists():
            data = json.loads(cached.read_text())
        else:
            resp = self.client.get(NPPES_URL, params={"version": "2.1", "number": npi})
            resp.raise_for_status()
            data = resp.json()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data))
        results = data.get("results") or []
        return results[0] if results else None

    @staticmethod
    def summarize(raw: dict) -> dict:
        basic = raw.get("basic", {})
        if raw.get("enumeration_type") == "NPI-2":
            name = basic.get("organization_name", "")
        else:
            name = f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
        taxonomy = next((t.get("desc") for t in raw.get("taxonomies", []) if t.get("primary")),
                        None)
        return {"name": name.title(), "status": basic.get("status", "A"),
                "taxonomy": taxonomy, "type": raw.get("enumeration_type")}

    def check(self, npi: str, name: str | None = None) -> CheckResult:
        try:
            raw = self.lookup(npi)
        except (httpx.HTTPError, ValueError) as err:
            return CheckResult(ok=None, source=self.source,
                               message=f"NPI Registry unavailable ({err.__class__.__name__}); "
                                       "verify manually.")
        return _verdict(npi, name, self.summarize(raw) if raw else None, self.source)


class ChainedProviderCheck:
    """Local directory first (synthetic and customer rosters), then live NPPES."""

    def __init__(self, directory: DirectoryProviderCheck, live: NppesProviderCheck | None):
        self.directory, self.live = directory, live

    def check(self, npi: str, name: str | None = None) -> CheckResult:
        if self.directory.knows(npi) or self.live is None:
            return self.directory.check(npi, name)
        return self.live.check(npi, name)
