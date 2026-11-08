from decimal import Decimal

from core.models import Policy, PolicyRule
from simulation.network import DistributedNode, GovernanceNetwork


class FixedDelayRng:
    def __init__(self, delays):
        self.delays = iter(delays)

    def gauss(self, mean, std_dev):
        return next(self.delays)

    def choice(self, values):
        return values[0]


def policy(version, cap):
    return Policy(version=version, domain="procurement", rules={"R1": PolicyRule(rule_id="R1", target_tool="*", parameter="aggregate_budget", operator="<=", threshold=Decimal(str(cap)))})


def test_gaussian_propagation_delay_is_lower_bounded_at_ten_ms():
    v1, v2 = policy("1.0.0", 1000), policy("2.0.0", 650)
    network = GovernanceNetwork(2, v1, rng=FixedDelayRng([-500.0, 3.0]))
    delays = network.broadcast_update(v2, current_time_ms=0.0, mean_delay_ms=180, std_dev_ms=40)
    assert set(delays.values()) == {10.0}
    assert all(delay >= 10.0 for delay in delays.values())
    assert all(node.cached_policy.version == "1.0.0" for node in network.nodes.values())
    network.advance_to(10.0)
    assert all(node.cached_policy.version == "2.0.0" for node in network.nodes.values())


def test_late_old_generation_cannot_overwrite_newer_snapshot():
    v1, v2, v3 = policy("1.0.0", 1000), policy("2.0.0", 650), policy("3.0.0", 500)
    network = GovernanceNetwork(1, v1, rng=FixedDelayRng([50.0, 10.0]))
    network.broadcast_update(v2, current_time_ms=0.0)
    network.broadcast_update(v3, current_time_ms=1.0)
    network.advance_to(11.0)
    node = network.nodes["node_01"]
    assert node.cached_policy.version == "3.0.0"
    network.advance_to(50.0)
    assert node.cached_policy.version == "3.0.0"
    assert node.generation == 2


def test_node_snapshot_rejects_older_generation_directly():
    v1, v2 = policy("1.0.0", 1000), policy("2.0.0", 650)
    node = DistributedNode("node_01", v2, generation=2)
    assert not node.apply_policy_update(v1, generation=1, at_ms=100)
    assert node.cached_policy.version == "2.0.0"
