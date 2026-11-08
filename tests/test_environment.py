from decimal import Decimal

import pandas as pd

from core.models import RecoveryStrategy
from evaluation.run_benchmarks import run_benchmarks
from simulation.environment import SimulationConfig, run_simulation
from visualization.plot_results import generate_pareto_plot


def test_four_recovery_modes_preserve_expected_safety_utility_tradeoffs():
    closed = run_simulation(RecoveryStrategy.FAIL_CLOSED, seed=91)
    opened = run_simulation(RecoveryStrategy.FAIL_OPEN, seed=91)
    hitl = run_simulation(RecoveryStrategy.HITL, seed=91, config=SimulationConfig(hitl_approval_probability=1.0))
    compensated = run_simulation(RecoveryStrategy.COMPENSATING, seed=91)

    assert closed.completed == 0 and closed.steps_completed == 2
    assert closed.total_spend == 450.0 and closed.unauthorized_spend == 0.0
    assert opened.completed == 1 and opened.total_spend == 750.0
    assert opened.unauthorized_spend == 100.0 and opened.policy_violated == 1
    assert opened.passport_taint == "TAINTED"
    assert hitl.completed == 1 and hitl.policy_violated == 0
    assert hitl.recovery_latency_ms == 450.0
    assert compensated.completed == 1 and compensated.total_spend == 650.0
    assert compensated.unauthorized_spend == 0.0 and compensated.compensation_applied
    hotel = next(row for row in compensated.trace if row["step_id"] == 4)
    assert Decimal(hotel["arguments"]["cost"]) == Decimal("200.00")
    assert compensated.conflict_type == "STALE_NODE_DRIFT"


def test_update_disabled_runs_without_conflicts_or_unauthorized_spend():
    result = run_simulation(RecoveryStrategy.FAIL_OPEN, seed=8, config=SimulationConfig(inject_policy_update=False))
    assert result.completed == 1
    assert result.conflict_type == "NONE"
    assert result.unauthorized_spend == 0.0


def test_seeded_simulation_is_repeatable():
    first = run_simulation(RecoveryStrategy.COMPENSATING, seed=125)
    second = run_simulation(RecoveryStrategy.COMPENSATING, seed=125)
    assert first.to_dict() == second.to_dict()
    assert first.trace == second.trace
    assert first.topology == second.topology


def test_benchmark_csv_row_counts_and_chart_artifacts(tmp_path):
    raw, summary = run_benchmarks(trials=2, seed=10, output_dir=tmp_path)
    assert len(raw) == 8
    assert len(summary) == 4
    assert list(raw["strategy"].value_counts().values) == [2, 2, 2, 2]
    assert pd.read_csv(tmp_path / "benchmark_raw_results.csv").shape[0] == 8
    png, pdf = generate_pareto_plot(tmp_path / "benchmark_summary.csv", tmp_path)
    assert png.is_file() and png.stat().st_size > 1000
    assert pdf.is_file() and pdf.stat().st_size > 1000


def test_policy_arrival_during_action_is_detected_at_commit_boundary():
    result = run_simulation(
        RecoveryStrategy.COMPENSATING,
        seed=3,
        config=SimulationConfig(propagation_mean_ms=10.0, propagation_std_dev_ms=0.0),
    )
    step_three = next(row for row in result.trace if row["step_id"] == 3)
    assert step_three["preflight_conflict"] == "STALE_NODE_DRIFT"
    assert step_three["commit_conflict"] == "IN_FLIGHT_DIVERGENCE"
    assert step_three["executed"] is True
