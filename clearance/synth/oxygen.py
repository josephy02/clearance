"""Synthetic home-oxygen referral packets with planted, labeled defects.

Reuses the CPAP generator's cover sheet, Medicare card, and filler pages; adds
an oxygen order, a pulmonology visit note, and an oximetry/blood gas report.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from .pap import _letterhead, page_card, page_cover, page_extra
from .people import CLINICS, Person, Practitioner, bad_npi, fmt_date
from .render import H, Document

PACK_ID = "medicare-oxygen-home"
RULES = ["order_signed", "order_elements", "mbi_format", "qualifying_blood_gas",
         "qualified_tester", "test_before_order", "provider_active", "coverage_active"]
ROUTE_RANK = {"ready": 0, "needs_info": 1, "human_review": 2}

DEFECTS = {
    "not_qualifying": ({"qualifying_blood_gas": "fail"}, "needs_info"),
    "group2_with_condition": ({}, "ready"),
    "group2_no_condition": ({"qualifying_blood_gas": "fail"}, "human_review"),
    "tested_by_supplier": ({"qualified_tester": "fail"}, "needs_info"),
    "test_after_order": ({"test_before_order": "fail"}, "needs_info"),
    "not_room_air": ({"qualifying_blood_gas": "fail"}, "needs_info"),
    "unsigned_order": ({"order_signed": "fail"}, "needs_info"),
    "invalid_npi": ({"provider_active": "fail"}, "human_review"),
}

# 20 orders: 8 clean, 12 with a planted defect.
PLAN = ([[]] * 8 + [["not_qualifying"]] * 2 + [["group2_with_condition"]] * 2
        + [["group2_no_condition"]] * 2 + [["tested_by_supplier"]] * 2
        + [["test_after_order"]] + [["not_room_air"]] + [["unsigned_order"]] + [["invalid_npi"]])

EDEMA = [
    "Exam notable for 2+ pitting edema of both ankles; echo last year suggested right heart strain.",
    "Echocardiogram 2025 showed moderate pulmonary hypertension (RVSP 52 mm Hg).",
]
NO_EDEMA = "No peripheral edema. Heart regular, no murmurs. No history of heart failure."
PERFORMERS = {
    "practitioner": "Office oximetry by treating practitioner (practitioner)",
    "laboratory": "Pulmonary function laboratory (laboratory)",
    "hospital": "Hospital respiratory therapy department (hospital)",
    "supplier": "Cascade Sleep & Respiratory, DME supplier (supplier)",
}
CONDITION_TEXT = {
    "rest": "At rest, awake (rest)",
    "exercise": "During exercise, 6-minute walk (exercise)",
    "sleep": "Overnight, during sleep (sleep)",
}


@dataclass
class OxygenOrder:
    order_id: str
    patient: Person
    practitioner: Practitioner
    defects: list[str]
    level: str
    f2f: date
    test: date
    order: date
    condition: str
    spo2: int | None
    po2: int | None
    baseline: int | None
    room_air: bool
    performer: str
    hematocrit: int | None
    exam_text: str
    signed: bool
    npi_on_order: str
    extra_page: bool
    shuffle: bool
    cover: bool
    item_label: str = "Home oxygen order"
    eligibility: str = "active"
    layout: dict[str, int] = field(default_factory=dict)
    page_types: list[str] = field(default_factory=list)

    def expected_fields(self) -> dict:
        return {
            "patient_name": self.patient.name, "patient_dob": self.patient.dob.isoformat(),
            "medicare_id": self.patient.mbi,
            "ordering_provider_name": self.practitioner.signed_name,
            "ordering_npi": self.npi_on_order, "item_ordered": "E1390",
            "order_date": self.order.isoformat(), "order_signed": self.signed,
            "test_date": self.test.isoformat(), "test_condition": self.condition,
            "on_room_air": self.room_air, "spo2": self.spo2, "po2": self.po2,
            "baseline_spo2": self.baseline, "test_performer": self.performer,
            "hematocrit": self.hematocrit,
        }

    def expected_rules(self) -> dict[str, str]:
        out = dict.fromkeys(RULES, "pass")
        for d in self.defects:
            out.update(DEFECTS[d][0])
        return out

    def expected_route(self) -> str:
        return max([DEFECTS[d][1] for d in self.defects] or ["ready"], key=ROUTE_RANK.get)

    def ground_truth(self) -> dict:
        return {"order_id": self.order_id, "pack_id": PACK_ID, "defects": self.defects,
                "degradation": self.level, "fields": self.expected_fields(),
                "illegible_fields": [], "rules": self.expected_rules(),
                "route": self.expected_route(), "page_types": self.page_types,
                "synthetic": True}


def plan_orders(rng: random.Random, practitioners: list[Practitioner], used: set[str],
                make_patient) -> list[OxygenOrder]:
    plan = list(PLAN)
    rng.shuffle(plan)
    active = [p for p in practitioners if p.status == "A"]
    out = []
    for i, defects in enumerate(plan):
        prac = rng.choice(active)
        test = date(2026, 1, 10) + timedelta(days=rng.randint(0, 220))
        f2f = test - timedelta(days=rng.randint(0, 10))
        order = test + timedelta(days=rng.randint(0, 12))
        if "test_after_order" in defects:
            order = test - timedelta(days=rng.randint(3, 14))
        condition = rng.choice(["rest", "rest", "exercise", "sleep"])
        abg = rng.random() < 0.3
        spo2 = po2 = baseline = hematocrit = None
        if "group2_with_condition" in defects or "group2_no_condition" in defects:
            condition = "rest"
            spo2 = 89
        elif "not_qualifying" in defects:
            condition = "rest"
            spo2 = rng.randint(91, 94)
        elif abg and condition == "rest":
            po2 = rng.randint(46, 55)
        else:
            spo2 = rng.randint(80, 88)
        if condition in ("exercise", "sleep"):
            baseline = rng.randint(90, 95)
        exam = NO_EDEMA
        if "group2_with_condition" in defects:
            exam = rng.choice(EDEMA)
        out.append(OxygenOrder(
            order_id=f"oxy-{i + 1:03d}", patient=make_patient(rng, used), practitioner=prac,
            defects=list(defects), level=["clean", "light", "heavy"][i % 3],
            f2f=f2f, test=test, order=order, condition=condition, spo2=spo2, po2=po2,
            baseline=baseline, room_air="not_room_air" not in defects,
            performer="supplier" if "tested_by_supplier" in defects
            else rng.choice(["practitioner", "laboratory", "hospital"]),
            hematocrit=hematocrit, exam_text=exam, signed="unsigned_order" not in defects,
            npi_on_order=bad_npi(rng) if "invalid_npi" in defects else prac.npi,
            extra_page=rng.random() < 0.25, shuffle=rng.random() < 0.35,
            cover=rng.random() < 0.85,
            layout={k: rng.randrange(3) for k in ("swo", "f2f", "abg", "card", "cover")},
        ))
    return out


def page_order(doc: Document, o: OxygenOrder):
    p = doc.new_page()
    pr, pt = o.practitioner, o.patient
    v = o.layout["swo"]
    _letterhead(p, CLINICS[pr.clinic])
    p.heading(["STANDARD WRITTEN ORDER: OXYGEN", "DME ORDER / PRESCRIPTION",
               "Order for Home Oxygen"][v])
    p.kv(["Patient Name", "Beneficiary Name", "Patient"][v], pt.name)
    p.kv(["Date of Birth", "DOB", "Date of Birth"][v], fmt_date(pt.dob, v))
    p.kv(["Medicare ID", "MBI", "Medicare Number"][v], pt.mbi)
    p.kv(["Order Date", "Date of Order", "Date Ordered"][v], fmt_date(o.order, v))
    p.kv(["Item Ordered", "Equipment", "Device"][v],
         "E1390 Oxygen concentrator, stationary; E0431 portable gaseous system")
    p.kv("Flow rate", f"{o.rng_flow} L/min via nasal cannula, continuous")
    p.kv("Length of Need", "99 months (lifetime)")
    p.kv("Diagnosis", "J44.9 Chronic obstructive pulmonary disease")
    p.skip(6)
    p.kv(["Ordering Practitioner", "Prescriber", "Treating Practitioner"][v], pr.signed_name)
    p.kv(["Practitioner NPI", "Prescriber NPI", "NPI"][v], o.npi_on_order)
    p.skip(14)
    p.signature(["Practitioner Signature", "Signature", "Practitioner Signature"][v], o.signed,
                name="signature", printed=pr.signed_name if v == 2 else None)


def page_visit(doc: Document, o: OxygenOrder, rng: random.Random):
    p = doc.new_page()
    pr, pt = o.practitioner, o.patient
    v = o.layout["f2f"]
    _letterhead(p, CLINICS[pr.clinic])
    p.heading(["Office Visit Note", "Pulmonology Follow-up", "Progress Note"][v])
    p.kv("Patient", pt.name)
    p.kv("DOB", fmt_date(pt.dob, v))
    p.kv(["Date of Visit", "Encounter Date", "Visit Date"][v], fmt_date(o.f2f, v))
    p.kv("Provider", pr.signed_name)
    p.rule_line()
    p.subheading("History of Present Illness")
    p.para(rng.choice([
        "Long-standing COPD with progressive dyspnea on exertion, now short of breath "
        "walking one block.",
        "Follow-up of severe emphysema. Reports breathlessness with dressing and bathing.",
    ]))
    p.subheading("Exam")
    p.para(f"BP {rng.randint(118, 138)}/{rng.randint(70, 86)}, HR {rng.randint(70, 96)}, "
           f"RR {rng.randint(16, 22)}. Diminished breath sounds bilaterally. {o.exam_text}")
    p.subheading("Assessment and Plan")
    p.para("COPD with hypoxemia documented on testing reviewed today. Start home oxygen; "
           "recheck in 3 months.")
    p.skip(8)
    p.text(p.style.margin, p.y, f"Electronically signed by {pr.signed_name}")


def page_test(doc: Document, o: OxygenOrder):
    p = doc.new_page()
    pt = o.patient
    v = o.layout["abg"]
    p.y = H - 60
    abg = o.po2 is not None
    p.heading("Arterial Blood Gas Report" if abg else
              ["Oximetry Test Report", "Oxygen Saturation Test", "Overnight Oximetry Report"][v])
    p.kv("Patient Name", pt.name)
    p.kv("Date of Birth", fmt_date(pt.dob, v))
    p.kv(["Test Date", "Date of Test", "Date Performed"][v], fmt_date(o.test, v))
    p.kv(["Test Condition", "Testing Condition", "Condition"][v], CONDITION_TEXT[o.condition])
    p.kv(["On Room Air", "Room Air", "Breathing Room Air"][v],
         "Yes" if o.room_air else "No (on 2 L/min nasal cannula)")
    p.rule_line()
    p.subheading("Results")
    if o.po2 is not None:
        p.kv(["Arterial PO2", "PaO2", "Arterial PO2"][v], f"{o.po2} mm Hg")
        p.kv("PaCO2", "44 mm Hg")
        p.kv("pH", "7.41")
    if o.spo2 is not None:
        p.kv(["Qualifying SpO2", "Lowest SpO2", "Oxygen Saturation"][v], f"{o.spo2}%")
    if o.baseline is not None:
        p.kv(["Awake Resting SpO2", "Baseline SpO2", "Resting SpO2"][v], f"{o.baseline}%")
    p.rule_line()
    p.kv(["Performed By", "Tested By", "Test Performed By"][v], PERFORMERS[o.performer])
    p.para("Results reviewed with the treating practitioner.")


def render_packet(o: OxygenOrder, path, rng: random.Random) -> list[dict]:
    o.rng_flow = rng.choice([1, 2, 2, 3])
    builders = [("order_swo", lambda d: page_order(d, o)),
                ("face_to_face_note", lambda d: page_visit(d, o, rng)),
                ("blood_gas_report", lambda d: page_test(d, o)),
                ("insurance_card", lambda d: page_card(d, o))]
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
