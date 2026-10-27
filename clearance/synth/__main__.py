"""Regenerate synthetic packets, ground truth, provider roster, and 271 fixtures.

    uv run python -m clearance.synth [--pack pap|oxygen|all] [--seed 7]

Deterministic for a given seed.
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from pathlib import Path

from ..integrations.x12_decode import build_271
from . import oxygen, pap
from .fax import degrade, rasterize, save_pdf, smudge
from .people import make_patient, make_practitioners, roster

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def write_packet(order, render, out_pdf: Path, rng: random.Random) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean.pdf"
        boxes = render(order, clean, rng)
        pages = [degrade(img, order.level, rng) for img in rasterize(clean)]
    for name in order.ground_truth().get("illegible_fields", []):
        box_key = {"ahi_or_rdi": "ahi"}.get(name, name)
        for i, page_boxes in enumerate(boxes):
            if box_key in page_boxes:
                pages[i] = smudge(pages[i], page_boxes[box_key], rng)
    save_pdf(pages, out_pdf)


def write_eligibility(patient, status: str) -> None:
    d = DATA / "fixtures" / "eligibility"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{patient.mbi}.x12").write_text(
        build_271(patient.mbi, patient.first, patient.last, patient.dob.isoformat(), status))


def generate(packs: list[str], seed: int) -> None:
    rng = random.Random(seed)
    practitioners = make_practitioners(rng)
    synth = DATA / "synth"
    synth.mkdir(parents=True, exist_ok=True)
    (synth / "providers.json").write_text(json.dumps(roster(practitioners), indent=2) + "\n")
    used: set[str] = set()
    gt_dir = DATA / "ground_truth"
    gt_dir.mkdir(parents=True, exist_ok=True)

    if "pap" in packs:
        out = DATA / "fixtures" / "pap"
        out.mkdir(parents=True, exist_ok=True)
        for o in pap.plan_orders(rng, practitioners, used, make_patient):
            write_packet(o, pap.render_packet, out / f"{o.order_id}.pdf", rng)
            write_eligibility(o.patient, o.eligibility)
            (gt_dir / f"{o.order_id}.json").write_text(json.dumps(o.ground_truth(), indent=2))
            print(f"{o.order_id}  {o.level:<6} {o.expected_route():<13} {','.join(o.defects)}")

    if "oxygen" in packs:
        orng = random.Random(seed + 1)      # independent stream: regenerating one suite
        out = DATA / "fixtures" / "oxygen"  # never reshuffles the other
        out.mkdir(parents=True, exist_ok=True)
        for o in oxygen.plan_orders(orng, practitioners, set(), make_patient):
            write_packet(o, oxygen.render_packet, out / f"{o.order_id}.pdf", orng)
            write_eligibility(o.patient, o.eligibility)
            (gt_dir / f"{o.order_id}.json").write_text(json.dumps(o.ground_truth(), indent=2))
            print(f"{o.order_id}  {o.level:<6} {o.expected_route():<13} {','.join(o.defects)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="all", choices=["pap", "oxygen", "all"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    generate(["pap", "oxygen"] if args.pack == "all" else [args.pack], args.seed)


if __name__ == "__main__":
    main()
