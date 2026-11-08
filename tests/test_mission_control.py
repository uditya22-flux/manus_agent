from decimal import Decimal

from core.models import GovernanceEnvelope, PolicyRule, RuleSeverity, TaintStatus
from simulation.mission_control import MissionControl


def make_draft(mc):
    return mc.build_policy("2.1.0", "company", [
        PolicyRule(rule_id="BUDGET", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal("350.00"), severity=RuleSeverity.CRITICAL),
        PolicyRule(rule_id="PII", target_tool="aws_s3_delete", parameter="allow_pii_export", operator="==", threshold=False, severity=RuleSeverity.CRITICAL),
    ])


def test_overview_and_fleet_expose_ten_node_adoption_and_passports():
    mc = MissionControl()
    overview = mc.overview()
    rows = mc.fleet_records()
    assert len(mc.nodes) == 10
    assert len(rows) == 6
    assert overview["cluster_policy_version"] == "2.0.0"
    assert overview["online_agents"] == 2
    assert overview["incidents_24h"] == 5
    assert overview["divergence_incidents_24h"] == 2
    assert overview["stale_nodes"] == 1
    assert overview["policy_adoption"]["2.0.0"] == 90.0
    assert {row["consistency"] for row in rows}.issuperset({"STALE-NODE", "MID-FLIGHT-CONFLICT"})
    passport = mc.passport_for("FinOps-Worker-42")
    assert passport["session_id"] == "sess_finops_worker_42"
    assert passport["taint_status"] == TaintStatus.TAINTED.value


def test_agent_controls_pause_force_update_and_terminate():
    mc = MissionControl()
    mc.pause_agent("Support-Agent-07")
    assert mc.agents["Support-Agent-07"].status == "PAUSED"
    target_node = mc.agents["FinOps-Worker-42"].node_id
    mc.force_update_agent("FinOps-Worker-42")
    assert mc.nodes[target_node]["version"] == mc.cluster_policy.version
    assert mc.agents["FinOps-Worker-42"].status == "CONFLICT_DETECTED"
    finops = next(row for row in mc.fleet_records() if row["agent_id"] == "FinOps-Worker-42")
    assert finops["consistency"] == "MID-FLIGHT-CONFLICT"
    mc.terminate_agent("Code-Review-03")
    assert mc.agents["Code-Review-03"].status == "TERMINATED"


def test_policy_diff_canary_progress_and_full_rollout():
    mc = MissionControl()
    draft = make_draft(mc)
    assert any(row["rule_id"] == "BUDGET" and row["change"] == "CHANGED" for row in mc.policy_diff(draft))
    first = mc.canary_rollout(draft, 10)
    assert first["updated_count"] == 0
    assert first["pending_count"] == 1
    assert first["progress_percent"] == 0.0
    first_advance = mc.advance_propagation(1000.0)
    assert len(first_advance["delivered"]) == 1
    assert first_advance["rollout"]["progress_percent"] == 10.0
    last = mc.canary_rollout(draft, 100)
    assert last["updated_count"] == 1
    assert last["pending_count"] == 9
    second_advance = mc.advance_propagation(1000.0)
    assert len(second_advance["delivered"]) == 9
    assert second_advance["rollout"]["progress_percent"] == 100.0
    assert mc.overview()["stale_nodes"] == 0


def test_shadow_replay_uses_five_hundred_synthetic_history_records():
    mc = MissionControl()
    report = mc.shadow_replay(make_draft(mc), limit=500)
    assert report["sample_size"] == 500
    assert report["predicted_halts"] + report["compliant_without_change"] == 500
    assert report["compensation_candidates"] <= report["predicted_halts"]
    assert report["already_over_cap_irreversible"] <= report["predicted_halts"]
    assert set(report["violations_by_rule"]) == {"BUDGET", "PII"}
    assert "synthetic" in report["history_type"]
    custom = mc.build_policy("2.1.0", "company", [
        PolicyRule(rule_id="CUSTOM", target_tool="*", parameter="record_limit", operator="<=", threshold=100, severity=RuleSeverity.MEDIUM),
    ])
    assert mc.shadow_replay(custom, 500)["unsupported_rules"] == ["CUSTOM"]


def test_hitl_actions_support_override_compensation_and_abort():
    mc = MissionControl()
    override = mc.decide_hitl("HITL-2401", "override")
    assert override["status"] == "APPROVED_OVERRIDE"
    assert mc.agents["FinOps-Worker-42"].taint_status == TaintStatus.TAINTED

    residual = mc.decide_hitl("HITL-2402", "compensate")
    assert residual["proposed_cost"] == Decimal("200.00")
    assert residual["residual_violation"] == Decimal("0.00")
    assert residual["status"] == "COMPENSATED"

    third = mc.hitl_requests[0]
    third["status"] = "PENDING"
    aborted = mc.decide_hitl("HITL-2401", "abort")
    assert aborted["status"] == "ABORTED_FROZEN"
    assert mc.agents["FinOps-Worker-42"].status == "HALTED"


def test_irreversible_prior_spend_remains_as_residual_violation_after_zero_clamp():
    mc = MissionControl()
    result = mc.decide_hitl("HITL-2401", "compensate")
    assert result["proposed_cost"] == Decimal("0.00")
    assert result["residual_violation"] == Decimal("50.00")
    assert result["status"] == "COMPENSATED_WITH_RESIDUAL_VIOLATION"
    assert "No rollback is possible" in result["decision_note"]


def test_token_estimates_and_propagation_delays_are_explicitly_modeled():
    mc = MissionControl(seed=7)
    token = mc.token_economy()
    assert token["estimated_tokens_saved"] == token["resolved_interventions"] * 1010
    assert "modeled estimate" in token["status"]
    delays = mc.propagation_samples(100)
    assert len(delays) == 100 and min(delays) >= 10.0
    latency = mc.latency_samples(250)
    assert len(latency["automated_proxy_ms"]) == 250
    assert max(latency["automated_proxy_ms"]) < 3.0
    assert sum(latency["human_escalation_ms"]) / 250 > 300.0


def test_each_agent_has_five_step_trace_and_json_verifiable_envelopes():
    mc = MissionControl()
    for agent_id in mc.agents:
        trace = mc.session_trace(agent_id)
        assert len(trace) == 5
        envelope = GovernanceEnvelope.model_validate(trace[0]["envelope"])
        assert envelope.verify_signature()
        assert trace[0]["envelope"]["passport"]["session_id"] == mc.agents[agent_id].session_id
    finops = mc.session_trace("FinOps-Worker-42")
    assert finops[3]["decision"] == "CONFLICT_DETECTED"
    assert finops[3]["action_type"] == "IRREVERSIBLE"
    assert finops[3]["policy_violations"]
