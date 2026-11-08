"""Validated data models used by the AgentBridge runtime and simulator."""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _canonical_value(value: Any) -> Any:
    """Normalize values for stable JSON hashes across processes and numeric types."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimals cannot be hashed")
        normalized = format(value.normalize(), "f")
        if normalized in {"-0", ""}:
            normalized = "0"
        return {"$number": normalized}
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return {"$number": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite floats cannot be hashed")
        normalized = format(Decimal(str(value)).normalize(), "f")
        return {"$number": "0" if normalized in {"-0", ""} else normalized}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return _canonical_value(value.model_dump(mode="python"))
    raise TypeError(f"Unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return deterministic JSON with normalized numbers and sorted keys."""
    return json.dumps(_canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class TaintStatus(str, Enum):
    CLEAN = "CLEAN"
    TAINTED = "TAINTED"


class ActionType(str, Enum):
    REVERSIBLE = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class RecoveryStrategy(str, Enum):
    FAIL_CLOSED = "FAIL_CLOSED"
    FAIL_OPEN = "FAIL_OPEN"
    HITL = "HITL"
    COMPENSATING = "COMPENSATING"


class ConflictType(str, Enum):
    NONE = "NONE"
    STALE_NODE_DRIFT = "STALE_NODE_DRIFT"
    IN_FLIGHT_DIVERGENCE = "IN_FLIGHT_DIVERGENCE"


class RuleSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    CRITICAL = "CRITICAL"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


class PolicyRule(StrictModel):
    rule_id: str = Field(min_length=1)
    target_tool: str = Field(min_length=1, description="Tool name or '*' for all tools")
    parameter: str = Field(min_length=1)
    operator: str
    threshold: Any
    severity: RuleSeverity = RuleSeverity.MEDIUM

    @model_validator(mode="after")
    def validate_operator(self) -> "PolicyRule":
        allowed = {"<", "<=", "==", "!=", ">=", ">", "in", "not_in"}
        if self.operator not in allowed:
            raise ValueError(f"Unsupported policy operator {self.operator!r}; expected one of {sorted(allowed)}")
        return self


class Policy(StrictModel):
    """A content-addressed policy snapshot; issuance time is excluded from its digest."""

    version: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    rules: Dict[str, PolicyRule]
    timestamp: float = Field(default_factory=time.time)
    policy_hash: str = ""

    @model_validator(mode="after")
    def set_or_verify_hash(self) -> "Policy":
        expected = self.compute_hash(self.version, self.domain, self.rules)
        if self.policy_hash and self.policy_hash != expected:
            raise ValueError("policy_hash does not match the canonical policy contents")
        if not self.policy_hash:
            object.__setattr__(self, "policy_hash", expected)
        return self

    @staticmethod
    def compute_hash(version: str, domain: str, rules: Dict[str, PolicyRule]) -> str:
        payload = {
            "version": version,
            "domain": domain,
            "rules": {key: value.model_dump(mode="python") for key, value in sorted(rules.items())},
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


class ContextPassport(StrictModel):
    session_id: str = Field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:8]}")
    agent_id: str = Field(min_length=1)
    user_role: str = Field(min_length=1)
    initial_policy_version: str = Field(min_length=1)
    initial_policy_hash: str = Field(min_length=1)
    active_plan_step: str = "step_1"
    taint_status: TaintStatus = TaintStatus.CLEAN
    execution_history: List[Dict[str, Any]] = Field(default_factory=list)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "taint_status" and getattr(self, "taint_status", TaintStatus.CLEAN) == TaintStatus.TAINTED:
            if value not in (TaintStatus.TAINTED, TaintStatus.TAINTED.value):
                raise ValueError("Passport taint is monotonic and cannot be cleared")
        super().__setattr__(name, value)

    def mark_tainted(self) -> None:
        self.taint_status = TaintStatus.TAINTED


class Action(StrictModel):
    step_id: int = Field(ge=1)
    tool_name: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    action_type: ActionType
    duration_ms: float = Field(default=25.0, ge=0.0)
    executed: bool = False
    execution_result: Dict[str, Any] | None = None


class GovernanceEnvelope(StrictModel):
    envelope_id: str = Field(default_factory=lambda: f"env_{uuid.uuid4().hex[:8]}")
    timestamp: float = Field(default_factory=time.time)
    node_id: str = Field(min_length=1)
    evaluated_policy_version: str = Field(min_length=1)
    evaluated_policy_hash: str = Field(min_length=1)
    passport: ContextPassport
    proposed_action: Action
    signature: str = ""

    @model_validator(mode="after")
    def set_or_verify_signature(self) -> "GovernanceEnvelope":
        expected = self.compute_signature()
        if self.signature and self.signature != expected:
            raise ValueError("Governance envelope signature does not match its contents")
        if not self.signature:
            object.__setattr__(self, "signature", expected)
        return self

    def signature_payload(self) -> Dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "node_id": self.node_id,
            "timestamp": self.timestamp,
            "evaluated_policy_version": self.evaluated_policy_version,
            "evaluated_policy_hash": self.evaluated_policy_hash,
            "passport": self.passport.model_dump(mode="python"),
            "proposed_action": self.proposed_action.model_dump(mode="python"),
        }

    def compute_signature(self) -> str:
        return hashlib.sha256(canonical_json(self.signature_payload()).encode("utf-8")).hexdigest()[:16]

    def verify_signature(self) -> bool:
        return self.signature == self.compute_signature()
