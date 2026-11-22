"""What would make a refused action possible, and what to look at instead. Code-written, never model text.

The conditions are the eligibility rules in `eligibility.py` restated in words, with every number read from
`policy.py`, so a threshold is never written down twice. `needs_for` lists them for one action type (and the
parameters the request named); `alternatives_for` offers questions the copilot can answer about the device.
"""

from __future__ import annotations

from fleet_copilot import policy
from fleet_copilot.contracts import MAX_NEEDS, ActionType, Alternative
from fleet_copilot.domain.battery import SERVICE_RECOMMENDED

_TOPIC_NEEDS = {
    "low_disk": f"Free disk under {policy.LOW_DISK_FREE_PCT:g}% at the latest reading",
    "memory_pressure": f"Memory use at or above {policy.MEMORY_USED_PCT:g}% at the latest reading",
    "failing_check": "A compliance check failing at the latest reading",
    "battery_service": f"Battery reporting '{SERVICE_RECOMMENDED}' at the latest reading",
}
_FRESH = f"A reading from the last {policy.STALE_HOURS} hours"
_MEMORY_SUSTAINED = (
    f"Memory use at or above {policy.MEMORY_USED_PCT:g}% sustained over the last "
    f"{policy.UPGRADE_TREND_WINDOW_DAYS} days"
)
_STORAGE_SUSTAINED = (
    f"Free disk under {policy.LOW_DISK_FREE_PCT:g}% sustained over the last "
    f"{policy.UPGRADE_TREND_WINDOW_DAYS} days"
)


def _ticket_needs(check_id: str | None) -> list[str]:
    failing = (
        f"The {check_id} check failing at the latest reading"
        if check_id
        else "A compliance check failing at the latest reading"
    )
    return [failing, _FRESH]


def _replacement_needs() -> list[str]:
    return [
        f"Battery reports '{SERVICE_RECOMMENDED}' in {policy.REPLACEMENT_MIN_SNAPSHOTS} or more "
        "consecutive readings",
        f"A battery reading from the last {policy.STALE_HOURS} hours",
    ]


def _upgrade_needs(component: str | None) -> list[str]:
    if component == "memory":
        return [_MEMORY_SUSTAINED, _FRESH]
    if component == "storage":
        return [_STORAGE_SUSTAINED, _FRESH]
    return [_MEMORY_SUSTAINED, _STORAGE_SUSTAINED]


def _notification_needs(topic: str | None) -> list[str]:
    if topic in _TOPIC_NEEDS:
        return [_TOPIC_NEEDS[topic], _FRESH]
    return list(_TOPIC_NEEDS.values())


def needs_for(
    action_type: ActionType,
    reason: str,
    *,
    check_id: str | None = None,
    component: str | None = None,
    topic: str | None = None,
) -> list[str]:
    """The conditions that would make this action possible, at most `MAX_NEEDS`, one line each."""
    if reason == "already_done":
        needs = [
            "No action of this kind carried out for the device in the last "
            f"{policy.ACTION_REPEAT_WINDOW_DAYS} days"
        ]
    elif reason == "too_many_pending":
        needs = [
            f"Fewer than {policy.MAX_PENDING_PER_TENANT} proposals waiting for a decision",
            "Approve or reject some of the waiting proposals first",
        ]
    elif action_type == "remediation_ticket":
        needs = _ticket_needs(check_id)
    elif action_type == "replacement_flag":
        needs = _replacement_needs()
    elif action_type == "upgrade_order":
        needs = _upgrade_needs(component)
    else:
        needs = _notification_needs(topic)
    return needs[:MAX_NEEDS]


def _second_alternative(
    action_type: ActionType, component: str | None, topic: str | None
) -> tuple[str, str] | None:
    """The one subject question that fits the action, besides the device's own history."""
    if action_type == "replacement_flag" or topic == "battery_service":
        return ("Check battery wear", "Which batteries are wearing?")
    if action_type == "remediation_ticket" or topic == "failing_check":
        return ("Failing compliance checks", "Which devices are failing compliance checks?")
    if component == "memory" or topic == "memory_pressure":
        return ("Memory pressure", "Which devices are consistently RAM-constrained?")
    return ("Devices low on disk", "Which devices are low on disk space?")


def alternatives_for(
    action_type: ActionType, label: str, *, component: str | None = None, topic: str | None = None
) -> list[Alternative]:
    """Questions to ask instead, naming the device by `label` (its display hostname). At most four."""
    options = [("Show its 30-day health", f"Show the 30-day history of {label}")]
    second = _second_alternative(action_type, component, topic)
    if second is not None:
        options.append(second)
    return [Alternative(label=name, request=request) for name, request in options]
