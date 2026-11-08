"""Detect policy snapshot drift at authorization and simulated commit boundaries."""
from __future__ import annotations

from dataclasses import dataclass

from .models import ConflictType, GovernanceEnvelope, Policy


@dataclass(frozen=True)
class ConflictReport:
    detected: bool
    conflict_type: ConflictType
    session_version: str
    node_version: str
    cluster_version: str
    message: str
    session_hash: str = ""
    node_hash: str = ""
    cluster_hash: str = ""


class ConflictDetector:
    @staticmethod
    def inspect(envelope: GovernanceEnvelope, current_cluster_policy: Policy) -> ConflictReport:
        node_v = envelope.evaluated_policy_version
        session_v = envelope.passport.initial_policy_version
        cluster_v = current_cluster_policy.version
        node_h = envelope.evaluated_policy_hash
        session_h = envelope.passport.initial_policy_hash
        cluster_h = current_cluster_policy.policy_hash

        # Hash comparison catches same-version policy equivocation as well as version drift.
        if node_v != cluster_v or node_h != cluster_h:
            return ConflictReport(
                detected=True,
                conflict_type=ConflictType.STALE_NODE_DRIFT,
                session_version=session_v,
                node_version=node_v,
                cluster_version=cluster_v,
                message=f"Node {envelope.node_id} is stale ({node_v}/{node_h}) vs cluster ({cluster_v}/{cluster_h}).",
                session_hash=session_h,
                node_hash=node_h,
                cluster_hash=cluster_h,
            )

        if session_v != node_v or session_h != node_h:
            return ConflictReport(
                detected=True,
                conflict_type=ConflictType.IN_FLIGHT_DIVERGENCE,
                session_version=session_v,
                node_version=node_v,
                cluster_version=cluster_v,
                message=f"Session pinned to {session_v}/{session_h}, but node evaluates under {node_v}/{node_h}.",
                session_hash=session_h,
                node_hash=node_h,
                cluster_hash=cluster_h,
            )

        return ConflictReport(
            detected=False,
            conflict_type=ConflictType.NONE,
            session_version=session_v,
            node_version=node_v,
            cluster_version=cluster_v,
            message="Policy version and hash are consistent across session, node, and cluster.",
            session_hash=session_h,
            node_hash=node_h,
            cluster_hash=cluster_h,
        )
