"""Metrics aggregation for Monte Carlo benchmark output."""
from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd


RAW_COLUMNS = [
    "trial", "seed", "strategy", "completed", "steps_completed", "policy_violated",
    "unauthorized_spend", "total_spend", "policy_cap", "recovery_latency_ms",
    "interceptor_overhead_ms", "audit_events", "conflict_type", "compensation_applied",
    "final_policy_version", "passport_taint",
]


def summarize_results(results: Iterable[Mapping]) -> pd.DataFrame:
    frame = pd.DataFrame(list(results))
    if frame.empty:
        return pd.DataFrame(columns=[
            "strategy", "Trials", "Task_Completion_Rate", "Governance_Violation_Rate",
            "Mean_Unauthorized_Cost", "Mean_Recovery_Latency_ms", "Mean_Total_Spend",
        ])
    summary = frame.groupby("strategy", sort=True).agg(
        Trials=("completed", "count"),
        Task_Completion_Rate=("completed", "mean"),
        Governance_Violation_Rate=("policy_violated", "mean"),
        Mean_Unauthorized_Cost=("unauthorized_spend", "mean"),
        Mean_Recovery_Latency_ms=("recovery_latency_ms", "mean"),
        Mean_Total_Spend=("total_spend", "mean"),
    ).reset_index()
    summary["Task_Completion_Rate"] *= 100.0
    summary["Governance_Violation_Rate"] *= 100.0
    return summary
