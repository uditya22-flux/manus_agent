from decimal import Decimal

from core.conflict_detector import ConflictDetector
from core.models import Action, ActionType, ConflictType, ContextPassport, GovernanceEnvelope, Policy, PolicyRule


def policy(version, cap):
    return Policy(version=version, domain="procurement", rules={"R1": PolicyRule(rule_id="R1", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal(str(cap)))})


def envelope(node_policy, session_policy):
    passport = ContextPassport(agent_id="agent", user_role="employee", initial_policy_version=session_policy.version, initial_policy_hash=session_policy.policy_hash)
    return GovernanceEnvelope(node_id="node_01", evaluated_policy_version=node_policy.version, evaluated_policy_hash=node_policy.policy_hash, passport=passport, proposed_action=Action(step_id=3, tool_name="lookup", action_type=ActionType.REVERSIBLE))


def test_stale_node_drift_has_precedence_when_node_differs_from_cluster():
    v1, v2 = policy("1.0.0", 1000), policy("2.0.0", 650)
    report = ConflictDetector.inspect(envelope(v1, v1), v2)
    assert report.detected
    assert report.conflict_type == ConflictType.STALE_NODE_DRIFT


def test_in_flight_divergence_when_node_is_current_but_session_is_pinned_old():
    v1, v2 = policy("1.0.0", 1000), policy("2.0.0", 650)
    report = ConflictDetector.inspect(envelope(v2, v1), v2)
    assert report.detected
    assert report.conflict_type == ConflictType.IN_FLIGHT_DIVERGENCE


def test_consistent_session_node_and_cluster_has_no_conflict():
    current = policy("2.0.0", 650)
    report = ConflictDetector.inspect(envelope(current, current), current)
    assert not report.detected
    assert report.conflict_type == ConflictType.NONE


def test_same_version_different_hash_is_stale_node_drift():
    current = policy("2.0.0", 650)
    equivocating = policy("2.0.0", 700)
    report = ConflictDetector.inspect(envelope(equivocating, equivocating), current)
    assert report.conflict_type == ConflictType.STALE_NODE_DRIFT
