"""Render eval metrics as markdown and the automation-vs-error threshold chart."""

from __future__ import annotations

from pathlib import Path

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def render_chart(m: dict, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = [s["threshold"] for s in m["sweep"]]
    auto = [100 * s["auto_rate"] for s in m["sweep"]]
    err = [100 * s["error_rate"] for s in m["sweep"]]
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.plot(xs, auto, color=BLUE, lw=2, marker="o", ms=4, label="Auto-routed READY (% of orders)")
    ax.plot(xs, err, color=ORANGE, lw=2, marker="o", ms=4,
            label="Wrong among auto-routed (% of READY)")
    ax.axvline(m["threshold"], color=MUTED, lw=1, ls="--")
    ax.text(m["threshold"], 90, f" pack bar {m['threshold']:.2f}", color=MUTED, fontsize=8,
            va="center", ha="left")
    ax.annotate(f"{auto[-1]:.0f}%", (xs[-1], auto[-1]), xytext=(6, 0),
                textcoords="offset points", color=INK, fontsize=8, va="center")
    ax.annotate(f"{auto[0]:.0f}%", (xs[0], auto[0]), xytext=(-6, 0), ha="right",
                textcoords="offset points", color=INK, fontsize=8, va="center")
    ax.set_ylim(-2, 100)
    ax.set_xlabel("auto_ready_min_confidence", color=MUTED, fontsize=9)
    ax.set_ylabel("%", color=MUTED, fontsize=9)
    ax.set_title(f"{m['pack_id']}: automation vs. error ({m['orders']} synthetic orders)",
                 color=INK, fontsize=10, loc="left", pad=12)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2),
              ncol=2, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render_markdown(results: dict, charts: dict, today: str) -> str:
    lines = [f"# Evaluation report — {today}", "",
             "All orders are synthetic (`SYNTHETIC – TEST DATA`). Expected outcomes come from "
             "planted defects in the generator, not from the engine.", ""]
    for suite, m in results.items():
        lines += [
            f"## {m['pack_id']} v{m['pack_version']} ({m['orders']} orders)", "",
            f"Models: extractor `{m['models'].get('extractor')}`, judge "
            f"`{m['models'].get('judge')}`. Auto-ready threshold {m['threshold']:.2f}.", "",
            "| Metric | Value |", "|---|---|",
            f"| Field extraction accuracy | {_pct(m['field_accuracy'])} |",
            *[f"| … on {lvl} faxes | {_pct(v)} |"
              for lvl, v in m["field_accuracy_by_level"].items()],
            f"| Page classification accuracy | {_pct(m['page_classification_accuracy'])} |",
            f"| Defect recall (all rules) | {_pct(m['defect_recall'])} |",
            f"| Routing accuracy | {_pct(m['routing_accuracy'])} |",
            f"| Orders wrongly auto-routed READY | {len(m['unsafe_ready'])} "
            f"{'(' + ', '.join(m['unsafe_ready']) + ')' if m['unsafe_ready'] else ''} |",
            f"| LLM-judgment quotes found verbatim | {_pct(m['quote_validity'])} "
            f"({m['quotes']} quotes) |",
            f"| Latency p50 / p95 | {m['latency_p50_s']:.2f}s / {m['latency_p95_s']:.2f}s |",
            f"| Model cost per order | ${m['cost_per_order_usd']:.4f} |", "",
            "### Per-rule precision / recall (positive = rule should flag)", "",
            "| Rule | TP | FP | FN | Precision | Recall |", "|---|---|---|---|---|---|",
            *[f"| `{rid}` | {s['tp']} | {s['fp']} | {s['fn']} | {_pct(s['precision'])} | "
              f"{_pct(s['recall'])} |" for rid, s in m["rules"].items()],
            "", "### Routing confusion matrix (rows = expected)", "",
            "| expected \\ got | ready | needs_info | human_review |", "|---|---|---|---|",
            *[f"| {w} | " + " | ".join(str(m["confusion"][w][g])
                                       for g in ("ready", "needs_info", "human_review")) + " |"
              for w in ("ready", "needs_info", "human_review")],
            "", "### Field accuracy by field", "",
            "| Field | Accuracy |", "|---|---|",
            *[f"| `{k}` | {_pct(v)} |" for k, v in m["field_accuracy_by_field"].items()],
            "", "### Automation rate vs. threshold", "",
            f"![threshold sweep]({charts[suite].name})", "",
            "| Threshold | Auto-routed | Wrong among auto-routed | READY recall |",
            "|---|---|---|---|",
            *[f"| {s['threshold']:.2f} | {_pct(s['auto_rate'])} | {_pct(s['error_rate'])} | "
              f"{_pct(s['ready_recall'])} |" for s in m["sweep"]],
            "", "### Misses", "",
            *[f"- `{o['order_id']}` ({o['degradation']}, defects: "
              f"{', '.join(o['defects']) or 'none'}): expected **{o['want']}**, got "
              f"**{o['got']}**"
              + (f"; field errors: {', '.join(o['field_errors'])}" if o["field_errors"] else "")
              + ("; rule mismatches: " + ", ".join(
                  f"{x['rule']} {x['want']}→{x['got']}" for x in o["rule_mismatches"])
                 if o["rule_mismatches"] else "")
              for o in m["per_order"] if o["want"] != o["got"] or o["rule_mismatches"]],
            "",
        ]
    return "\n".join(lines)
