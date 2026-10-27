"""Synthetic CPAP (E0601) referral packets with planted, labeled defects.

Expected rule outcomes and routes are derived from the planted defects here,
independently of the engine, so the eval measures the engine rather than
echoing it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from .people import (
    CLINICS,
    STEDI_MEDICARE_MEMBER,
    SUPPLIER,
    Person,
    Practitioner,
    bad_npi,
    fmt_date,
)
from .render import Document, H, PageDraw, W

PACK_ID = "medicare-pap-osa-initial"

# defect -> (rule outcomes it forces, route it implies)
DEFECTS = {
    "f2f_after_test": ({"f2f_before_test": "fail"}, "needs_info"),
    "mild_no_symptom": ({"qualifying_sleep_test": "fail"}, "human_review"),
    "mild_buried_symptom": ({}, "ready"),
    "unsigned_order": ({"order_signed": "fail"}, "needs_info"),
    "missing_sleep_study": ({"qualifying_sleep_test": "fail", "f2f_before_test": "fail"},
                            "needs_info"),
    "invalid_npi": ({"provider_active": "fail"}, "human_review"),
    "inactive_npi": ({"provider_active": "fail"}, "human_review"),
    "eligibility_inactive": ({"coverage_active": "fail"}, "human_review"),
    "illegible_ahi": ({"qualifying_sleep_test": "unverifiable"}, "human_review"),
}
RULES = ["order_signed", "order_elements", "mbi_format", "f2f_before_test",
         "qualifying_sleep_test", "provider_active", "coverage_active"]
ROUTE_RANK = {"ready": 0, "needs_info": 1, "human_review": 2}

# 40 orders: 16 clean, 24 with at least one planted defect (two with two).
PLAN = (
    [[]] * 16
    + [["f2f_after_test"]] * 3 + [["mild_no_symptom"]] * 3 + [["mild_buried_symptom"]] * 4
    + [["unsigned_order"]] * 2 + [["unsigned_order", "f2f_after_test"]]
    + [["missing_sleep_study"]] * 3 + [["invalid_npi"]] * 2 + [["inactive_npi"]]
    + [["eligibility_inactive"]] * 2 + [["eligibility_inactive", "unsigned_order"]]
    + [["illegible_ahi"]] * 2
)

SYMPTOM_EXPLICIT = [
    "She reports excessive daytime sleepiness; Epworth Sleepiness Scale 15/24.",
    "Long-standing insomnia with frequent awakenings, despite good sleep hygiene.",
    "History of hypertension, treated with lisinopril 20 mg daily.",
]
# Documented, but only by meaning: no keyword a string match would catch.
SYMPTOM_BURIED = [
    "Social: retired machinist. His daughter no longer lets him drive the grandchildren "
    "since he nodded off at a stoplight last spring.",
    "Wife says he has been forgetting appointments and losing his train of thought at "
    "work for the past year, which is new for him.",
    "PMH: hyperlipidemia; osteoarthritis of both knees; essential HTN on amlodipine; "
    "remote appendectomy.",
    "He had a small left-sided CVA in 2019 with full recovery of strength.",
]
SYMPTOM_DENIED = (
    "Denies daytime sleepiness or drowsy driving; Epworth 6/24. Denies insomnia. "
    "Mood is good and cognition intact. No history of hypertension, heart disease, or stroke."
)
HPI_BASE = [
    "Patient presents for evaluation of loud nightly snoring and witnessed pauses in "
    "breathing reported by the bed partner.",
    "Referred for suspected obstructive sleep apnea after a partner noted choking and gasping "
    "during sleep.",
    "Presents with snoring for many years and morning headaches several days per week.",
]


@dataclass
class PapOrder:
    order_id: str
    patient: Person
    practitioner: Practitioner
    defects: list[str]
    level: str
    f2f: date
    test: date
    order: date
    test_type: str
    ahi: float
    events: int
    signed: bool
    npi_on_order: str
    symptom: str                  # explicit | buried | denied | none
    symptom_text: str
    eligibility: str              # active | inactive
    extra_page: bool
    shuffle: bool
    cover: bool
    layout: dict[str, int] = field(default_factory=dict)
    page_types: list[str] = field(default_factory=list)

    @property
    def has_sleep_study(self) -> bool:
        return "missing_sleep_study" not in self.defects

    def expected_fields(self) -> dict:
        study = self.has_sleep_study
        return {
            "patient_name": self.patient.name,
            "patient_dob": self.patient.dob.isoformat(),
            "medicare_id": self.patient.mbi,
            "ordering_provider_name": self.practitioner.signed_name,
            "ordering_npi": self.npi_on_order,
            "item_ordered": "E0601",
            "order_date": self.order.isoformat(),
            "order_signed": self.signed,
            "f2f_date": self.f2f.isoformat(),
            "sleep_test_date": self.test.isoformat() if study else None,
            "sleep_test_type": self.test_type if study else None,
            "ahi_or_rdi": self.ahi if study else None,
            "total_events": self.events if study else None,
        }

    def expected_rules(self) -> dict[str, str]:
        out = dict.fromkeys(RULES, "pass")
        for d in self.defects:
            for rule, outcome in DEFECTS[d][0].items():
                if out[rule] != "fail":           # fail wins over unverifiable
                    out[rule] = outcome
        return out

    def expected_route(self) -> str:
        routes = [DEFECTS[d][1] for d in self.defects] or ["ready"]
        return max(routes, key=ROUTE_RANK.get)

    def ground_truth(self) -> dict:
        return {
            "order_id": self.order_id, "pack_id": PACK_ID, "defects": self.defects,
            "degradation": self.level, "fields": self.expected_fields(),
            "illegible_fields": ["ahi_or_rdi"] if "illegible_ahi" in self.defects else [],
            "rules": self.expected_rules(), "route": self.expected_route(),
            "page_types": self.page_types, "symptom": self.symptom,
            "synthetic": True,
        }


def plan_orders(rng: random.Random, practitioners: list[Practitioner],
                used_names: set[str], make_patient, count: int = 40) -> list[PapOrder]:
    plan = list(PLAN[:count])
    rng.shuffle(plan)
    active = [p for p in practitioners if p.status == "A"]
    inactive = [p for p in practitioners if p.status != "A"]
    orders = []
    stedi_slots = {i for i, d in enumerate(plan) if not d}
    stedi_slots = set(sorted(stedi_slots)[:4])     # four clean orders use Stedi's mock member
    for i, defects in enumerate(plan):
        defects = list(defects)
        patient = STEDI_MEDICARE_MEMBER if i in stedi_slots else make_patient(rng, used_names)
        prac = rng.choice(inactive if "inactive_npi" in defects else active)
        test = date(2026, 1, 5) + timedelta(days=rng.randint(0, 220))
        f2f = test - timedelta(days=rng.randint(7, 75))
        if "f2f_after_test" in defects:
            f2f = test + timedelta(days=rng.randint(2, 20))
        order = max(test, f2f) + timedelta(days=rng.randint(2, 21))
        mild = defects and defects[0] in ("mild_no_symptom", "mild_buried_symptom")
        mild = mild or rng.random() < 0.15
        if mild:
            ahi = round(rng.uniform(5.2, 14.6), 1)
            hours = rng.uniform(5.5, 7.5)
            events = max(10, int(ahi * hours))
        else:
            ahi = round(rng.uniform(16, 58), 1)
            events = int(ahi * rng.uniform(5.5, 7.5))
        if "mild_no_symptom" in defects:
            symptom, text = "denied", SYMPTOM_DENIED
        elif "mild_buried_symptom" in defects:
            symptom, text = "buried", rng.choice(SYMPTOM_BURIED)
        elif mild:
            symptom, text = "explicit", rng.choice(SYMPTOM_EXPLICIT)
        else:
            symptom, text = "none", ""
        level = ["clean", "light", "heavy"][i % 3]
        if "illegible_ahi" in defects:
            level = "heavy"
        orders.append(PapOrder(
            order_id=f"pap-{i + 1:03d}", patient=patient, practitioner=prac, defects=defects,
            level=level, f2f=f2f, test=test, order=order,
            test_type=rng.choice(["PSG", "HST", "HST"]), ahi=ahi, events=events,
            signed="unsigned_order" not in defects,
            npi_on_order=bad_npi(rng) if "invalid_npi" in defects else prac.npi,
            symptom=symptom, symptom_text=text,
            eligibility="inactive" if "eligibility_inactive" in defects else "active",
            extra_page=rng.random() < 0.3, shuffle=rng.random() < 0.35,
            cover=rng.random() < 0.85,
            layout={k: rng.randrange(3) for k in ("swo", "f2f", "psg", "card", "cover")},
        ))
    return orders


# --- page builders ------------------------------------------------------------------

def _letterhead(p: PageDraw, clinic: tuple[str, str, str]):
    p.text(p.style.margin, H - 46, clinic[0], size=13, bold=True)
    p.text(p.style.margin, H - 60, f"{clinic[1]} · Tel {clinic[2]}", size=8.5)
    p.y = H - 92


def page_cover(doc: Document, o: PapOrder, total_pages: int):
    p = doc.new_page()
    clinic = CLINICS[o.practitioner.clinic]
    v = o.layout["cover"]
    p.y = H - 80
    p.heading(["FAX", "FACSIMILE TRANSMITTAL", "Fax Cover Sheet"][v], size=26 if v == 0 else 18)
    p.skip(8)
    p.kv("To", f"{SUPPLIER[0]} — Intake")
    p.kv("Fax", SUPPLIER[1].split("Fax ")[1])
    p.kv("From", clinic[0])
    p.kv("Phone", clinic[2])
    p.kv("Date", fmt_date(o.order + timedelta(days=1), v))
    p.kv("Number of pages", f"{total_pages} (including cover)")
    p.kv("Re", f"{getattr(o, 'item_label', 'CPAP order')} for patient {o.patient.name}")
    p.skip(14)
    p.para("Please find attached the order and supporting documentation for the above patient. "
           "Call our office with any questions.")
    p.skip(30)
    p.para("CONFIDENTIALITY NOTICE: This facsimile contains information intended only for the "
           "named recipient. If you received it in error, notify the sender and destroy all "
           "copies.", size=8.5)


def page_swo(doc: Document, o: PapOrder):
    p = doc.new_page()
    clinic, pr, pt = CLINICS[o.practitioner.clinic], o.practitioner, o.patient
    v = o.layout["swo"]
    _letterhead(p, clinic)
    item = "E0601 Continuous positive airway pressure (CPAP) device"
    if v == 0:
        p.heading("STANDARD WRITTEN ORDER")
        p.kv("Patient Name", pt.name, name="patient_name")
        p.kv("Date of Birth", fmt_date(pt.dob, 0))
        p.kv("Medicare ID", pt.mbi)
        p.kv("Order Date", fmt_date(o.order, 0), name="order_date")
        p.kv("Item Ordered", item)
        p.kv("Quantity", "1")
        p.kv("Supplies", "A7030 full face mask 1 per 3 months; A7038 filters 2 per month")
        p.kv("Length of Need", "99 months (lifetime)")
        p.kv("Diagnosis", "G47.33 Obstructive sleep apnea")
        p.skip(6)
        p.kv("Ordering Practitioner", pr.signed_name)
        p.kv("Practitioner NPI", o.npi_on_order, name="npi")
        p.skip(14)
        p.signature("Practitioner Signature", o.signed, name="signature")
        p.kv("Date Signed", fmt_date(o.order, 0) if o.signed else "")
    elif v == 1:
        p.heading("DME ORDER / PRESCRIPTION")
        p.kv_row([("Beneficiary Name", pt.name), ("DOB", fmt_date(pt.dob, 2))])
        p.kv_row([("MBI", pt.mbi), ("Date of Order", fmt_date(o.order, 2))])
        p.rule_line()
        p.kv("Equipment", item)
        p.kv("Settings", "APAP 5-15 cm H2O, heated humidification")
        p.kv("Diagnosis (ICD-10)", "G47.33")
        p.kv("Length of Need", "Lifetime")
        p.rule_line()
        p.kv_row([("Prescriber", pr.signed_name), ("Prescriber NPI", o.npi_on_order)])
        p.skip(16)
        p.signature("Signature", o.signed, name="signature")
    else:
        p.heading("Order for Durable Medical Equipment")
        p.kv("Patient", pt.name)
        p.kv("Date of Birth", fmt_date(pt.dob, 1))
        p.kv("Medicare Number", pt.mbi)
        p.kv("Date Ordered", fmt_date(o.order, 1))
        p.subheading("Equipment requested")
        p.checkbox("E0601 CPAP device", True)
        p.checkbox("E0470 Respiratory assist device (bilevel) without backup rate", False)
        p.checkbox("E0562 Heated humidifier", True)
        p.kv("Device", "E0601 CPAP with heated humidifier")
        p.kv("Length of Need", "99")
        p.skip(6)
        p.kv("Treating Practitioner", pr.signed_name)
        p.kv("NPI", o.npi_on_order)
        p.skip(16)
        p.signature("Practitioner Signature", o.signed, name="signature",
                    printed=pr.signed_name)


def page_f2f(doc: Document, o: PapOrder, rng: random.Random):
    p = doc.new_page()
    clinic, pr, pt = CLINICS[o.practitioner.clinic], o.practitioner, o.patient
    v = o.layout["f2f"]
    _letterhead(p, clinic)
    p.heading(["Office Visit Note", "Sleep Medicine Consultation", "Progress Note"][v])
    label = ["Date of Visit", "Encounter Date", "Visit Date"][v]
    if v == 1:
        p.kv_row([("Patient", pt.name), ("DOB", fmt_date(pt.dob, 0))])
        p.kv_row([(label, fmt_date(o.f2f, 0)), ("Provider", pr.signed_name)])
    else:
        p.kv("Patient", pt.name)
        p.kv("DOB", fmt_date(pt.dob, v))
        p.kv(label, fmt_date(o.f2f, v))
        p.kv("Provider", pr.signed_name)
    p.rule_line()
    he = "She" if pt.first in {"Marisol", "Agnes", "Priya", "Lucinda", "Rosalind", "Ingrid",
                               "Odessa", "Yvette", "Hattie", "Mabel", "Beatriz", "Esther",
                               "Lorraine", "Delphine", "Nadia", "Celeste", "Jane"} else "He"
    p.subheading("Chief Complaint")
    p.para(rng.choice(["Snoring and witnessed apneas.", "Possible sleep apnea.",
                       "Loud snoring, unrefreshing sleep."]))
    p.subheading("History of Present Illness")
    hpi = rng.choice(HPI_BASE)
    if o.symptom in ("explicit", "denied"):
        hpi += " " + o.symptom_text.replace("She ", f"{he} ")
    p.para(hpi)
    p.subheading("Past Medical / Social History")
    pmh = rng.choice(["Hyperlipidemia. GERD.", "Osteoarthritis. Seasonal allergies.",
                      "Prediabetes. Former smoker, quit 2004."])
    if o.symptom == "buried":
        pmh = o.symptom_text
    p.para(pmh)
    p.subheading("Exam")
    bp = (f"{rng.randint(112, 128)}/{rng.randint(70, 80)}" if o.symptom in ("denied", "buried")
          else f"{rng.randint(112, 150)}/{rng.randint(70, 92)}")
    p.para(f"BP {bp}, HR {rng.randint(60, 88)}, BMI {rng.uniform(27, 41):.1f}. Neck "
           f"circumference {rng.randint(15, 19)} in. Mallampati {rng.choice(['III', 'IV'])}. "
           "Lungs clear.")
    p.subheading("Assessment and Plan")
    p.para("Clinical presentation is consistent with suspected obstructive sleep apnea. "
           "Will obtain a diagnostic sleep study and follow up with results.")
    p.skip(10)
    p.text(p.style.margin, p.y, f"Electronically signed by {pr.signed_name}")


def page_sleep_study(doc: Document, o: PapOrder, rng: random.Random):
    p = doc.new_page()
    pt = o.patient
    v = o.layout["psg"]
    p.y = H - 60
    psg = o.test_type == "PSG"
    p.heading(["Diagnostic Sleep Study Report", "Polysomnography Report",
               "Sleep Study Interpretation"][v] if psg else
              ["Home Sleep Apnea Test Report", "Home Sleep Apnea Test (HSAT) Results",
               "Sleep Study Interpretation"][v])
    p.text(p.style.margin, p.y + 4, "Northgate Sleep Disorders Center · Accredited sleep facility",
           size=8.5)
    p.y -= 12
    p.kv("Patient Name", pt.name)
    p.kv("Date of Birth", fmt_date(pt.dob, v))
    p.kv(["Study Date", "Date of Study", "Recording Date"][v], fmt_date(o.test, v))
    p.kv(["Study Type", "Type of Study", "Test Type"][v],
         "Attended in-lab polysomnogram, Type I (PSG)" if psg
         else "Home sleep apnea test, Type III (HST)")
    hours = o.events / o.ahi
    p.kv("Total Recording Time", f"{hours * 60 + rng.randint(10, 40):.0f} min")
    p.rule_line()
    p.subheading("Respiratory Summary")
    idx_label = (["AHI", "Apnea-Hypopnea Index", "AHI"][v] if psg
                 else ["RDI", "Respiratory Disturbance Index", "REI"][v])
    ev_label = ["Total Respiratory Events", "Total Events", "Total Apneas + Hypopneas"][v]
    if v == 1:
        p.kv(idx_label, f"{o.ahi} events/hour", name="ahi")
        p.kv(ev_label, str(o.events))
    else:
        p.kv_row([(idx_label, f"{o.ahi} /hr"), (ev_label, str(o.events))], names=["ahi", None])
    p.kv("Lowest SpO2", f"{rng.randint(78, 89)}%")
    p.kv("Time with SpO2 below 89%", f"{rng.uniform(0.5, 14):.1f} min")
    p.rule_line()
    p.subheading("Interpretation")
    severity = "mild" if o.ahi < 15 else "moderate" if o.ahi < 30 else "severe"
    p.para(f"This study demonstrates {severity} obstructive sleep apnea. Respiratory events "
           "were predominantly obstructive hypopneas. Recommend clinical correlation and "
           "consideration of positive airway pressure therapy.")
    p.skip(10)
    p.text(p.style.margin, p.y, "Interpreting physician: Helena Marchetti, MD, "
           "Diplomate, American Board of Sleep Medicine", size=9)


def page_card(doc: Document, o: PapOrder):
    p = doc.new_page()
    pt = o.patient
    x0, y0, w, h = 120, H - 330, W - 240, 210
    c = p.canvas
    c.setLineWidth(1.5)
    c.roundRect(x0, y0, w, h, 12)
    p.y = y0 + h - 30
    p.style = p.style.__class__(p.style.font, p.style.bold, 11, x0 + 20)
    p.heading("MEDICARE HEALTH INSURANCE", size=14)
    p.kv("Name", pt.name.upper())
    p.kv("Medicare Number", pt.mbi)
    p.kv("Entitled to", "HOSPITAL (PART A) · MEDICAL (PART B)")
    p.kv("Coverage starts", fmt_date(date(pt.dob.year + 65, pt.dob.month, 1), 0))
    p.style = p.style.__class__(p.style.font, p.style.bold, 10.5, 54)
    p.y = y0 - 40
    p.para("Copy of beneficiary's Medicare card (front).")


def page_extra(doc: Document, o: PapOrder, rng: random.Random):
    p = doc.new_page()
    kind = rng.randrange(3)
    pt = o.patient
    if kind == 0:
        p.heading("Pharmacy Refill Authorization Request")
        p.kv("Patient Name", pt.name)
        p.kv("Medication", rng.choice(["Atorvastatin 40 mg", "Omeprazole 20 mg"]))
        p.para("Please authorize a 90-day refill. Fax back to the pharmacy at (253) 555-0188.")
    elif kind == 1:
        p.heading("Laboratory Results: Lipid Panel")
        p.kv("Patient Name", pt.name)
        p.kv("Collected", fmt_date(o.f2f - timedelta(days=30), 0))
        for k, val in [("Total cholesterol", "212 mg/dL"), ("LDL", "131 mg/dL"),
                       ("HDL", "44 mg/dL"), ("Triglycerides", "186 mg/dL")]:
            p.kv(k, val)
    else:
        p.heading("Acknowledgment of Receipt of Notice of Privacy Practices")
        p.para("I acknowledge that I have received a copy of this office's Notice of Privacy "
               "Practices, which describes how my health information may be used and disclosed.")
        p.kv("Patient", pt.name)


def render_packet(o: PapOrder, path, rng: random.Random) -> list[dict]:
    """Draw the packet; returns per-page field boxes in packet order."""
    builders = [("order_swo", lambda d: page_swo(d, o)),
                ("face_to_face_note", lambda d: page_f2f(d, o, rng))]
    if o.has_sleep_study:
        builders.append(("sleep_study_report", lambda d: page_sleep_study(d, o, rng)))
    builders.append(("insurance_card", lambda d: page_card(d, o)))
    if o.extra_page:
        builders.append(("other", lambda d: page_extra(d, o, rng)))
    if o.shuffle:
        rng.shuffle(builders)
    if o.cover:
        total = len(builders) + 1
        builders.insert(0, ("cover_sheet", lambda d: page_cover(d, o, total)))
    doc = Document(path, rng)
    for _, build in builders:
        build(doc)
    doc.save()
    o.page_types = [t for t, _ in builders]
    return doc.page_boxes
