from decimal import Decimal

import pytest

from core.models import Action, ActionType, ContextPassport, GovernanceEnvelope, Policy, PolicyRule, RuleSeverity
from core.policy_engine import PolicyEngine, PolicyVersionError


def make_policy(version="1.0.0", cap=Decimal("650.00")):
    return Policy(
        version=version,
        domain="procurement",
        rules={"budget": PolicyRule(
            rule_id="budget", target_tool="*", parameter="aggregate_budget",
            operator="<=", threshold=cap, severity=RuleSeverity.CRITICAL,
        )},
    )


def test_policy_hash_is_canonical_and_excludes_issuance_time():
    first = make_policy(cap=Decimal("650.00"))
    second = make_policy(cap=650.0)
    assert first.policy_hash == second.policy_hash
    assert len(first.policy_hash) == 16
    assert make_policy(cap=Decimal("651.00")).policy_hash != first.policy_hash


def test_supplied_incorrect_hash_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        Policy(version="1.0.0", domain="procurement", rules={}, policy_hash="not-a-valid-hash")


def test_policy_engine_updates_node_cache_without_rollback():
    v1, v2, v3 = make_policy("1.0.0", Decimal("1000")), make_policy("2.0.0", Decimal("650")), make_policy("3.0.0", Decimal("500"))
    engine = PolicyEngine(v1)
    engine.register_node("node_01")
    engine.publish(v2)
    engine.update_node("node_01", v2)
    engine.publish(v3)
    engine.update_node("node_01", v3)
    engine.update_node("node_01", v2)  # A delayed old delivery is ignored.
    assert engine.resolve_node_policy("node_01").version == "3.0.0"
    with pytest.raises(PolicyVersionError):
        engine.publish(v2)


def test_decision_cache_includes_execution_history_context():
    policy = make_policy()
    engine = PolicyEngine(policy)
    action = Action(step_id=4, tool_name="reserve_hotel", arguments={"cost": Decimal("200")}, action_type=ActionType.IRREVERSIBLE)
    under_cap = engine.evaluate_action(policy, action, [{"cost": Decimal("450")}])
    over_cap = engine.evaluate_action(policy, action, [{"cost": Decimal("500")}])
    assert under_cap == []
    assert over_cap and over_cap[0]["rule_id"] == "budget"
    assert engine.decision_cache_size == 2


def test_envelope_signature_verifies_serialized_policy_context():
    policy = make_policy()
    passport = ContextPassport(agent_id="a", user_role="employee", initial_policy_version=policy.version, initial_policy_hash=policy.policy_hash)
    envelope = GovernanceEnvelope(node_id="node_01", evaluated_policy_version=policy.version, evaluated_policy_hash=policy.policy_hash, passport=passport, proposed_action=Action(step_id=1, tool_name="lookup", action_type=ActionType.REVERSIBLE))
    assert envelope.verify_signature()
    assert envelope.model_dump(mode="json")["proposed_action"]["action_type"] == "REVERSIBLE"
