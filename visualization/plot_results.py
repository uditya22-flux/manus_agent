"""Generate publication-ready safety/utility charts from benchmark CSV data."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


STRATEGY_COLORS = {
    "FAIL_CLOSED": "#64748b",
    "FAIL_OPEN": "#dc2626",
    "HITL": "#d97706",
    "COMPENSATING": "#0f766e",
}


def generate_pareto_plot(
    summary_path: str | Path = "benchmark_summary.csv",
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    source = Path(summary_path)
    if not source.is_file():
        raise FileNotFoundError(f"Benchmark summary not found: {source}. Run python -m evaluation.run_benchmarks first.")
    frame = pd.read_csv(source)
    required = {"strategy", "Task_Completion_Rate", "Governance_Violation_Rate"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Summary CSV is missing columns: {sorted(missing)}")
    out = Path(output_dir) if output_dir is not None else source.parent
    out.mkdir(parents=True, exist_ok=True)

    sns.set_theme(style="whitegrid", context="talk", font_scale=0.78)
    fig, ax = plt.subplots(figsize=(9.2, 6.8), dpi=180)
    for _, row in frame.iterrows():
        strategy = str(row["strategy"])
        completion = float(row["Task_Completion_Rate"])
        compliance = 100.0 - float(row["Governance_Violation_Rate"])
        ax.scatter(
            completion, compliance,
            s=230,
            color=STRATEGY_COLORS.get(strategy, "#2563eb"),
            label=strategy.replace("_", " "),
            edgecolor="#0f172a",
            linewidth=1.2,
            zorder=4,
        )
        offset_y = 9 if strategy == "FAIL_CLOSED" else -16 if strategy == "FAIL_OPEN" else 8
        ax.annotate(strategy.replace("_", " "), (completion, compliance), xytext=(8, offset_y), textcoords="offset points", fontsize=9, weight="bold")

    ax.set_title("Safety–Utility Trade-off Under In-Flight Policy Drift", loc="left", weight="bold", pad=18)
    ax.set_xlabel("Utility · Task completion rate (%)  ↑")
    ax.set_ylabel("Safety · Policy compliance rate (%)  ↑")
    ax.set_xlim(-5, 105)
    ax.set_ylim(-5, 105)
    ax.set_xticks(range(0, 101, 20))
    ax.set_yticks(range(0, 101, 20))
    ax.text(0.01, -0.17, "Higher and farther right is better. Fail-open can finish while exceeding the updated aggregate cap.", transform=ax.transAxes, fontsize=8.5, color="#475569")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    png = out / "pareto_frontier.png"
    pdf = out / "pareto_frontier.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    return png, pdf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", nargs="?", default="benchmark_summary.csv")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    png, pdf = generate_pareto_plot(args.summary, args.output_dir)
    print(f"Saved {png} and {pdf}")


if __name__ == "__main__":
    main()
