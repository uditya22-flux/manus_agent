import random
from decimal import Decimal

from core.models import Action, ActionType, ContextPassport, Policy, PolicyRule, RecoveryStrategy, TaintStatus
from core.recovery import RecoveryExecutor


def policies():
    old = Policy(version="1.0.0", domain="procurement", rules={"R1": PolicyRule(rule_id="R1", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal("1000.00"))})
    new = Policy(version="2.0.0", domain="procurement", rules={"R1": PolicyRule(rule_id="R1", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal("650.00"))})
    return old, new


def state(old):
    passport = ContextPassport(agent_id="agent", user_role="employee", initial_policy_version=old.version, initial_policy_hash=old.policy_hash)
    passport.execution_history.append({"step_id": 2, "cost": Decimal("450.00")})
    action = Action(step_id=3, tool_name="check_hotel", action_type=ActionType.REVERSIBLE)
    future = [
        Action(step_id=4, tool_name="reserve_hotel", arguments={"cost": Decimal("300.00")}, action_type=ActionType.IRREVERSIBLE),
        Action(step_id=5, tool_name="send_invites", action_type=ActionType.IRREVERSIBLE),
    ]
    return passport, action, future


def test_fail_closed_halts_without_taint_or_violation():
    old, new = policies()
    passport, action, future = state(old)
    allowed, message, plan, telemetry = RecoveryExecutor.execute(RecoveryStrategy.FAIL_CLOSED, action, future, new, passport)
    assert not allowed and message.startswith("ABORTED_FAIL_CLOSED")
    assert plan == future and telemetry["violation_logged"] is False
    assert passport.taint_status == TaintStatus.CLEAN


def test_fail_open_pins_legacy_and_taints_context():
    old, new = policies()
    passport, action, future = state(old)
    allowed, message, _, telemetry = RecoveryExecutor.execute(RecoveryStrategy.FAIL_OPEN, action, future, new, passport)
    assert allowed and message.startswith("ALLOWED_LEGACY_PINNED")
    assert telemetry["violation_logged"] is True
    assert passport.taint_status == TaintStatus.TAINTED


def test_hitl_approval_clamps_plan_and_denial_fails_closed():
    old, new = policies()
    passport, action, future = state(old)
    allowed, message, plan, telemetry = RecoveryExecutor.execute(RecoveryStrategy.HITL, action, future, new, passport, rng=random.Random(0), hitl_approval_probability=1.0)
    assert allowed and message.startswith("ALLOWED_HITL")
    assert telemetry["recovery_cost_ms"] == 450.0
    assert plan[0].arguments["cost"] == Decimal("200.00")
    assert Decimal("450.00") + plan[0].arguments["cost"] == Decimal("650.00")

    passport, action, future = state(old)
    denied, message, _, telemetry = RecoveryExecutor.execute(RecoveryStrategy.HITL, action, future, new, passport, rng=random.Random(0), hitl_approval_probability=0.0)
    assert not denied and message.startswith("DENIED_HITL")
    assert telemetry["operator_approved"] is False


def test_compensation_shares_budget_across_all_future_costs():
    old, new = policies()
    passport, action, future = state(old)
    future.append(Action(step_id=6, tool_name="second_purchase", arguments={"cost": Decimal("100.00")}, action_type=ActionType.IRREVERSIBLE))
    allowed, message, plan, telemetry = RecoveryExecutor.execute(RecoveryStrategy.COMPENSATING, action, future, new, passport)
    assert allowed and message.startswith("ALLOWED_COMPENSATED")
    assert plan[0].arguments["cost"] == Decimal("200.00")
    assert plan[2].arguments["cost"] == Decimal("0.00")
    assert sum((item.arguments.get("cost", Decimal("0")) for item in plan), Decimal("0")) == Decimal("200.00")
    assert telemetry["compensation_applied"] is True


def test_taint_cannot_be_cleared():
    old, _ = policies()
    passport, _, _ = state(old)
    passport.mark_tainted()
    try:
        passport.taint_status = TaintStatus.CLEAN
    except ValueError:
        pass
    else:
        raise AssertionError("Taint must be monotonic")
