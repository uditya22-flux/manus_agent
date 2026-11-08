"""Benchmark scenario definitions for reproducible policy-drift experiments."""
from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from core.models import Action, ActionType, Policy, PolicyRule, RuleSeverity


def get_corporate_procurement_scenario() -> Tuple[Policy, Policy, List[Action]]:
    """Return the defined $1,000-to-$650 corporate travel policy transition."""
    policy_v1 = Policy(
        version="1.0.0",
        domain="procurement",
        rules={
            "R1": PolicyRule(
                rule_id="R1",
                target_tool="*",
                parameter="aggregate_budget",
                operator="<=",
                threshold=Decimal("1000.00"),
                severity=RuleSeverity.CRITICAL,
            )
        },
    )
    policy_v2 = Policy(
        version="2.0.0",
        domain="procurement",
        rules={
            "R1": PolicyRule(
                rule_id="R1",
                target_tool="*",
                parameter="aggregate_budget",
                operator="<=",
                threshold=Decimal("650.00"),
                severity=RuleSeverity.CRITICAL,
            )
        },
    )
    plan = [
        Action(step_id=1, tool_name="search_flight_schedules", arguments={"query": "COK-DEL"}, action_type=ActionType.REVERSIBLE, duration_ms=35.0),
        Action(step_id=2, tool_name="charge_corporate_card_flight", arguments={"vendor": "AirIndia", "cost": Decimal("450.00")}, action_type=ActionType.IRREVERSIBLE, duration_ms=90.0),
        Action(step_id=3, tool_name="check_hotel_availability", arguments={"city": "Delhi", "nights": 3}, action_type=ActionType.REVERSIBLE, duration_ms=35.0),
        Action(step_id=4, tool_name="reserve_conference_hotel", arguments={"vendor": "Hyatt", "cost": Decimal("300.00")}, action_type=ActionType.IRREVERSIBLE, duration_ms=90.0),
        Action(step_id=5, tool_name="dispatch_calendar_invites", arguments={"attendees": ["team@domain.com"]}, action_type=ActionType.IRREVERSIBLE, duration_ms=25.0),
    ]
    return policy_v1, policy_v2, plan
