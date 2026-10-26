"""Interfaces every integration implements, so a customer's own system can slot in.

A Brightree eligibility export, an EHR provider directory, or a different
clearinghouse only needs to satisfy one of these protocols.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class CheckResult(BaseModel):
    ok: bool | None                      # None: the check could not be completed
    message: str
    source: str                          # which implementation answered
    detail: dict[str, Any] = Field(default_factory=dict)


class ProviderCheck(Protocol):
    def check(self, npi: str, name: str | None = None) -> CheckResult: ...


class EligibilityCheck(Protocol):
    def check(self, payer: str, member_id: str, name: str | None = None,
              dob: str | None = None) -> CheckResult: ...
