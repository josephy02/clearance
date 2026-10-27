"""Re-run rules and routing on every stored order, reusing what was already read.

    uv run python -m clearance.api.redecide

For after a pack or engine change: no re-OCR, no re-extraction cost. Model checks
(llm_judgment) do run again. Reviewer decisions and the audit log are kept.
"""

from __future__ import annotations

from ..engine.pack import load_pack
from ..pipeline import Pipeline
from . import store


def main() -> None:
    pipeline = Pipeline()
    changed = 0
    for oid in store.list_orders():
        old = store.load_decision(oid)
        pack = load_pack(old.pack_id)
        new = pipeline.decide(pack, old.extraction, order_id=oid, timings=old.timings,
                              source_file=old.source_file)
        new.models = old.models | {"judge": pipeline.judge.model}
        store.save_decision(new)
        if new.route != old.route:
            changed += 1
            store.audit("decision", oid, route=new.route, pack=f"{pack.pack_id}@{pack.version}",
                        models=new.models, source="redecide")
            print(f"{oid}: {old.route.value} -> {new.route.value}")
    print(f"re-decided {len(store.list_orders())} orders; {changed} changed route")


if __name__ == "__main__":
    main()
