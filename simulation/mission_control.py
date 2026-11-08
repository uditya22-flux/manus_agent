"""Stateful demo mission-control services for the in-memory governance portal.

All records and button actions are simulated, process-local demo data. Nothing in this
module connects to real agents, customers, approval systems, or external tools.
"""
from __future__ import annotations

import math
import heapq
import random
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List

from core.models import Action, ActionType, ContextPassport, GovernanceEnvelope, Policy, PolicyRule, RuleSeverity, TaintStatus
from core.policy_engine import PolicyEngine, _version_key, as_decimal


@dataclass
class AgentRecord:
    agent_id: str
    agent_name: str
    domain: str
    user_role: str
    session_id: str
    node_id: str
    initial_policy_version: str
    initial_policy_hash: str
    active_step: str
    status: str
    taint_status: TaintStatus
    taint_reason: str = ""


class MissionControl:
    """Deterministic demo fleet with explicit in-memory policy and intervention actions."""

    def __init__(self, seed: int = 20261108, node_count: int = 10):
        if node_count < 2:
            raise ValueError("node_count must be at least 2 to represent stale and current nodes")
        self.seed = seed
        self.node_count = node_count
        self._rng = random.Random(seed)
        self.nodes = {f"node_{index:02d}": {"version": "2.0.0", "hash": "", "pending": []} for index in range(1, node_count + 1)}
        self.initial_policy = Policy(
            version="1.0.0", domain="company", rules={
                "BUDGET": PolicyRule(rule_id="BUDGET", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal("500.00"), severity=RuleSeverity.CRITICAL),
            },
        )
        self.cluster_policy = Policy(
            version="2.0.0", domain="company", rules={
                "BUDGET": PolicyRule(rule_id="BUDGET", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal("400.00"), severity=RuleSeverity.CRITICAL),
                "PII": PolicyRule(rule_id="PII", target_tool="*", parameter="allow_pii_export", operator="==", threshold=False, severity=RuleSeverity.CRITICAL),
            },
        )
        for node in self.nodes.values():
            node["hash"] = self.cluster_policy.policy_hash
        # One deliberately stale edge node demonstrates policy adoption and stale drift.
        if node_count >= 10:
            self.nodes["node_10"] = {"version": self.initial_policy.version, "hash": self.initial_policy.policy_hash, "pending": [self.cluster_policy.version]}
        elif node_count > 1:
            stale_id = sorted(self.nodes)[-1]
            self.nodes[stale_id] = {"version": self.initial_policy.version, "hash": self.initial_policy.policy_hash, "pending": [self.cluster_policy.version]}

        stale_node = sorted(key for key, value in self.nodes.items() if value["version"] != self.cluster_policy.version)[0]
        current_nodes = sorted(key for key in self.nodes if key != stale_node)
        records = [
            ("FinOps-Worker-42", "FinOps Worker 42", "Procurement", "finance_operator", current_nodes[0], "1.0.0", "step_4", "CONFLICT_DETECTED", TaintStatus.TAINTED, "Untrusted payment webhook payload"),
            ("Support-Agent-07", "Customer Support 07", "Customer Support", "support_agent", current_nodes[0], "2.0.0", "step_2", "ONLINE", TaintStatus.CLEAN, ""),
            ("Data-Pipeline-12", "Data Pipeline 12", "Internal BI", "data_engineer", stale_node, "1.0.0", "step_3", "HALTED", TaintStatus.TAINTED, "External source schema changed"),
            ("Code-Review-03", "Code Review 03", "Engineering", "reviewer", current_nodes[min(1, len(current_nodes) - 1)], "2.0.0", "step_1", "IDLE", TaintStatus.CLEAN, ""),
            ("Customer-Success-21", "Customer Success 21", "Customer Support", "customer_success", current_nodes[min(2, len(current_nodes) - 1)], "2.0.0", "step_5", "PENDING_HUMAN_REVIEW", TaintStatus.CLEAN, "Irreversible customer message requires review"),
            ("Internal-BI-04", "Internal BI 04", "Internal BI", "analyst", current_nodes[min(3, len(current_nodes) - 1)], "2.0.0", "step_2", "ONLINE", TaintStatus.CLEAN, ""),
        ]
        self.agents: Dict[str, AgentRecord] = {}
        for agent_id, name, domain, role, node_id, initial_version, step, status, taint, reason in records:
            initial_policy = self.initial_policy if initial_version == self.initial_policy.version else self.cluster_policy
            self.agents[agent_id] = AgentRecord(
                agent_id=agent_id,
                agent_name=name,
                domain=domain,
                user_role=role,
                session_id=f"sess_{agent_id.lower().replace('-', '_')}",
                node_id=node_id,
                initial_policy_version=initial_version,
                initial_policy_hash=initial_policy.policy_hash,
                active_step=step,
                status=status,
                taint_status=taint,
                taint_reason=reason,
            )

        self.historical_sessions = self._create_history(500)
        self.hard_token_budget = 80_000
        self.token_spend = 56_240
        self._resolved_interventions = 17
        self.audit_log: List[Dict[str, Any]] = [
            {"time_ago_hours": 23.4, "event": "Policy v2.0.0 published", "actor": "Governance Board", "result": "Published"},
            {"time_ago_hours": 8.2, "event": "node_10 drift detected", "actor": "runtime-interceptor", "result": "Quarantined"},
            {"time_ago_hours": 2.7, "event": "FinOps-Worker-42 escalated", "actor": "runtime-interceptor", "result": "Pending operator"},
        ]
        self.incidents = [
            {"time_ago_hours": 0.6, "kind": "MID-FLIGHT-CONFLICT", "session_id": "sess_finops_worker_42", "severity": "CRITICAL"},
            {"time_ago_hours": 2.7, "kind": "HITL-ESCALATION", "session_id": "sess_customer_success_21", "severity": "MEDIUM"},
            {"time_ago_hours": 8.2, "kind": "STALE-NODE", "session_id": "sess_data_pipeline_12", "severity": "HIGH"},
            {"time_ago_hours": 11.1, "kind": "TAINTED-CONTEXT", "session_id": "sess_finops_worker_42", "severity": "HIGH"},
            {"time_ago_hours": 18.0, "kind": "HITL-ESCALATION", "session_id": "sess_customer_success_21", "severity": "LOW"},
        ]
        self.prompt_mutations = [
            {"time_ago_hours": 23.4, "source": "Governance Board (demo)", "request": "Reduce the procurement aggregate cap to $400", "compiled_rule": "aggregate_budget <= 400.00", "status": "VALIDATED", "note": "Illustrative compiler audit record; no LLM call is made."},
            {"time_ago_hours": 8.2, "source": "Security owner (demo)", "request": "Block PII exports from every tool", "compiled_rule": "allow_pii_export == false", "status": "VALIDATED", "note": "Illustrative compiler audit record; no LLM call is made."},
        ]
        self.hitl_requests: List[Dict[str, Any]] = [
            {
                "request_id": "HITL-2401", "agent_id": "FinOps-Worker-42", "tool": "stripe_payment_gateway",
                "action_type": "IRREVERSIBLE", "session_version": "1.0.0", "cluster_version": "2.0.0",
                "spent": Decimal("450.00"), "old_cap": Decimal("500.00"), "new_cap": Decimal("400.00"),
                "proposed_cost": Decimal("150.00"), "purpose": "Hotel reservation in Delhi",
                "status": "PENDING", "taint_status": "TAINTED", "residual_violation": Decimal("0.00"),
            },
            {
                "request_id": "HITL-2402", "agent_id": "Customer-Success-21", "tool": "send_customer_email",
                "action_type": "IRREVERSIBLE", "session_version": "2.0.0", "cluster_version": "2.0.0",
                "spent": Decimal("450.00"), "old_cap": Decimal("1000.00"), "new_cap": Decimal("650.00"),
                "proposed_cost": Decimal("300.00"), "purpose": "Conference hotel reservation",
                "status": "PENDING", "taint_status": "CLEAN", "residual_violation": Decimal("0.00"),
            },
        ]
        self.last_rollout: Dict[str, Any] | None = None
        self._pending_deliveries: List[tuple[float, int, str, Policy]] = []
        self._delivery_sequence = 0
        self.virtual_time_ms = 0.0
        self.draft_rules = [rule.model_copy(deep=True) for rule in self.cluster_policy.rules.values()]

    def _create_history(self, count: int) -> List[Dict[str, Any]]:
        rng = random.Random(self.seed ^ 0x4A9B12)
        tools = ["stripe_payment_gateway", "aws_s3_export", "aws_s3_delete", "reserve_hotel", "send_customer_email", "query_data_warehouse"]
        domains = ["Procurement", "Customer Support", "Internal BI", "Engineering"]
        result = []
        for index in range(count):
            committed = Decimal(rng.randrange(0, 90100)) / Decimal("100")
            proposed = Decimal(rng.randrange(0, 40000)) / Decimal("100")
            result.append({
                "session_id": f"hist-{index + 1:04d}",
                "domain": rng.choice(domains),
                "proposed_tool": rng.choice(tools),
                "committed": committed,
                "proposed": proposed,
                "allow_pii_export": bool(rng.getrandbits(1)),
            })
        return result

    def _version_hash(self, version: str) -> str:
        if version == self.cluster_policy.version:
            return self.cluster_policy.policy_hash
        if version == self.initial_policy.version:
            return self.initial_policy.policy_hash
        if self.last_rollout and version == self.last_rollout["version"]:
            return self.last_rollout["policy_hash"]
        return "unavailable"

    def fleet_records(self) -> List[Dict[str, Any]]:
        rows = []
        for agent in self.agents.values():
            node = self.nodes[agent.node_id]
            if node["version"] != self.cluster_policy.version or node["hash"] != self.cluster_policy.policy_hash:
                consistency = "STALE-NODE"
            elif agent.initial_policy_version != node["version"] or agent.initial_policy_hash != node["hash"]:
                consistency = "MID-FLIGHT-CONFLICT"
            else:
                consistency = "IN-SYNC"
            rows.append({
                "agent_name": agent.agent_name,
                "agent_id": agent.agent_id,
                "domain": agent.domain,
                "session_id": agent.session_id,
                "node_id": agent.node_id,
                "status": agent.status,
                "initial_policy_version": agent.initial_policy_version,
                "current_node_version": node["version"],
                "cluster_version": self.cluster_policy.version,
                "consistency": consistency,
                "taint_status": agent.taint_status.value,
                "active_step": agent.active_step,
                "pending_update": ", ".join(node["pending"]),
            })
        return rows

    def passport_for(self, agent_id: str) -> Dict[str, Any]:
        agent = self.agents[agent_id]
        node = self.nodes[agent.node_id]
        return {
            "session_id": agent.session_id,
            "agent_id": agent.agent_id,
            "user_role": agent.user_role,
            "domain": agent.domain,
            "initial_policy_version": agent.initial_policy_version,
            "initial_policy_hash": agent.initial_policy_hash,
            "active_plan_step": agent.active_step,
            "taint_status": agent.taint_status.value,
            "taint_reason": agent.taint_reason,
            "node_id": agent.node_id,
            "node_policy_version": node["version"],
            "cluster_policy_version": self.cluster_policy.version,
        }

    def session_trace(self, agent_id: str) -> List[Dict[str, Any]]:
        """Return a signed, deterministic five-step operational trace for a demo agent."""
        agent = self._agent(agent_id)
        node = self.nodes[agent.node_id]
        tool_sets = {
            "Procurement": [
                ("search_flight_schedules", ActionType.REVERSIBLE, {"route": "COK-DEL"}),
                ("stripe_payment_gateway", ActionType.IRREVERSIBLE, {"vendor": "AirIndia", "cost": Decimal("450.00")}),
                ("check_hotel_availability", ActionType.REVERSIBLE, {"city": "Delhi"}),
                ("reserve_hotel", ActionType.IRREVERSIBLE, {"vendor": "Hyatt", "cost": Decimal("150.00")}),
                ("dispatch_calendar_invites", ActionType.IRREVERSIBLE, {"attendees": ["team@domain.com"]}),
            ],
            "Customer Support": [
                ("lookup_customer_ticket", ActionType.REVERSIBLE, {"ticket_id": "T-1042"}),
                ("check_refund_eligibility", ActionType.REVERSIBLE, {"order_id": "O-883"}),
                ("update_customer_record", ActionType.IRREVERSIBLE, {"field": "case_status", "value": "review"}),
                ("send_customer_email", ActionType.IRREVERSIBLE, {"template": "case_update"}),
                ("close_support_case", ActionType.IRREVERSIBLE, {"ticket_id": "T-1042"}),
            ],
            "Internal BI": [
                ("query_data_warehouse", ActionType.REVERSIBLE, {"dataset": "finance_daily"}),
                ("aggregate_report", ActionType.REVERSIBLE, {"period": "quarter"}),
                ("aws_s3_export", ActionType.IRREVERSIBLE, {"destination": "reports/quarter.csv"}),
                ("publish_dashboard", ActionType.IRREVERSIBLE, {"dashboard": "finance-health"}),
                ("notify_analysts", ActionType.IRREVERSIBLE, {"channel": "bi-ops"}),
            ],
            "Engineering": [
                ("fetch_repository_diff", ActionType.REVERSIBLE, {"repository": "service-api"}),
                ("run_test_suite", ActionType.REVERSIBLE, {"suite": "unit"}),
                ("check_dependency_policy", ActionType.REVERSIBLE, {"scope": "production"}),
                ("open_review_comment", ActionType.IRREVERSIBLE, {"repository": "service-api"}),
                ("merge_pull_request", ActionType.IRREVERSIBLE, {"branch": "release"}),
            ],
        }
        plan = tool_sets.get(agent.domain, tool_sets["Internal BI"])
        active_step = int(agent.active_step.rsplit("_", 1)[-1])
        committed_history: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []
        for step_id, (tool_name, action_type, arguments) in enumerate(plan, start=1):
            if step_id < active_step:
                decision = "COMMITTED"
            elif step_id > active_step:
                decision = "NOT_REACHED"
            elif agent.status == "CONFLICT_DETECTED":
                decision = "CONFLICT_DETECTED"
            elif agent.status == "PENDING_HUMAN_REVIEW":
                decision = "PENDING_HUMAN_REVIEW"
            elif agent.status == "HALTED":
                decision = "ABORTED"
            elif agent.status == "IDLE":
                decision = "IDLE"
            elif agent.status in {"PAUSED", "TERMINATED"}:
                decision = agent.status
            else:
                decision = "READY"

            action = Action(step_id=step_id, tool_name=tool_name, arguments=arguments, action_type=action_type)
            passport = ContextPassport(
                session_id=agent.session_id,
                agent_id=agent.agent_id,
                user_role=agent.user_role,
                initial_policy_version=agent.initial_policy_version,
                initial_policy_hash=agent.initial_policy_hash,
                active_plan_step=f"step_{step_id}",
                taint_status=agent.taint_status,
                execution_history=[dict(item) for item in committed_history],
            )
            envelope = GovernanceEnvelope(
                envelope_id=f"env_{agent.session_id}_{step_id:02d}_{agent.node_id}_inspector",
                timestamp=step_id / 1000.0,
                node_id=agent.node_id,
                evaluated_policy_version=node["version"],
                evaluated_policy_hash=node["hash"],
                passport=passport,
                proposed_action=action,
            )
            spent = sum((as_decimal(item.get("cost", 0)) for item in committed_history), Decimal("0"))
            proposed_cost = as_decimal(arguments.get("cost", 0))
            cap_rule = next((rule for rule in self.cluster_policy.rules.values() if rule.parameter == "aggregate_budget"), None)
            violations = []
            if cap_rule and spent + proposed_cost > as_decimal(cap_rule.threshold):
                violations.append({"rule_id": cap_rule.rule_id, "actual": str(spent + proposed_cost), "operator": cap_rule.operator, "threshold": str(cap_rule.threshold), "severity": cap_rule.severity.value})
            report = {
                "step_id": step_id,
                "tool": tool_name,
                "action_type": action_type.value,
                "plan_note": f"{action_type.value.title()} operation in the {agent.domain} workflow; endpoint effect is simulated.",
                "arguments": {key: str(value) if isinstance(value, Decimal) else value for key, value in arguments.items()},
                "decision": decision,
                "executed": decision == "COMMITTED",
                "tool_output": "Mock receipt recorded" if decision == "COMMITTED" else "No external side effect committed",
                "conflict_type": "STALE_NODE_DRIFT" if node["version"] != self.cluster_policy.version else "IN_FLIGHT_DIVERGENCE" if agent.initial_policy_version != node["version"] else "NONE",
                "policy_violations": violations,
                "envelope": envelope.model_dump(mode="json"),
            }
            trace.append(report)
            if decision == "COMMITTED":
                entry = {"step_id": step_id, "tool": tool_name, "cost": arguments.get("cost", Decimal("0"))}
                committed_history.append(entry)
        return trace

    def overview(self) -> Dict[str, Any]:
        statuses = [agent.status for agent in self.agents.values()]
        online = sum(status == "ONLINE" for status in statuses)
        active = sum(status in {"ONLINE", "CONFLICT_DETECTED", "PENDING_HUMAN_REVIEW"} for status in statuses)
        idle = sum(status in {"IDLE", "PAUSED"} for status in statuses)
        halted = sum(status in {"HALTED", "TERMINATED"} for status in statuses)
        adoption: Dict[str, int] = {}
        for node in self.nodes.values():
            adoption[node["version"]] = adoption.get(node["version"], 0) + 1
        attention = sum(agent.status in {"CONFLICT_DETECTED", "PENDING_HUMAN_REVIEW"} for agent in self.agents.values())
        tainted = sum(agent.taint_status == TaintStatus.TAINTED for agent in self.agents.values())
        recent_incidents = [item for item in self.incidents if 0 <= item["time_ago_hours"] <= 24]
        return {
            "online_agents": online,
            "active_agents": active,
            "idle_agents": idle,
            "halted_agents": halted,
            "total_agents": len(self.agents),
            "cluster_policy_version": self.cluster_policy.version,
            "policy_adoption": {version: count / self.node_count * 100 for version, count in adoption.items()},
            "node_versions": adoption,
            "stale_nodes": sum(node["version"] != self.cluster_policy.version or node["hash"] != self.cluster_policy.policy_hash for node in self.nodes.values()),
            "incidents_24h": len(recent_incidents),
            "divergence_incidents_24h": sum(item["kind"] in {"STALE-NODE", "MID-FLIGHT-CONFLICT"} for item in recent_incidents),
            "attention_required": attention,
            "tainted_agents": tainted,
            "token_spend": self.token_spend,
            "token_budget": self.hard_token_budget,
        }

    def pause_agent(self, agent_id: str) -> Dict[str, Any]:
        agent = self._agent(agent_id)
        if agent.status == "TERMINATED":
            raise ValueError("A terminated thread cannot be paused")
        agent.status = "PAUSED"
        self._audit("Pause agent", agent_id, "Paused")
        return self.passport_for(agent_id)

    def force_update_agent(self, agent_id: str) -> Dict[str, Any]:
        agent = self._agent(agent_id)
        self.nodes[agent.node_id].update({"version": self.cluster_policy.version, "hash": self.cluster_policy.policy_hash, "pending": []})
        if agent.status == "CONFLICT_DETECTED" and agent.initial_policy_version == self.cluster_policy.version and agent.initial_policy_hash == self.cluster_policy.policy_hash:
            agent.status = "ONLINE"
        self._audit("Force policy cache update", agent_id, self.cluster_policy.version)
        return self.passport_for(agent_id)

    def terminate_agent(self, agent_id: str) -> Dict[str, Any]:
        agent = self._agent(agent_id)
        agent.status = "TERMINATED"
        self._audit("Terminate thread", agent_id, "Terminated")
        return self.passport_for(agent_id)

    def _agent(self, agent_id: str) -> AgentRecord:
        try:
            return self.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"Unknown agent {agent_id!r}") from exc

    def _audit(self, event: str, actor: str, result: str) -> None:
        self.audit_log.insert(0, {"time_ago_hours": 0.0, "event": event, "actor": actor, "result": result})

    def build_policy(self, version: str, domain: str, rules: List[PolicyRule]) -> Policy:
        if not rules:
            raise ValueError("A policy must contain at least one rule")
        current_key = _version_key(self.cluster_policy.version)
        new_key = _version_key(version)
        if new_key <= current_key and not (version == self.cluster_policy.version and self.last_rollout and self.last_rollout["policy_hash"] == Policy.compute_hash(version, domain, {rule.rule_id: rule for rule in rules})):
            raise ValueError(f"Draft version {version} must be newer than active version {self.cluster_policy.version}")
        return Policy(version=version, domain=domain, rules={rule.rule_id: rule for rule in rules})

    @staticmethod
    def _rule_text(policy: Policy, key: str) -> str:
        rule = policy.rules.get(key)
        if rule is None:
            return "—"
        threshold = str(rule.threshold)
        return f"{rule.target_tool}.{rule.parameter} {rule.operator} {threshold} ({rule.severity.value})"

    def policy_diff(self, draft: Policy) -> List[Dict[str, str]]:
        keys = sorted(set(self.cluster_policy.rules) | set(draft.rules))
        return [
            {"rule_id": key, "active": self._rule_text(self.cluster_policy, key), "proposed": self._rule_text(draft, key), "change": "UNCHANGED" if self._rule_text(self.cluster_policy, key) == self._rule_text(draft, key) else "CHANGED"}
            for key in keys
        ]

    def canary_rollout(self, policy: Policy, percent: int) -> Dict[str, Any]:
        if not 1 <= percent <= 100:
            raise ValueError("Canary percentage must be in [1, 100]")
        current = self.cluster_policy
        if _version_key(policy.version) < _version_key(current.version):
            raise ValueError("A rollout cannot move the cluster head backwards")
        if policy.version == current.version and policy.policy_hash != current.policy_hash:
            raise ValueError("A policy version cannot be reused with different rules")
        if _version_key(policy.version) > _version_key(current.version):
            self.cluster_policy = policy
            self._audit(f"Policy {policy.version} published", "Governance Board", "Cluster head advanced")
        same_rollout = bool(self.last_rollout and self.last_rollout["version"] == policy.version and self.last_rollout["policy_hash"] == policy.policy_hash)
        if not same_rollout:
            self.last_rollout = {
                "version": policy.version,
                "policy_hash": policy.policy_hash,
                "requested_percent": percent,
                "delays_ms": {},
            }
        self.last_rollout["requested_percent"] = percent
        target_count = max(1, math.ceil(self.node_count * percent / 100))
        updated_count = sum(node["version"] == policy.version and node["hash"] == policy.policy_hash for node in self.nodes.values())
        pending_nodes = {event[2] for event in self._pending_deliveries if event[3].policy_hash == policy.policy_hash}
        needing = [node_id for node_id, node in sorted(self.nodes.items()) if (node["version"] != policy.version or node["hash"] != policy.policy_hash) and node_id not in pending_nodes]
        schedule_count = min(len(needing), max(0, target_count - updated_count - len(pending_nodes)))
        selected = needing[:schedule_count]
        delays: Dict[str, float] = {}
        for node_id in selected:
            delay = max(10.0, self._rng.gauss(180.0, 40.0))
            delays[node_id] = delay
            heapq.heappush(self._pending_deliveries, (self.virtual_time_ms + delay, self._delivery_sequence, node_id, policy))
            self._delivery_sequence += 1
            self.nodes[node_id]["pending"] = [policy.version]
        self.last_rollout["delays_ms"].update(delays)
        status = self.rollout_status() or {}
        status["scheduled_nodes"] = selected
        status["scheduled_count"] = len(selected)
        self._audit(f"Canary rollout {policy.version}", "Governance Board", f"{status['updated_count']}/{self.node_count} current; {status['pending_count']} pending")
        return status

    def advance_propagation(self, delta_ms: float = 200.0) -> Dict[str, Any]:
        """Advance virtual time and apply policy deliveries whose stochastic delay elapsed."""
        if delta_ms < 0:
            raise ValueError("Virtual time increment must be non-negative")
        target_time = self.virtual_time_ms + float(delta_ms)
        delivered: List[Dict[str, Any]] = []
        while self._pending_deliveries and self._pending_deliveries[0][0] <= target_time:
            arrival, _, node_id, policy = heapq.heappop(self._pending_deliveries)
            node = self.nodes[node_id]
            if policy.version in node["pending"]:
                node["pending"].remove(policy.version)
            if _version_key(policy.version) >= _version_key(node["version"]):
                node.update({"version": policy.version, "hash": policy.policy_hash})
                delivered.append({"node_id": node_id, "policy_version": policy.version, "arrival_time_ms": round(arrival, 3)})
        self.virtual_time_ms = target_time
        if delivered:
            self._audit("Policy propagation events delivered", "virtual-clock", f"{len(delivered)} node(s) by {self.virtual_time_ms:.1f} ms")
        return {"virtual_time_ms": self.virtual_time_ms, "delivered": delivered, "rollout": self.rollout_status()}

    def _snapshot_rollout_status(self) -> Dict[str, Any] | None:
        if self.last_rollout is None:
            return None
        version = self.last_rollout["version"]
        policy_hash = self.last_rollout["policy_hash"]
        updated_count = sum(node["version"] == version and node["hash"] == policy_hash for node in self.nodes.values())
        pending_count = sum(event[3].version == version and event[3].policy_hash == policy_hash for event in self._pending_deliveries)
        return {
            **self.last_rollout,
            "updated_count": updated_count,
            "pending_count": pending_count,
            "total_nodes": self.node_count,
            "progress_percent": updated_count / self.node_count * 100.0,
            "virtual_time_ms": self.virtual_time_ms,
        }

    def shadow_replay(self, policy: Policy, limit: int = 500) -> Dict[str, Any]:
        if limit < 1:
            raise ValueError("Replay limit must be positive")
        sessions = self.historical_sessions[-min(limit, len(self.historical_sessions)):]
        engine = PolicyEngine(policy)
        halts = 0
        compensation_candidates = 0
        preexisting_excess = 0
        compliant = 0
        violations_by_rule = {rule.rule_id: 0 for rule in policy.rules.values()}
        unsupported_rules = sorted(rule.rule_id for rule in policy.rules.values() if rule.parameter not in {"aggregate_budget", "allow_pii_export"})
        for item in sessions:
            committed = item["committed"]
            action = Action(
                step_id=1,
                tool_name=item["proposed_tool"],
                arguments={"cost": item["proposed"], "allow_pii_export": item["allow_pii_export"]},
                action_type=ActionType.IRREVERSIBLE,
            )
            violations = engine.evaluate_action(policy, action, [{"cost": committed}])
            for violation in violations:
                violations_by_rule[violation["rule_id"]] = violations_by_rule.get(violation["rule_id"], 0) + 1
            if violations:
                halts += 1
                budget_violation = any(item.get("parameter") == "aggregate_budget" for item in violations)
                budget_rule = next((rule for rule in policy.rules.values() if rule.parameter == "aggregate_budget" and rule.target_tool in ("*", action.tool_name)), None)
                if budget_violation and budget_rule and committed <= as_decimal(budget_rule.threshold):
                    compensation_candidates += 1
                elif budget_violation and budget_rule:
                    preexisting_excess += 1
            else:
                compliant += 1
        return {
            "sample_size": len(sessions),
            "policy_version": policy.version,
            "policy_hash": policy.policy_hash,
            "predicted_halts": halts,
            "compensation_candidates": compensation_candidates,
            "already_over_cap_irreversible": preexisting_excess,
            "compliant_without_change": compliant,
            "halt_rate_percent": halts / len(sessions) * 100.0,
            "violations_by_rule": violations_by_rule,
            "unsupported_rules": unsupported_rules,
            "history_type": "deterministic synthetic demo archive; not production recordings",
        }

    def hitl_queue(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self.hitl_requests]

    def decide_hitl(self, request_id: str, decision: str) -> Dict[str, Any]:
        request = next((item for item in self.hitl_requests if item["request_id"] == request_id), None)
        if request is None:
            raise KeyError(f"Unknown HITL request {request_id!r}")
        if request["status"] != "PENDING":
            raise ValueError(f"Request {request_id} is already {request['status']}")
        agent = self._agent(request["agent_id"])
        if decision == "override":
            request["status"] = "APPROVED_OVERRIDE"
            request["decision_note"] = "One-time policy bypass approved; session remains tainted and audited."
            agent.taint_status = TaintStatus.TAINTED
            self._audit("One-time policy override", request["agent_id"], request["request_id"])
        elif decision == "compensate":
            cap = as_decimal(request["new_cap"])
            spent = as_decimal(request["spent"])
            proposed = as_decimal(request["proposed_cost"])
            remaining = max(Decimal("0.00"), cap - spent)
            clamped = min(proposed, remaining).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            residual = max(Decimal("0.00"), spent - cap)
            request["proposed_cost"] = clamped
            request["residual_violation"] = residual
            request["status"] = "COMPENSATED" if residual == 0 else "COMPENSATED_WITH_RESIDUAL_VIOLATION"
            request["decision_note"] = f"Future action clamped to ${clamped:.2f}; prior committed spend leaves ${residual:.2f} residual excess. No rollback is possible."
            self._audit("Compensating clamp", request["agent_id"], request["decision_note"])
        elif decision == "abort":
            request["status"] = "ABORTED_FROZEN"
            request["decision_note"] = "Workflow aborted and agent thread frozen before the proposed tool call."
            agent.status = "HALTED"
            self._audit("Abort and freeze", request["agent_id"], request["request_id"])
        else:
            raise ValueError("decision must be override, compensate, or abort")
        self._resolved_interventions += 1
        return dict(request)

    def token_economy(self) -> Dict[str, int | float]:
        raw_exception_tokens = 1_200
        concise_policy_repair_tokens = 190
        saved_each = raw_exception_tokens - concise_policy_repair_tokens
        return {
            "resolved_interventions": self._resolved_interventions,
            "raw_exception_tokens_per_case": raw_exception_tokens,
            "policy_repair_tokens_per_case": concise_policy_repair_tokens,
            "estimated_tokens_saved": self._resolved_interventions * saved_each,
            "estimated_saved_per_case": saved_each,
            "status": "modeled estimate; tokenization/model calls are not instrumented",
        }

    def propagation_samples(self, count: int = 100) -> List[float]:
        rng = random.Random(self.seed ^ 0x1A2B3C)
        return [max(10.0, rng.gauss(180.0, 40.0)) for _ in range(count)]

    def latency_samples(self, count: int = 250) -> Dict[str, List[float]]:
        """Return seeded demo latency samples; these are modeled, not observed measurements."""
        if count < 1:
            raise ValueError("Sample count must be positive")
        rng = random.Random(self.seed ^ 0x8842AA)
        return {
            "automated_proxy_ms": [max(0.05, rng.gauss(0.35, 0.07)) for _ in range(count)],
            "human_escalation_ms": [max(50.0, rng.gauss(450.0, 35.0)) for _ in range(count)],
        }

    def rollout_status(self) -> Dict[str, Any] | None:
        return self._snapshot_rollout_status()
