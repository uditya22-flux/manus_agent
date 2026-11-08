"""Runtime proxy that stamps requests before mock tool endpoints are reached."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .conflict_detector import ConflictDetector, ConflictReport
from .models import Action, GovernanceEnvelope, Policy, ContextPassport
from .policy_engine import PolicyEngine


@dataclass
class InterceptionResult:
    envelope: GovernanceEnvelope
    conflict: ConflictReport
    policy_violations: List[Dict[str, Any]]
    allowed: bool
    overhead_ms: float = 0.35


class RuntimeInterceptor:
    """Create an auditable envelope and inspect it before any simulated side effect."""

    OVERHEAD_MS = 0.35

    @classmethod
    def inspect(
        cls,
        *,
        action: Action,
        node_id: str,
        node_policy: Policy,
        cluster_policy: Policy,
        passport: ContextPassport,
        policy_engine: PolicyEngine,
        sim_time_ms: float = 0.0,
    ) -> InterceptionResult:
        envelope = GovernanceEnvelope(
            envelope_id=f"env_{passport.session_id}_{action.step_id:02d}_{node_id}_pre_{round(sim_time_ms * 1000)}",
            timestamp=sim_time_ms / 1000.0,
            node_id=node_id,
            evaluated_policy_version=node_policy.version,
            evaluated_policy_hash=node_policy.policy_hash,
            passport=passport.model_copy(deep=True),
            proposed_action=action.model_copy(deep=True),
        )
        conflict = ConflictDetector.inspect(envelope, cluster_policy)
        violations = policy_engine.evaluate_action(node_policy, action, passport.execution_history)
        return InterceptionResult(
            envelope=envelope,
            conflict=conflict,
            policy_violations=violations,
            allowed=not conflict.detected and not violations,
            overhead_ms=cls.OVERHEAD_MS,
        )
