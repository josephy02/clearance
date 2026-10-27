"""Build docs/implementation_kit.{md,html,pdf} and docs/deck.html from the latest eval.

    uv run python docs/build.py

Measured numbers come from reports/latest_claude.json (falling back to offline), so the
kit never drifts from the eval. The PDF is printed with headless Chrome when available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

ORDERS, T_BEFORE, T_READY, T_REVIEW, FTE_HOURS = 1500, 18.0, 1.5, 6.0, 140
COLD_OCR_S = 9      # measured: two-pass OCR on a 5-6 page packet, laptop CPU (eval caches OCR)


def load_eval() -> dict:
    for name in ("latest_claude.json", "latest_offline.json", "latest.json"):
        p = ROOT / "reports" / name
        if p.exists():
            return json.loads(p.read_text())
    raise SystemExit("No eval results. Run `make eval` first.")


def numbers() -> dict:
    data = load_eval()
    pap = data["suites"]["pap"]
    at = next(s for s in pap["sweep"] if abs(s["threshold"] - pap["threshold"]) < 1e-9)
    a = at["auto_rate"]
    t_after = a * T_READY + (1 - a) * T_REVIEW
    saved = ORDERS * (T_BEFORE - t_after)
    defects = Counter(d for p in (ROOT / "data" / "ground_truth").glob("pap-*.json")
                      for d in json.loads(p.read_text())["defects"])
    n = pap["orders"]

    def share(*keys):
        return f"{100 * sum(defects[k] for k in keys) / n:.0f}%"

    return {
        "EVAL_DATE": data["date"], "THRESHOLD": f"{pap['threshold']:.2f}",
        "AUTO_RATE": f"{100 * a:.0f}%",
        "LATENCY": f"about {pap['latency_p50_s'] + COLD_OCR_S:.0f} seconds",
        "COST": f"${pap['cost_per_order_usd']:.3f}", "T_AFTER": f"{t_after:.1f}",
        "MIN_SAVED": f"{saved:,.0f}", "HOURS": f"{saved / 60:,.0f}",
        "FTES": f"{saved / 60 / FTE_HOURS:.1f}",
        "MONTHLY_COST": f"${ORDERS * pap['cost_per_order_usd']:,.0f}",
        "RECALL": f"{100 * pap['defect_recall']:.0f}%",
        "UNSIGNED": share("unsigned_order"), "F2F": share("f2f_after_test"),
        "MISSING_STUDY": share("missing_sleep_study"), "MILD": share("mild_no_symptom"),
        "NPI": share("invalid_npi", "inactive_npi"), "ELIG": share("eligibility_inactive"),
        "ILLEGIBLE": share("illegible_ahi"),
        "UNSAFE": str(sum(len(s["unsafe_ready"]) for s in data["suites"].values())),
        "FIELDS": f"{100 * min(s['field_accuracy'] for s in data['suites'].values()):.0f}%",
    }


CSS = """
body { font: 10.5pt/1.5 "Public Sans", -apple-system, "Segoe UI", sans-serif; color: #1b2230;
       max-width: 46em; margin: 0 auto; padding: 28px 32px; }
h1 { font-size: 19pt; margin: 0 0 4px; } h2 { font-size: 13pt; margin: 22px 0 6px;
     page-break-before: always; } h2:first-of-type { page-break-before: auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; }
th, td { border-bottom: 1px solid #d8dde4; padding: 4px 6px; text-align: left;
         vertical-align: top; }
pre { background: #f6f8fa; padding: 10px; font-size: 6.6pt; line-height: 1.35; overflow: hidden; }
code { font-size: 9pt; } hr { display: none; } em { color: #5b6574; }
"""


def main() -> None:
    env = Environment(loader=FileSystemLoader(DOCS / "templates"), variable_start_string="{{",
                      variable_end_string="}}", keep_trailing_newline=True)
    nums = numbers()
    (DOCS / "deck.html").write_text(env.get_template("deck.html.j2").render(**nums))
    md = env.get_template("implementation_kit.md.j2").render(**nums)
    (DOCS / "implementation_kit.md").write_text(md)
    body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>Implementation kit</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")
    out_html = DOCS / "implementation_kit.html"
    out_html.write_text(html)
    chrome = CHROME if Path(CHROME).exists() else shutil.which("chromium") or shutil.which(
        "google-chrome")
    if chrome:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={DOCS / 'implementation_kit.pdf'}", out_html.as_uri()],
                       check=True, capture_output=True, timeout=120)
        print("wrote docs/implementation_kit.{md,html,pdf}")
    else:
        print("wrote docs/implementation_kit.{md,html} (no Chrome found for the PDF)")


if __name__ == "__main__":
    main()
