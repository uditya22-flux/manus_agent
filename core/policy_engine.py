"""Policy publication, cache resolution, and action-level constraint evaluation."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Tuple

from .models import Action, Policy, PolicyRule, canonical_json


class PolicyVersionError(ValueError):
    """Raised when a policy publication would make the version head ambiguous."""


def as_decimal(value: Any) -> Decimal:
    """Convert monetary values without binary-float arithmetic."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Expected a numeric value, got {value!r}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Expected a numeric value, got {value!r}") from exc
    if not result.is_finite():
        raise ValueError("Monetary values must be finite")
    return result


def _version_key(version: str) -> Tuple[int, ...]:
    parts = version.split(".")
    if not parts or any(not re.fullmatch(r"\d+", part) for part in parts):
        raise PolicyVersionError(f"Policy version must be numeric dotted notation: {version!r}")
    return tuple(int(part) for part in parts)


def _compare(actual: Any, operator: str, threshold: Any) -> bool:
    if operator in {"<", "<=", ">=", ">"}:
        left = as_decimal(actual)
        right = as_decimal(threshold)
        return {"<": left < right, "<=": left <= right, ">=": left >= right, ">": left > right}[operator]
    if operator == "==":
        if isinstance(actual, (int, float, Decimal)) and not isinstance(actual, bool) and isinstance(threshold, (int, float, Decimal)) and not isinstance(threshold, bool):
            return as_decimal(actual) == as_decimal(threshold)
        return actual == threshold
    if operator == "!=":
        return not _compare(actual, "==", threshold)
    if operator == "in":
        return actual in threshold
    if operator == "not_in":
        return actual not in threshold
    raise ValueError(f"Unsupported operator {operator!r}")


class PolicyEngine:
    """In-memory policy authority and evaluator with content/context-aware caching."""

    def __init__(self, initial_policy: Policy):
        self.cluster_policy = initial_policy
        self.history: Dict[Tuple[str, str], Policy] = {(initial_policy.version, initial_policy.policy_hash): initial_policy}
        self.node_caches: Dict[str, Policy] = {}
        self._decision_cache: Dict[str, Tuple[Dict[str, Any], ...]] = {}

    def register_node(self, node_id: str, policy: Policy | None = None) -> None:
        self.node_caches[node_id] = policy or self.cluster_policy

    def publish(self, policy: Policy) -> None:
        old_key = _version_key(self.cluster_policy.version)
        new_key = _version_key(policy.version)
        if new_key <= old_key:
            raise PolicyVersionError(f"New policy {policy.version} must be newer than current {self.cluster_policy.version}")
        self.cluster_policy = policy
        self.history[(policy.version, policy.policy_hash)] = policy
        self._decision_cache.clear()

    def update_node(self, node_id: str, policy: Policy) -> None:
        if (policy.version, policy.policy_hash) not in self.history:
            raise PolicyVersionError("Cannot cache a policy that has not been published by this authority")
        current = self.node_caches.get(node_id)
        if current is not None and _version_key(policy.version) < _version_key(current.version):
            return  # A delayed old message cannot roll a node back.
        self.node_caches[node_id] = policy
        self._decision_cache.clear()

    def resolve_node_policy(self, node_id: str) -> Policy:
        try:
            return self.node_caches[node_id]
        except KeyError as exc:
            raise KeyError(f"Unknown governance node {node_id!r}") from exc

    def evaluate_action(self, policy: Policy, action: Action, execution_history: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        history = list(execution_history)
        cache_key = canonical_json({
            "policy_hash": policy.policy_hash,
            "version": policy.version,
            "domain": policy.domain,
            "tool": action.tool_name,
            "arguments": action.arguments,
            "history": history,
        })
        cached = self._decision_cache.get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached]

        violations: List[Dict[str, Any]] = []
        for map_key, rule in sorted(policy.rules.items()):
            if rule.target_tool not in ("*", action.tool_name):
                continue
            if rule.parameter == "aggregate_budget":
                spent = sum((as_decimal(entry.get("cost", 0)) for entry in history), Decimal("0"))
                proposed = as_decimal(action.arguments.get("cost", 0))
                actual = spent + proposed
            else:
                if rule.parameter not in action.arguments:
                    continue
                actual = action.arguments[rule.parameter]
            try:
                passed = _compare(actual, rule.operator, rule.threshold)
            except (TypeError, ValueError, InvalidOperation) as exc:
                violations.append({
                    "rule_id": rule.rule_id or map_key,
                    "parameter": rule.parameter,
                    "error": str(exc),
                    "severity": rule.severity.value,
                })
                continue
            if not passed:
                violations.append({
                    "rule_id": rule.rule_id or map_key,
                    "parameter": rule.parameter,
                    "actual": str(actual) if isinstance(actual, Decimal) else actual,
                    "operator": rule.operator,
                    "threshold": str(rule.threshold) if isinstance(rule.threshold, Decimal) else rule.threshold,
                    "severity": rule.severity.value,
                })
        self._decision_cache[cache_key] = tuple(dict(item) for item in violations)
        return violations

    @property
    def decision_cache_size(self) -> int:
        return len(self._decision_cache)
