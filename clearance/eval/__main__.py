"""`make eval`: score every fixture suite, write reports/eval_<date>.md, a threshold
chart, and reports/latest.json (read by the review UI).

    uv run python -m clearance.eval [--suite pap] [--limit N] [--live-nppes]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date

from ..pipeline import ROOT, Pipeline
from .harness import available_suites, evaluate
from .report import render_chart, render_markdown

REPORTS = ROOT / "reports"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", action="append", help="pap, oxygen (default: all present)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--live-nppes", action="store_true",
                    help="let unknown NPIs fall through to the live NPI Registry")
    args = ap.parse_args()
    if not args.live_nppes:
        os.environ["CLEARANCE_NPPES_LIVE"] = "0"   # reproducible: synthetic roster only

    pipeline = Pipeline()
    suites = args.suite or available_suites()
    results = {}
    for suite in suites:
        print(f"evaluating {suite} ...", flush=True)
        results[suite] = evaluate(suite, pipeline, args.limit)
        m = results[suite]
        print(f"  fields {m['field_accuracy']:.1%}  routes {m['routing_accuracy']:.1%}  "
              f"defect recall {m['defect_recall']:.1%}  unsafe READY {len(m['unsafe_ready'])}")

    REPORTS.mkdir(exist_ok=True)
    today = date.today().isoformat()
    charts = {s: render_chart(m, REPORTS / f"threshold_{s}_{today}.png")
              for s, m in results.items()}
    md = render_markdown(results, charts, today)
    out = REPORTS / f"eval_{today}.md"
    out.write_text(md)
    (REPORTS / "latest.json").write_text(json.dumps(
        {"date": today, "suites": results}, indent=2, default=str))
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
