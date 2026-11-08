"""ReAct-style agent state and mock tool endpoint semantics for the simulator."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from core.models import Action, ContextPassport, Policy


class AutonomousAgent:
    """A multi-step plan runner whose external effects exist only in simulator state."""

    def __init__(self, agent_id: str, user_role: str, initial_policy: Policy, plan: List[Action], session_id: str | None = None):
        self.agent_id = agent_id
        self.initial_policy = initial_policy
        self.plan = [action.model_copy(deep=True) for action in plan]
        self.passport = ContextPassport(
            session_id=session_id or f"sess_agent_{agent_id}",
            agent_id=agent_id,
            user_role=user_role,
            initial_policy_version=initial_policy.version,
            initial_policy_hash=initial_policy.policy_hash,
            active_plan_step="step_1",
        )
        self.completed_steps = 0

    def action_at(self, index: int) -> Action:
        return self.plan[index]

    def replace_remaining_plan(self, start_index: int, revised: List[Action]) -> None:
        if len(revised) != len(self.plan) - start_index:
            raise ValueError("A recovery plan must preserve the number of remaining steps")
        self.plan[start_index:] = [action.model_copy(deep=True) for action in revised]

    def commit_action(self, index: int) -> Dict[str, Any]:
        """Commit a mock endpoint effect after authorization; no network/API call occurs."""
        action = self.plan[index]
        if action.executed:
            raise RuntimeError(f"Action step {action.step_id} was already committed")
        cost = action.arguments.get("cost", Decimal("0"))
        amount = cost if isinstance(cost, Decimal) else Decimal(str(cost))
        receipt = {
            "status": "COMMITTED",
            "receipt_id": f"sim-{self.passport.session_id}-{action.step_id:02d}",
            "tool": action.tool_name,
            "simulated_only": True,
        }
        action.executed = True
        action.execution_result = receipt
        self.completed_steps += 1
        self.passport.active_plan_step = f"step_{action.step_id + 1}"
        self.passport.execution_history.append({
            "step_id": action.step_id,
            "tool": action.tool_name,
            "cost": amount,
            "action_type": action.action_type.value,
        })
        return receipt
