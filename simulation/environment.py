"""Discrete-event orchestration for policy pushes, action interception, and recovery."""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Dict, List

from core.interceptor import RuntimeInterceptor
from core.conflict_detector import ConflictDetector
from core.models import ConflictType, GovernanceEnvelope, Policy, RecoveryStrategy
from core.policy_engine import PolicyEngine, as_decimal
from core.recovery import RecoveryExecutor
from evaluation.scenarios import get_corporate_procurement_scenario
from simulation.agent import AutonomousAgent
from simulation.network import GovernanceNetwork


@dataclass(frozen=True)
class SimulationConfig:
    node_count: int = 10
    inject_policy_update: bool = True
    update_before_step: int = 3
    propagation_mean_ms: float = 180.0
    propagation_std_dev_ms: float = 40.0
    hitl_approval_probability: float = 0.92


@dataclass
class SimulationResult:
    strategy: str
    seed: int
    completed: int
    steps_completed: int
    total_spend: float
    policy_cap: float
    unauthorized_spend: float
    policy_violated: int
    recovery_latency_ms: float
    interceptor_overhead_ms: float
    audit_events: int
    conflict_type: str
    compensation_applied: bool
    final_policy_version: str
    passport_taint: str
    trace: List[Dict[str, Any]]
    topology: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value.pop("trace")
        value.pop("topology")
        return value


def _json_model(model: GovernanceEnvelope) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _decimal_float(value: Any) -> float:
    return float(value if isinstance(value, Decimal) else Decimal(str(value)))


def run_simulation(
    strategy: RecoveryStrategy | str,
    *,
    seed: int = 0,
    config: SimulationConfig | None = None,
) -> SimulationResult:
    """Run one isolated trial using only seeded random sources and virtual milliseconds."""
    if isinstance(strategy, str):
        strategy = RecoveryStrategy(strategy)
    config = config or SimulationConfig()
    if config.node_count < 1:
        raise ValueError("node_count must be at least 1")
    if not 0.0 <= config.hitl_approval_probability <= 1.0:
        raise ValueError("hitl_approval_probability must be in [0, 1]")

    policy_v1, policy_v2, scenario_plan = get_corporate_procurement_scenario()
    policy_engine = PolicyEngine(policy_v1)
    network_rng = random.Random(seed)
    hitl_rng = random.Random(seed ^ 0xA6B71D)
    network = GovernanceNetwork(config.node_count, policy_v1, rng=network_rng)
    agent = AutonomousAgent("agent_alpha", "employee", policy_v1, scenario_plan, session_id=f"sess_{abs(seed):08x}_{strategy.value.lower()}")

    sim_time_ms = 0.0
    recovery_latency_ms = 0.0
    interceptor_overhead_ms = 0.0
    audit_events = 0
    compensation_applied = False
    conflict_type = ConflictType.NONE
    recovery_resolved = False
    effective_policy: Policy = policy_v1
    pushed = False
    trace: List[Dict[str, Any]] = []

    for index in range(len(agent.plan)):
        action = agent.action_at(index)
        if config.inject_policy_update and not pushed and action.step_id == config.update_before_step:
            policy_engine.publish(policy_v2)
            network.broadcast_update(
                policy_v2,
                current_time_ms=sim_time_ms,
                mean_delay_ms=config.propagation_mean_ms,
                std_dev_ms=config.propagation_std_dev_ms,
            )
            pushed = True

        network.advance_to(sim_time_ms)
        node = network.select_node()
        intercepted = RuntimeInterceptor.inspect(
            action=action,
            node_id=node.node_id,
            node_policy=node.cached_policy,
            cluster_policy=network.cluster_policy,
            passport=agent.passport,
            policy_engine=policy_engine,
            sim_time_ms=sim_time_ms,
        )
        interceptor_overhead_ms += intercepted.overhead_ms
        report = intercepted.conflict
        recovery_message = ""
        recovery_telemetry: Dict[str, Any] = {}
        recovery_applied_here = False

        # The first detected conflict chooses the session's recovery semantics. Subsequent
        # observations remain visible in the trace but do not repeatedly prompt or re-charge.
        if report.detected and conflict_type == ConflictType.NONE:
            conflict_type = report.conflict_type
        if report.detected and not recovery_resolved:
            recovery_applied_here = True
            allowed, recovery_message, revised, recovery_telemetry = RecoveryExecutor.execute(
                strategy,
                action,
                agent.plan[index + 1 :],
                network.cluster_policy,
                agent.passport,
                rng=hitl_rng,
                hitl_approval_probability=config.hitl_approval_probability,
            )
            recovery_resolved = True
            recovery_latency_ms += float(recovery_telemetry.get("recovery_cost_ms", 0.0))
            audit_events += int(bool(recovery_telemetry.get("violation_logged", False)))
            compensation_applied = compensation_applied or bool(recovery_telemetry.get("compensation_applied", False))
            if strategy == RecoveryStrategy.FAIL_OPEN:
                effective_policy = policy_v1  # Deliberate session-level legacy pin; tainted and audited.
            else:
                effective_policy = network.cluster_policy
            if len(revised) == len(agent.plan) - index - 1:
                agent.replace_remaining_plan(index + 1, revised)

        def append_aborted(decision: str, commit_envelope: GovernanceEnvelope | None = None, commit_report: Any = None) -> None:
            trace.append({
                "step_id": action.step_id,
                "tool": action.tool_name,
                "action_type": action.action_type.value,
                "node_id": node.node_id,
                "sim_time_ms": round(sim_time_ms, 3),
                "node_policy_version": node.cached_policy.version,
                "cluster_policy_version": network.cluster_policy.version,
                "preflight_conflict": report.conflict_type.value,
                "commit_conflict": commit_report.conflict_type.value if commit_report else ConflictType.NONE.value,
                "decision": decision,
                "recovery_message": recovery_message,
                "recovery_applied": recovery_applied_here,
                "policy_violations": intercepted.policy_violations,
                "arguments": json.loads(json.dumps(action.model_dump(mode="json"), default=str))["arguments"],
                "executed": False,
                "envelope": json.loads(_json_model(intercepted.envelope)),
                "commit_envelope": json.loads(_json_model(commit_envelope)) if commit_envelope else None,
                "taint_status": agent.passport.taint_status.value,
            })

        if recovery_applied_here and not recovery_telemetry.get("operator_approved", True) and strategy == RecoveryStrategy.HITL:
            append_aborted("DENIED_HITL")
            break
        if recovery_applied_here and strategy == RecoveryStrategy.FAIL_CLOSED:
            append_aborted("ABORTED_FAIL_CLOSED")
            break
        if recovery_applied_here and not recovery_telemetry.get("violation_logged", False) and recovery_message.startswith("ABORTED_COMPENSATION_IMPOSSIBLE"):
            append_aborted("ABORTED_COMPENSATION_IMPOSSIBLE")
            break

        # Check the currently selected enforcement policy before a tool can proceed.
        preflight_violations = policy_engine.evaluate_action(effective_policy, action, agent.passport.execution_history)
        if preflight_violations:
            append_aborted("BLOCKED_POLICY_VIOLATION")
            break

        # Simulated operation latency; network events continue while the action is in flight.
        sim_time_ms += action.duration_ms
        network.advance_to(sim_time_ms)
        commit_node = network.nodes[node.node_id]
        commit_envelope = GovernanceEnvelope(
            envelope_id=f"env_{agent.passport.session_id}_{action.step_id:02d}_{node.node_id}_commit_{round(sim_time_ms * 1000)}",
            timestamp=sim_time_ms / 1000.0,
            node_id=commit_node.node_id,
            evaluated_policy_version=commit_node.cached_policy.version,
            evaluated_policy_hash=commit_node.cached_policy.policy_hash,
            passport=agent.passport.model_copy(deep=True),
            proposed_action=action.model_copy(deep=True),
        )
        commit_report = ConflictDetector.inspect(commit_envelope, network.cluster_policy)
        if commit_report.detected and conflict_type == ConflictType.NONE:
            conflict_type = commit_report.conflict_type
        if commit_report.detected and not recovery_resolved:
            # A policy update that lands after authorization but before commit is still
            # intercepted before the mock endpoint's irreversible side effect.
            recovery_applied_here = True
            allowed, recovery_message, revised, recovery_telemetry = RecoveryExecutor.execute(
                strategy,
                action,
                agent.plan[index + 1 :],
                network.cluster_policy,
                agent.passport,
                rng=hitl_rng,
                hitl_approval_probability=config.hitl_approval_probability,
            )
            recovery_resolved = True
            recovery_latency_ms += float(recovery_telemetry.get("recovery_cost_ms", 0.0))
            audit_events += int(bool(recovery_telemetry.get("violation_logged", False)))
            compensation_applied = compensation_applied or bool(recovery_telemetry.get("compensation_applied", False))
            effective_policy = policy_v1 if strategy == RecoveryStrategy.FAIL_OPEN else network.cluster_policy
            if len(revised) == len(agent.plan) - index - 1:
                agent.replace_remaining_plan(index + 1, revised)
            if not allowed:
                append_aborted("ABORTED_AT_COMMIT_BOUNDARY", commit_envelope, commit_report)
                break

        commit_violations = policy_engine.evaluate_action(effective_policy, action, agent.passport.execution_history)
        if commit_violations:
            # No rollback is attempted: the mock external effect has not yet committed.
            intercepted.policy_violations.extend(commit_violations)
            append_aborted("BLOCKED_AT_COMMIT_BOUNDARY", commit_envelope, commit_report)
            break

        receipt = agent.commit_action(index)
        trace.append({
            "step_id": action.step_id,
            "tool": action.tool_name,
            "action_type": action.action_type.value,
            "node_id": node.node_id,
            "sim_time_ms": round(sim_time_ms, 3),
            "node_policy_version": node.cached_policy.version,
            "cluster_policy_version": network.cluster_policy.version,
            "preflight_conflict": report.conflict_type.value,
            "commit_conflict": commit_report.conflict_type.value,
            "decision": "COMMITTED",
            "recovery_message": recovery_message,
            "recovery_applied": recovery_applied_here,
            "policy_violations": intercepted.policy_violations,
            "arguments": json.loads(json.dumps(action.model_dump(mode="json"), default=str))["arguments"],
            "executed": True,
            "receipt": receipt,
            "envelope": json.loads(_json_model(intercepted.envelope)),
            "commit_envelope": json.loads(_json_model(commit_envelope)),
            "taint_status": agent.passport.taint_status.value,
        })

    total_spend_decimal = sum((as_decimal(entry.get("cost", 0)) for entry in agent.passport.execution_history), Decimal("0"))
    final_policy = network.cluster_policy
    cap_rule = next((rule for rule in final_policy.rules.values() if rule.parameter == "aggregate_budget"), None)
    cap = as_decimal(cap_rule.threshold) if cap_rule else Decimal("0")
    unauthorized = max(Decimal("0"), total_spend_decimal - cap) if config.inject_policy_update and pushed else Decimal("0")

    return SimulationResult(
        strategy=strategy.value,
        seed=seed,
        completed=int(agent.completed_steps == len(scenario_plan)),
        steps_completed=agent.completed_steps,
        total_spend=_decimal_float(total_spend_decimal),
        policy_cap=_decimal_float(cap),
        unauthorized_spend=_decimal_float(unauthorized),
        policy_violated=int(unauthorized > 0),
        recovery_latency_ms=recovery_latency_ms,
        interceptor_overhead_ms=interceptor_overhead_ms,
        audit_events=audit_events,
        conflict_type=conflict_type.value,
        compensation_applied=compensation_applied,
        final_policy_version=final_policy.version,
        passport_taint=agent.passport.taint_status.value,
        trace=trace,
        topology=network.topology(),
    )


# Public alias retained for callers that prefer the longer descriptive name.
simulate = run_simulation
