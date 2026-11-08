"""Virtual-time distributed governance network with stochastic policy propagation."""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from core.models import Policy
from core.policy_engine import _version_key


@dataclass
class DistributedNode:
    node_id: str
    cached_policy: Policy
    generation: int = 0
    pending_updates: List[str] = field(default_factory=list)
    updated_at_ms: float = 0.0

    def apply_policy_update(self, new_policy: Policy, generation: int, at_ms: float) -> bool:
        """Apply only non-stale messages, so late deliveries cannot roll back a node."""
        if generation < self.generation:
            return False
        if generation == self.generation and new_policy.policy_hash != self.cached_policy.policy_hash:
            return False
        self.cached_policy = new_policy
        self.generation = generation
        self.updated_at_ms = at_ms
        return True


@dataclass(order=True)
class _PendingDelivery:
    arrival_time_ms: float
    sequence: int
    node_id: str = field(compare=False)
    policy: Policy = field(compare=False)
    generation: int = field(compare=False)


class GovernanceNetwork:
    """A deterministic discrete-event network; it never sleeps or opens sockets."""

    def __init__(self, node_count: int, initial_policy: Policy, rng: random.Random | None = None):
        if node_count < 1:
            raise ValueError("node_count must be at least 1")
        self.nodes: Dict[str, DistributedNode] = {
            f"node_{index:02d}": DistributedNode(f"node_{index:02d}", initial_policy)
            for index in range(1, node_count + 1)
        }
        self.cluster_policy = initial_policy
        self.rng = rng or random.Random(0)
        self._generation = 0
        self._sequence = 0
        self._events: List[_PendingDelivery] = []
        self.now_ms = 0.0
        self.last_scheduled_delays: Dict[str, float] = {}

    @property
    def pending_event_count(self) -> int:
        return len(self._events)

    def broadcast_update(
        self,
        new_policy: Policy,
        current_time_ms: float | None = None,
        mean_delay_ms: float = 180.0,
        std_dev_ms: float = 40.0,
    ) -> Dict[str, float]:
        """Advance the cluster head immediately and schedule one delivery per edge node."""
        base_time = self.now_ms if current_time_ms is None else float(current_time_ms)
        if base_time < self.now_ms:
            raise ValueError("Cannot broadcast from a time earlier than the network clock")
        if _version_key(new_policy.version) <= _version_key(self.cluster_policy.version):
            raise ValueError(f"Published version {new_policy.version} must be newer than {self.cluster_policy.version}")
        if mean_delay_ms < 0 or std_dev_ms < 0:
            raise ValueError("Propagation delay parameters must be non-negative")

        self.cluster_policy = new_policy
        self._generation += 1
        delays: Dict[str, float] = {}
        for node in self.nodes.values():
            delay = max(10.0, self.rng.gauss(mean_delay_ms, std_dev_ms))
            delays[node.node_id] = delay
            delivery = _PendingDelivery(
                arrival_time_ms=base_time + delay,
                sequence=self._sequence,
                node_id=node.node_id,
                policy=new_policy,
                generation=self._generation,
            )
            self._sequence += 1
            heapq.heappush(self._events, delivery)
            node.pending_updates.append(new_policy.version)
        self.last_scheduled_delays = delays
        return dict(delays)

    def advance_to(self, time_ms: float) -> List[Tuple[str, str, float]]:
        """Process all deliveries at or before ``time_ms`` in deterministic tie order."""
        if time_ms < self.now_ms:
            raise ValueError("Virtual network time cannot move backwards")
        applied: List[Tuple[str, str, float]] = []
        while self._events and self._events[0].arrival_time_ms <= time_ms:
            delivery = heapq.heappop(self._events)
            node = self.nodes[delivery.node_id]
            if delivery.policy.version in node.pending_updates:
                node.pending_updates.remove(delivery.policy.version)
            if node.apply_policy_update(delivery.policy, delivery.generation, delivery.arrival_time_ms):
                applied.append((node.node_id, delivery.policy.version, delivery.arrival_time_ms))
        self.now_ms = float(time_ms)
        return applied

    def select_node(self) -> DistributedNode:
        return self.rng.choice(list(self.nodes.values()))

    def topology(self) -> List[dict]:
        return [
            {
                "node_id": node.node_id,
                "policy_version": node.cached_policy.version,
                "policy_hash": node.cached_policy.policy_hash,
                "generation": node.generation,
                "pending_updates": list(node.pending_updates),
                "updated_at_ms": round(node.updated_at_ms, 3),
            }
            for node in self.nodes.values()
        ]
