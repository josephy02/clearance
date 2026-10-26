"""Synthetic patients, practitioners, and clinics. None of these are real."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from ..integrations.npi import npi_check_digit

FIRST = ["Marisol", "Theodore", "Agnes", "Wendell", "Priya", "Harold", "Lucinda", "Desmond",
         "Rosalind", "Calvin", "Ingrid", "Bernard", "Odessa", "Floyd", "Yvette", "Leonard",
         "Hattie", "Russell", "Mabel", "Clifford", "Beatriz", "Gordon", "Esther", "Vernon",
         "Lorraine", "Arturo", "Delphine", "Milton", "Nadia", "Raymond", "Celeste", "Otis"]
LAST = ["Okafor", "Brannigan", "Lindqvist", "Paredes", "Achterberg", "Whitcombe", "Nakashima",
        "Delacroix", "Fairbanks", "Oyelaran", "Castellanos", "Halvorsen", "Pemberton",
        "Quintanilla", "Rasmussen", "Sorensen", "Thibodeaux", "Underhill", "Valderrama",
        "Wojcik", "Yarborough", "Zielinski", "Abernathy", "Brightwater", "Crowhurst", "Dunleavy"]

CLINICS = [
    ("Riverbend Pulmonary & Sleep", "4120 Alder Way, Suite 200, Tacoma, WA 98405",
     "(253) 555-0142"),
    ("Cedar Hollow Family Medicine", "88 Mill Pond Rd, Olympia, WA 98501", "(360) 555-0177"),
    ("Northgate Sleep Disorders Center", "1501 N 115th St, Seattle, WA 98133", "(206) 555-0119"),
    ("Puget Internal Medicine Associates", "2210 Harbor Ave, Everett, WA 98201", "(425) 555-0163"),
]
SUPPLIER = ("Cascade Sleep & Respiratory", "(253) 555-0100 · Fax (253) 555-0199")

MBI_LETTERS = "ACDEFGHJKMNPQRTUVWXY"


@dataclass
class Person:
    first: str
    last: str
    dob: date
    mbi: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


@dataclass
class Practitioner:
    first: str
    last: str
    credential: str
    npi: str
    status: str = "A"                     # registry status; "D" = deactivated
    clinic: int = 0

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def signed_name(self) -> str:
        return f"{self.first} {self.last}, {self.credential}"


def make_mbi(rng: random.Random) -> str:
    """A format-valid MBI. Synthetic: generated, never issued."""
    alnum = MBI_LETTERS + "0123456789"
    return "".join([
        str(rng.randint(1, 9)), rng.choice(MBI_LETTERS), rng.choice(alnum), str(rng.randint(0, 9)),
        rng.choice(MBI_LETTERS), rng.choice(alnum), str(rng.randint(0, 9)),
        rng.choice(MBI_LETTERS), rng.choice(MBI_LETTERS), f"{rng.randint(0, 99):02d}",
    ])


def make_patient(rng: random.Random, used: set[str]) -> Person:
    while True:
        p = Person(rng.choice(FIRST), rng.choice(LAST),
                   date(1938, 1, 1) + timedelta(days=rng.randint(0, 365 * 23)), make_mbi(rng))
        if p.name not in used:
            used.add(p.name)
            return p


# Stedi's documented test-mode Medicare member. Orders for her exercise the live
# eligibility path when STEDI_API_KEY is set.
STEDI_MEDICARE_MEMBER = Person("Jane", "Doe", date(1955, 5, 5), "1AA2CC3DD45")

PRACTITIONER_NAMES = [
    ("Dana", "Whitfield", "MD"), ("Samuel", "Oduya", "DO"), ("Helena", "Marchetti", "MD"),
    ("Kenji", "Albrecht", "MD"), ("Rowan", "Esposito", "ARNP"), ("Ines", "Varga", "MD"),
    ("Tobias", "Ferreira", "PA-C"), ("Greta", "Lund", "MD"),
]


def make_practitioners(rng: random.Random) -> list[Practitioner]:
    """Luhn-valid synthetic NPIs in a '19' prefix block, one deactivated."""
    out = []
    for i, (first, last, cred) in enumerate(PRACTITIONER_NAMES):
        body = "19" + "".join(str(rng.randint(0, 9)) for _ in range(7))
        out.append(Practitioner(first, last, cred, body + npi_check_digit(body),
                                status="D" if i == len(PRACTITIONER_NAMES) - 1 else "A",
                                clinic=i % len(CLINICS)))
    return out


def roster(practitioners: list[Practitioner]) -> dict[str, dict]:
    return {p.npi: {"name": p.name, "status": p.status, "type": "NPI-1",
                    "taxonomy": ("Sleep Medicine" if p.credential != "PA-C"
                                 else "Physician Assistant")}
            for p in practitioners}


def bad_npi(rng: random.Random) -> str:
    """Ten digits that fail the check-digit rule."""
    body = "19" + "".join(str(rng.randint(0, 9)) for _ in range(7))
    good = int(npi_check_digit(body))
    return body + str((good + rng.randint(1, 9)) % 10)


def fmt_date(d: date, style: int) -> str:
    return [d.strftime("%m/%d/%Y"), d.strftime("%B %-d, %Y"), d.isoformat(),
            d.strftime("%d-%b-%Y")][style % 4]
