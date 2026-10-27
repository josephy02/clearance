"""FastAPI service and review UI.

    uv run uvicorn clearance.api.app:app --reload     ->  http://127.0.0.1:8000

POST /orders (PDF + pack id) -> order id; GET /orders/{id} -> decision, findings,
citations, quotes, outreach draft. The review UI at / is plain HTML/JS.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..engine.pack import PACKS_DIR, PackError, check_references, list_packs, load_pack, parse_pack
from ..engine.types import ExtractedField, FieldStatus, Route
from ..extract.fields import parse_value
from ..integrations.npi import NppesProviderCheck, npi_is_valid
from ..pipeline import ROOT, Decision, Pipeline
from . import store

UI = ROOT / "ui"
ROUTE_RANK = {Route.HUMAN_REVIEW: 0, Route.NEEDS_INFO: 1, Route.READY: 2}

app = FastAPI(title="clearance", version="0.1.0",
              description="Intake and qualification engine for DME orders. Synthetic data only.")
_pipeline: Pipeline | None = None
_seed = {"state": "idle", "done": 0, "total": 0}


def pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


def _summary(d: Decision) -> dict:
    f = d.extraction.fields
    review = store.load_review(d.order_id)
    return {
        "order_id": d.order_id, "pack_id": d.pack_id, "route": d.route,
        "patient": (f.get("patient_name").value if f.get("patient_name") else None),
        "received_at": store.load_meta(d.order_id).get("received_at"),
        "headline": d.reasons[0] if d.reasons else "",
        "flags": sum(r.outcome in ("fail", "unverifiable") for r in d.results),
        "min_confidence": d.min_confidence,
        "review": review["action"] if review else None,
    }


# --- orders ---------------------------------------------------------------------------

@app.get("/orders")
def list_orders():
    rows = []
    for oid in store.list_orders():
        try:
            rows.append(_summary(store.load_decision(oid)))
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda r: (r["review"] is not None, ROUTE_RANK[Route(r["route"])],
                             r["received_at"] or ""))
    return {"orders": rows, "seed": _seed}


@app.post("/orders")
async def create_order(file: UploadFile = File(...), pack_id: str = Form(...)):
    try:
        pack = load_pack(pack_id)
    except PackError as err:
        raise HTTPException(400, str(err)) from err
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Upload a PDF (the fax packet).")
    order_id = "ord-" + uuid.uuid4().hex[:8]
    path = store.save_packet(order_id, data)
    decision = pipeline().run(path, pack, order_id=order_id)
    store.save_decision(decision)
    store.audit("decision", order_id, route=decision.route, pack=f"{pack.pack_id}@{pack.version}",
                models=decision.models, source=file.filename)
    return {"order_id": order_id, "route": decision.route}


def _order_payload(order_id: str) -> dict:
    try:
        d = store.load_decision(order_id)
    except KeyError as err:
        raise HTTPException(404, f"No order {order_id}") from err
    pack = load_pack(d.pack_id)
    return {
        "decision": json.loads(d.model_dump_json()),
        "pages": store.page_count(order_id),
        "sources": {s.id: s.model_dump() for s in pack.sources},
        "fields": [f.model_dump() for f in pack.extraction.fields],
        "rule_fields": sorted(pack.rule_fields()),
        "review": store.load_review(order_id),
        "audit": store.audit_for(order_id),
        "meta": store.load_meta(order_id),
        "item": pack.item.model_dump(),
    }


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    return _order_payload(order_id)


@app.get("/orders/{order_id}/pages/{n}.png")
def get_page(order_id: str, n: int):
    p = store.order_dir(order_id) / "pages" / f"{n}.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.get("/orders/{order_id}/packet.pdf")
def get_packet(order_id: str):
    p = store.order_dir(order_id) / "packet.pdf"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


class FieldEdit(BaseModel):
    value: str
    reviewer: str = "reviewer"


@app.post("/orders/{order_id}/fields/{name}")
def edit_field(order_id: str, name: str, edit: FieldEdit):
    """A reviewer corrects an extracted value; rules re-run on the corrected record."""
    d = store.load_decision(order_id)
    pack = load_pack(d.pack_id)
    spec = next((f for f in pack.extraction.fields if f.name == name), None)
    if spec is None:
        raise HTTPException(404, f"{d.pack_id} has no field '{name}'")
    raw = edit.value.strip()
    if spec.type == "boolean":
        value = raw.lower() in ("true", "yes", "y", "1", "signed")
    elif raw == "":
        value = None
    else:
        value, _ = parse_value(spec, raw)
        if value is None:
            raise HTTPException(422, f"'{raw}' is not a valid {spec.type} for {name}.")
    field = d.extraction.fields.setdefault(name, ExtractedField(name=name))
    before = field.value
    field.value, field.edited_by = value, edit.reviewer
    field.status = FieldStatus.FOUND if value is not None else FieldStatus.MISSING
    field.confidence = 1.0 if value is not None else 0.0
    new = pipeline().decide(pack, d.extraction, order_id=order_id, timings=d.timings,
                            source_file=d.source_file)
    store.save_decision(new)
    store.audit("field_edit", order_id, field=name, before=before, after=value,
                reviewer=edit.reviewer, route_before=d.route, route_after=new.route)
    return _order_payload(order_id)


class Review(BaseModel):
    action: str                 # approve | override
    route: Route | None = None  # required for override
    note: str = ""
    reviewer: str = "reviewer"


@app.post("/orders/{order_id}/review")
def review_order(order_id: str, review: Review):
    d = store.load_decision(order_id)
    if review.action not in ("approve", "override"):
        raise HTTPException(422, "action must be approve or override")
    if review.action == "override" and (review.route is None or review.route == d.route):
        raise HTTPException(422, "An override needs a route different from the system's.")
    final = review.route if review.action == "override" else d.route
    record = {"action": review.action, "system_route": d.route, "final_route": final,
              "note": review.note, "reviewer": review.reviewer, "at": store.now()}
    store.save_review(order_id, record)
    store.audit(review.action, order_id, **{k: v for k, v in record.items() if k != "at"})
    if review.action == "override":
        store.save_override_case(order_id, {
            "order_id": order_id, "pack_id": d.pack_id, "pack_version": d.pack_version,
            "system_route": d.route, "reviewer_route": final, "note": review.note,
            "fields": {k: v.value for k, v in d.extraction.fields.items()},
            "rules": {r.rule_id: r.outcome for r in d.results},
        })
    return _order_payload(order_id)


@app.delete("/orders/{order_id}/review")
def reopen_order(order_id: str):
    p = store.order_dir(order_id) / "review.json"
    if p.exists():
        p.unlink()
        store.audit("reopen", order_id)
    return _order_payload(order_id)


# --- packs ----------------------------------------------------------------------------

@app.get("/packs")
def get_packs():
    return {"packs": [{"pack_id": p.pack_id, "version": p.version, "item": p.item.model_dump(),
                       "payer": p.payer, "rules": len(p.rules),
                       "fields": len(p.extraction.fields)} for p in list_packs()]}


@app.get("/packs/{pack_id}")
def get_pack(pack_id: str):
    try:
        pack = load_pack(pack_id)
    except PackError as err:
        raise HTTPException(404, str(err)) from err
    return {"yaml": (PACKS_DIR / f"{pack_id}.yaml").read_text(),
            "pack": json.loads(pack.model_dump_json())}


class PackDraft(BaseModel):
    yaml: str


@app.post("/packs/validate")
def validate_pack(draft: PackDraft):
    """Validate a pack draft. Returns readable problems, or the parsed pack."""
    try:
        pack = parse_pack(draft.yaml, "draft.yaml")
    except PackError as err:
        lines = str(err).splitlines()
        return {"ok": False, "problems": [ln.strip(" -") for ln in lines[1:]] or lines}
    return {"ok": True, "problems": check_references(pack),
            "pack": json.loads(pack.model_dump_json())}


@app.post("/packs/try")
def try_pack(draft: PackDraft, limit: int = 60):
    """Run a draft pack over every queued order of the same item, from cached OCR."""
    from ..extract.fields import OcrFieldExtractor
    from ..extract.ocr import ocr_pdf

    try:
        pack = parse_pack(draft.yaml, "draft.yaml")
    except PackError as err:
        raise HTTPException(422, str(err)) from err
    counts = {r.value: 0 for r in Route}
    changed = []
    for oid in store.list_orders()[:limit]:
        old = store.load_decision(oid)
        if old.pack_id != pack.pack_id:
            continue
        pages, images = ocr_pdf(store.order_dir(oid) / "packet.pdf")
        extraction = OcrFieldExtractor().extract(pack, pages, images)
        new = pipeline().decide(pack, extraction, order_id=oid)
        counts[new.route.value] += 1
        if new.route != old.route:
            changed.append({"order_id": oid, "before": old.route, "after": new.route,
                            "reason": new.reasons[0] if new.reasons else ""})
    return {"counts": counts, "changed": changed}


# --- results, lookups, demo data ------------------------------------------------------------

@app.get("/eval")
def get_eval():
    p = ROOT / "reports" / "latest.json"
    if not p.exists():
        raise HTTPException(404, "No eval yet. Run `make eval`.")
    return JSONResponse(json.loads(p.read_text()))


@app.get("/npi/{npi}")
def lookup_npi(npi: str):
    """Live NPPES lookup, for the provider panel."""
    if not npi_is_valid(npi):
        return {"npi": npi, "valid_check_digit": False}
    check = NppesProviderCheck()
    try:
        raw = check.lookup(npi)
    except Exception as err:  # noqa: BLE001 - surfaced to the UI as text
        return {"npi": npi, "valid_check_digit": True, "error": str(err)}
    return {"npi": npi, "valid_check_digit": True,
            "found": raw is not None, "record": check.summarize(raw) if raw else None}


def _seed_fixtures():
    fixtures = sorted((ROOT / "data" / "fixtures").glob("*/*.pdf"))
    _seed.update(state="running", total=len(fixtures), done=0)
    for pdf in fixtures:
        truth_path = ROOT / "data" / "ground_truth" / f"{pdf.stem}.json"
        truth = json.loads(truth_path.read_text()) if truth_path.exists() else {}
        pack_id = truth.get("pack_id")
        if not pack_id or (store.order_dir(pdf.stem) / "decision.json").exists():
            _seed["done"] += 1
            continue
        try:
            pack = load_pack(pack_id)
            store.save_packet(pdf.stem, pdf)
            decision = pipeline().run(store.order_dir(pdf.stem) / "packet.pdf", pack,
                                      order_id=pdf.stem)
            store.save_decision(decision)
            store.audit("decision", pdf.stem, route=decision.route,
                        pack=f"{pack.pack_id}@{pack.version}", models=decision.models,
                        source=f"fixtures/{pdf.parent.name}/{pdf.name}")
        except Exception as err:  # noqa: BLE001 - one bad fixture shouldn't stop the seed
            store.audit("seed_error", pdf.stem, error=str(err))
        _seed["done"] += 1
    _seed["state"] = "done"


@app.post("/demo/seed")
def seed():
    if _seed["state"] != "running":
        threading.Thread(target=_seed_fixtures, daemon=True).start()
    return _seed


@app.on_event("startup")
def _autoseed():
    if os.environ.get("CLEARANCE_AUTOSEED", "1") == "1" and not store.list_orders():
        threading.Thread(target=_seed_fixtures, daemon=True).start()


# --- UI --------------------------------------------------------------------------------

app.mount("/ui", StaticFiles(directory=UI), name="ui")


@app.get("/", response_class=HTMLResponse)
def index():
    html = (UI / "index.html").read_text()
    for asset in ("app.css", "app.js"):          # cache-bust so edits show on reload
        html = html.replace(f"/ui/{asset}", f"/ui/{asset}?v={int((UI / asset).stat().st_mtime)}")
    return html


@app.get("/reports/{name}")
def report_file(name: str):
    p = (ROOT / "reports" / name).resolve()
    if p.parent != (ROOT / "reports").resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)

