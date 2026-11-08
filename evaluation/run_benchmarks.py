"""Run the AgentBridge comparative Monte Carlo benchmark."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from core.models import RecoveryStrategy
from evaluation.metrics import RAW_COLUMNS, summarize_results
from simulation.environment import SimulationConfig, run_simulation


STRATEGIES = [
    RecoveryStrategy.FAIL_CLOSED,
    RecoveryStrategy.FAIL_OPEN,
    RecoveryStrategy.HITL,
    RecoveryStrategy.COMPENSATING,
]


def run_benchmarks(trials: int = 100, seed: int = 20261108, output_dir: str | Path = ".") -> tuple[pd.DataFrame, pd.DataFrame]:
    if trials < 1:
        raise ValueError("trials must be at least 1")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    for strategy in STRATEGIES:
        for trial in range(trials):
            trial_seed = seed + trial  # Common random numbers across strategies.
            result = run_simulation(strategy, seed=trial_seed, config=SimulationConfig())
            row = {"trial": trial + 1, **result.to_dict()}
            rows.append(row)
    raw = pd.DataFrame(rows)
    raw = raw.reindex(columns=RAW_COLUMNS)
    summary = summarize_results(rows)
    raw.to_csv(target / "benchmark_raw_results.csv", index=False, float_format="%.6f")
    summary.to_csv(target / "benchmark_summary.csv", index=False, float_format="%.6f")
    return raw, summary


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=100, help="Trials per strategy (default: 100)")
    parser.add_argument("--seed", type=int, default=20261108, help="Master seed; trial seeds are consecutive")
    parser.add_argument("--output-dir", default=".", help="Directory for CSV outputs")
    args = parser.parse_args(argv)
    print(f"Running {args.trials} trials per strategy ({args.trials * len(STRATEGIES)} total) with seed {args.seed}...")
    _, summary = run_benchmarks(args.trials, args.seed, args.output_dir)
    display = summary.copy()
    display["Task_Completion_Rate"] = display["Task_Completion_Rate"].map(lambda value: f"{value:.1f}%")
    display["Governance_Violation_Rate"] = display["Governance_Violation_Rate"].map(lambda value: f"{value:.1f}%")
    print("\n--- BENCHMARK SUMMARY ---")
    print(display.to_string(index=False))
    print(f"\nWrote {Path(args.output_dir) / 'benchmark_raw_results.csv'}")
    print(f"Wrote {Path(args.output_dir) / 'benchmark_summary.csv'}")


if __name__ == "__main__":
    main()
