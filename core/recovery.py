"""Fail-safe, legacy, human-reviewed, and compensating recovery handlers."""
from __future__ import annotations

import random
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Tuple

from .models import Action, ContextPassport, Policy, RecoveryStrategy
from .policy_engine import as_decimal


class RecoveryExecutor:
    """Apply a recovery decision and return the executable downstream plan."""

    @staticmethod
    def fail_closed(remaining_plan: List[Action]) -> Tuple[bool, str, List[Action], Dict[str, Any]]:
        return False, "ABORTED_FAIL_CLOSED: Execution halted on policy-version conflict.", remaining_plan, {
            "recovery_cost_ms": 0.5,
            "violation_logged": False,
            "compensation_applied": False,
        }

    @staticmethod
    def fail_open(remaining_plan: List[Action], passport: ContextPassport) -> Tuple[bool, str, List[Action], Dict[str, Any]]:
        # Taint is monotonic: continuation on legacy policy is never represented as trusted.
        passport.mark_tainted()
        return True, "ALLOWED_LEGACY_PINNED: Continue on session policy; record ex-post audit.", remaining_plan, {
            "recovery_cost_ms": 0.2,
            "violation_logged": True,
            "compensation_applied": False,
        }

    @staticmethod
    def _budget_rule(policy: Policy):
        for rule in policy.rules.values():
            if rule.parameter == "aggregate_budget" and rule.operator in {"<", "<="}:
                return rule
        return None

    @classmethod
    def _clamp_future_plan(
        cls,
        remaining_plan: List[Action],
        active_policy: Policy,
        passport: ContextPassport,
        current_action: Action,
    ) -> Tuple[bool, List[Action], Decimal | None, str]:
        rule = cls._budget_rule(active_policy)
        mutated = [action.model_copy(deep=True) for action in remaining_plan]
        if rule is None:
            return False, mutated, None, "No aggregate budget rule is present."

        cap = as_decimal(rule.threshold)
        already_committed = sum((as_decimal(entry.get("cost", 0)) for entry in passport.execution_history), Decimal("0"))
        current_cost = as_decimal(current_action.arguments.get("cost", 0))
        available = cap - already_committed - current_cost
        if available < 0:
            return False, mutated, available, "Committed and current-action spend already exceeds the active cap."

        changed = False
        for future in mutated:
            if "cost" not in future.arguments:
                continue
            proposed = as_decimal(future.arguments["cost"])
            allowed = min(proposed, available)
            # Currency values are allocated at cent precision and never rounded upward.
            allowed = allowed.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            if allowed < proposed:
                future.arguments["cost"] = allowed
                future.arguments["compensation_note"] = f"Clamped to remaining aggregate budget under policy {active_policy.version}."
                changed = True
            available -= allowed
            if available <= 0:
                available = Decimal("0")
        return changed, mutated, available, "Future costs allocated against one shared remaining-budget balance."

    @classmethod
    def hitl(
        cls,
        action: Action,
        remaining_plan: List[Action],
        active_policy: Policy,
        passport: ContextPassport,
        rng: random.Random,
        approval_probability: float = 0.92,
    ) -> Tuple[bool, str, List[Action], Dict[str, Any]]:
        if not 0.0 <= approval_probability <= 1.0:
            raise ValueError("approval_probability must be in [0, 1]")
        approved = rng.random() < approval_probability
        if not approved:
            return False, "DENIED_HITL: Operator rejected or did not approve the revised plan; fail closed.", remaining_plan, {
                "recovery_cost_ms": 450.0,
                "violation_logged": False,
                "compensation_applied": False,
                "operator_approved": False,
            }

        changed, mutated, available, _ = cls._clamp_future_plan(remaining_plan, active_policy, passport, action)
        if available is not None and available < 0:
            return False, "DENIED_HITL: Current committed spend cannot be made compliant; fail closed.", remaining_plan, {
                "recovery_cost_ms": 450.0,
                "violation_logged": False,
                "compensation_applied": False,
                "operator_approved": True,
            }
        return True, "ALLOWED_HITL: Operator approved a compliant revised plan.", mutated, {
            "recovery_cost_ms": 450.0,
            "violation_logged": False,
            "compensation_applied": changed,
            "operator_approved": True,
        }

    @classmethod
    def compensating(
        cls,
        action: Action,
        remaining_plan: List[Action],
        active_policy: Policy,
        passport: ContextPassport,
    ) -> Tuple[bool, str, List[Action], Dict[str, Any]]:
        changed, mutated, available, details = cls._clamp_future_plan(remaining_plan, active_policy, passport, action)
        if available is not None and available < 0:
            return False, f"ABORTED_COMPENSATION_IMPOSSIBLE: {details}", remaining_plan, {
                "recovery_cost_ms": 2.8,
                "violation_logged": False,
                "compensation_applied": False,
            }
        message = "ALLOWED_COMPENSATED: Downstream spend reallocated to satisfy the active aggregate cap." if changed else "ALLOWED: Remaining plan is compliant under the active policy."
        return True, message, mutated, {
            "recovery_cost_ms": 2.8,
            "violation_logged": False,
            "compensation_applied": changed,
        }

    @classmethod
    def execute(
        cls,
        strategy: RecoveryStrategy,
        action: Action,
        remaining_plan: List[Action],
        active_policy: Policy,
        passport: ContextPassport,
        *,
        rng: random.Random | None = None,
        hitl_approval_probability: float = 0.92,
    ) -> Tuple[bool, str, List[Action], Dict[str, Any]]:
        if strategy == RecoveryStrategy.FAIL_CLOSED:
            return cls.fail_closed(remaining_plan)
        if strategy == RecoveryStrategy.FAIL_OPEN:
            return cls.fail_open(remaining_plan, passport)
        if strategy == RecoveryStrategy.HITL:
            return cls.hitl(action, remaining_plan, active_policy, passport, rng or random.Random(0), hitl_approval_probability)
        if strategy == RecoveryStrategy.COMPENSATING:
            return cls.compensating(action, remaining_plan, active_policy, passport)
        raise ValueError(f"Unknown recovery strategy: {strategy}")
