"""File-backed order store and append-only audit log.

var/orders/<id>/packet.pdf, decision.json, review.json, pages/<n>.png
var/audit.jsonl: one line per event (decision, field edit, approve, override)
data/overrides/<id>.json: reviewer overrides, kept as future eval cases
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from ..pipeline import ROOT, Decision
from ..synth.fax import rasterize

VAR = ROOT / "var"
ORDERS = VAR / "orders"
AUDIT = VAR / "audit.jsonl"
OVERRIDES = ROOT / "data" / "overrides"
_lock = threading.Lock()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def order_dir(order_id: str) -> Path:
    if not order_id.replace("-", "").replace("_", "").isalnum():
        raise KeyError(order_id)
    return ORDERS / order_id


def save_packet(order_id: str, pdf: Path | bytes) -> Path:
    d = order_dir(order_id)
    (d / "pages").mkdir(parents=True, exist_ok=True)
    dest = d / "packet.pdf"
    if isinstance(pdf, bytes):
        dest.write_bytes(pdf)
    else:
        shutil.copyfile(pdf, dest)
    for i, img in enumerate(rasterize(dest, dpi=110)):
        img.save(d / "pages" / f"{i + 1}.png", optimize=True)
    return dest


def save_decision(decision: Decision, received_at: str | None = None) -> None:
    d = order_dir(decision.order_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"received_at": received_at or load_meta(decision.order_id).get("received_at") or now()}
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "decision.json").write_text(decision.model_dump_json(indent=1))


def load_meta(order_id: str) -> dict:
    p = order_dir(order_id) / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_decision(order_id: str) -> Decision:
    p = order_dir(order_id) / "decision.json"
    if not p.exists():
        raise KeyError(order_id)
    return Decision.model_validate_json(p.read_text())


def load_review(order_id: str) -> dict | None:
    p = order_dir(order_id) / "review.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_review(order_id: str, review: dict) -> None:
    (order_dir(order_id) / "review.json").write_text(json.dumps(review, indent=1))


def page_count(order_id: str) -> int:
    return len(list((order_dir(order_id) / "pages").glob("*.png")))


def list_orders() -> list[str]:
    if not ORDERS.exists():
        return []
    return sorted(p.name for p in ORDERS.iterdir() if (p / "decision.json").exists())


def audit(event: str, order_id: str, **data) -> dict:
    entry = {"at": now(), "event": event, "order_id": order_id, **data}
    with _lock:
        VAR.mkdir(exist_ok=True)
        with AUDIT.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    return entry


def audit_for(order_id: str) -> list[dict]:
    if not AUDIT.exists():
        return []
    out = []
    for line in AUDIT.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("order_id") == order_id:
            out.append(entry)
    return out


def save_override_case(order_id: str, case: dict) -> Path:
    OVERRIDES.mkdir(parents=True, exist_ok=True)
    p = OVERRIDES / f"{order_id}.json"
    p.write_text(json.dumps(case, indent=2, default=str))
    return p
