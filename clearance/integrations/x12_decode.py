"""Minimal X12 271 reader: coverage status (EB01) and AAA reject reasons.

Enough to decode a clearinghouse's raw 271 or an offline fixture. Code values
are from the public X12 005010X279A1 code lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

AAA_REASONS = {
    "04": "Authorized quantity exceeded",
    "15": "Required application data missing",
    "41": "Authorization/access restrictions",
    "42": "Unable to respond at current time",
    "43": "Invalid/missing provider identification",
    "44": "Invalid/missing provider name",
    "51": "Provider not on file",
    "52": "Service dates not within provider plan enrollment",
    "56": "Inappropriate date",
    "57": "Invalid/missing date(s) of service",
    "58": "Invalid/missing date of birth",
    "62": "Date of service not within allowable inquiry period",
    "63": "Date of service in future",
    "64": "Invalid/missing patient ID",
    "65": "Invalid/missing patient name",
    "67": "Patient not found",
    "71": "Patient birth date does not match that for the patient on the database",
    "72": "Invalid/missing subscriber/insured ID",
    "73": "Invalid/missing subscriber/insured name",
    "75": "Subscriber/insured not found",
    "76": "Duplicate subscriber/insured ID number",
    "78": "Subscriber/insured not in group/plan identified",
    "79": "Invalid participant identification",
    "80": "No response received; transaction terminated",
    "T4": "Payer name or identifier missing",
}

AAA_ACTIONS = {
    "C": "Please correct and resubmit",
    "N": "Resubmission not allowed",
    "P": "Please resubmit original transaction",
    "R": "Resubmission allowed",
    "S": "Do not resubmit; inquiry initiated to a third party",
    "W": "Please wait 30 days and resubmit",
    "X": "Please wait 10 days and resubmit",
    "Y": "Do not resubmit; we will hand over to the appropriate party",
}

EB_STATUS = {
    "1": "Active coverage", "2": "Active - full risk capitation",
    "3": "Active - services capitated", "4": "Active - services capitated to primary care",
    "5": "Active - pending investigation", "6": "Inactive",
    "7": "Inactive - pending eligibility update", "8": "Inactive - pending investigation",
}


@dataclass
class Reject:
    code: str
    message: str
    action: str | None = None


@dataclass
class Eligibility271:
    member_id: str | None = None
    member_name: str | None = None
    statuses: list[str] = field(default_factory=list)   # EB01 codes in order
    plans: list[str] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)

    @property
    def active(self) -> bool | None:
        if self.rejects:
            return False
        if any(s in "12345" for s in self.statuses):
            return True
        if any(s in "678" for s in self.statuses):
            return False
        return None


def _separators(raw: str) -> tuple[str, str]:
    raw = raw.lstrip()
    if raw.startswith("ISA") and len(raw) > 105:
        return raw[3], raw[105]
    return "*", "~"


def read_271(raw: str) -> Eligibility271:
    elem, seg = _separators(raw)
    out = Eligibility271()
    for segment in (s.strip() for s in raw.split(seg)):
        parts = segment.split(elem)
        tag = parts[0]
        if tag == "NM1" and len(parts) > 9 and parts[1] == "IL":
            last, first = parts[3], parts[4] if len(parts) > 4 else ""
            out.member_name = f"{first} {last}".strip().title()
            out.member_id = parts[9]
        elif tag == "EB" and len(parts) > 1:
            out.statuses.append(parts[1])
            if len(parts) > 5 and parts[5]:
                out.plans.append(parts[5])
        elif tag == "AAA" and len(parts) > 3:
            code = parts[3]
            action = parts[4] if len(parts) > 4 else None
            out.rejects.append(Reject(code, AAA_REASONS.get(code, f"Reject reason {code}"),
                                      AAA_ACTIONS.get(action or "")))
    return out


def build_271(member_id: str, first: str, last: str, dob: str, status: str,
              payer: str = "CMS", control: str = "000000001") -> str:
    """A small, well-formed 271 for fixtures. status: active | inactive | not_found."""
    d = dob.replace("-", "")
    body = [
        "ST*271*0001*005010X279A1",
        "BHT*0022*11*CLR" + control + "*20260301*1200",
        "HL*1**20*1", f"NM1*PR*2*{payer}*****PI*{payer}",
        "HL*2*1*21*1", "NM1*1P*2*CASCADE SLEEP AND RESPIRATORY*****XX*1999999984",
        "HL*3*2*22*0", f"TRN*2*{control}*9CLEARANCE",
        f"NM1*IL*1*{last.upper()}*{first.upper()}****MI*{member_id}",
    ]
    if status == "not_found":
        body.append("AAA*N**75*C")
    else:
        body.append(f"DMG*D8*{d}")
        if status == "active":
            body += ["EB*1*IND*30**MEDICARE PART B", "EB*1*IND*12**MEDICARE PART B"]
        else:
            body.append("EB*6*IND*30**MEDICARE PART B")
    body.append(f"SE*{len(body) + 1}*0001")
    isa = ("ISA*00*          *00*          *ZZ*" + payer.ljust(15) + "*ZZ*CLEARANCE      "
           "*260301*1200*^*00501*" + control + "*0*T*:")
    return "~\n".join([isa, "GS*HB*" + payer + "*CLEARANCE*20260301*1200*1*X*005010X279A1",
                       *body, "GE*1*1", "IEA*1*" + control]) + "~\n"
