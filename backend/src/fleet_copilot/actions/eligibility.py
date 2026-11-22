"""Code-only eligibility rules for the four actions.

Everything a proposal says (parameters, evidence, rationale, expected effect, notification text)
comes from the tenant-bound repository and the pinned as-of through this module. The model only
chooses an action type, a device and an enumerated parameter; it never writes any of the text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fleet_copilot import policy
from fleet_copilot.agent import texts
from fleet_copilot.agent.format import fmt_day, fmt_pct
from fleet_copilot.contracts import ActionType
from fleet_copilot.domain.battery import SERVICE_RECOMMENDED
from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.domain.windows import is_stale, window_start
from fleet_copilot.fleet import queries
from fleet_copilot.repository import Device, FleetRepository, NotFound, Snapshot
from fleet_copilot.timeutil import iso_z

Topic = Literal["low_disk", "memory_pressure", "failing_check", "battery_service"]
Component = Literal["memory", "storage"]

TOPICS: tuple[str, ...] = ("low_disk", "memory_pressure", "failing_check", "battery_service")
COMPONENTS: tuple[str, ...] = ("memory", "storage")
_GIB = 2**30

_SAFE_HOSTNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")


def display_hostname(hostname: str, device_id: str) -> str:
    """The device's hostname if it is a plain hostname, else its serial (the device id).

    A hostname is device-reported data. Anything that is not a plain hostname (spaces, slashes, a URL, a
    sentence, markup) is never spliced into text that an approver or an employee reads.
    """
    return hostname if _SAFE_HOSTNAME.fullmatch(hostname or "") else device_id


_TEMPLATES: dict[str, str] = {
    "low_disk": (
        "Your device {hostname} is low on disk space ({free_pct}% free). "
        "IT may contact you to help free up space."
    ),
    "memory_pressure": (
        "Your device {hostname} is running with high memory use ({used_pct}% used). "
        "IT may contact you about it."
    ),
    "failing_check": (
        "Your device {hostname} is failing the {check_id} compliance check. "
        "IT may contact you to fix it."
    ),
    "battery_service": (
        "The battery in your device {hostname} reports Service Recommended. "
        "IT may contact you about a replacement."
    ),
}


@dataclass(frozen=True)
class Eligible:
    action_type: ActionType
    device_id: str
    target_label: str  # hostname
    params: dict[str, str]  # canonical, code-derived
    refs: tuple[str, ...]  # tenant evidence refs, 1..MAX_REFS_PER_FINDING
    rationale: str  # code-written, numbers from data
    expected_effect: str  # code-written, ends with "(simulated)"
    employee_id: str | None = None  # notifications only; never shown to the model


@dataclass(frozen=True)
class Ineligible:
    reason: Literal["not_eligible", "insufficient_evidence", "not_found"]
    detail: str  # code-written, safe to show


def label(device: Device) -> str:
    """How a device is named in any text built here."""
    return display_hostname(device.hostname, device.device_id)


def _size_label(gib: int) -> str:
    return f"{gib // 1024} TiB" if gib >= 1024 and gib % 1024 == 0 else f"{gib} GiB"


def upgrade_target(component: Component, current_bytes: int) -> tuple[str, str] | None:
    """The (current, target) sizes of an upgrade, binary units, or None when no larger size exists."""
    if current_bytes <= 0:
        return None
    current = round(current_bytes / _GIB)
    if component == "memory":
        return _size_label(current), _size_label(current * policy.MEMORY_UPGRADE_FACTOR)
    larger = [size for size in policy.STORAGE_SIZES_GIB if size > current]
    if not larger:
        return None
    return _size_label(current), _size_label(larger[0])


def notification_message(topic: Topic, hostname: str, facts: dict[str, str]) -> str:
    """The fixed notification text for a topic; `hostname` must already be a `display_hostname`."""
    return _TEMPLATES[topic].format(hostname=hostname, **facts)


def _cap(refs: Sequence[str]) -> tuple[str, ...]:
    """At most MAX_REFS_PER_FINDING refs: the first and the most recent ones."""
    unique = list(dict.fromkeys(refs))
    if len(unique) <= policy.MAX_REFS_PER_FINDING:
        return tuple(unique)
    return (unique[0], *unique[-(policy.MAX_REFS_PER_FINDING - 1) :])


def _no(detail: str) -> Ineligible:
    return Ineligible("not_eligible", detail)


def _missing(name: str) -> Ineligible:
    return Ineligible("insufficient_evidence", f"{name} is required")


def _stale_or_missing(snap: Snapshot | None, as_of: datetime, what: str) -> Ineligible | None:
    if snap is None:
        return Ineligible("insufficient_evidence", f"There is no {what} reading for this device.")
    if is_stale(snap.collected_at, as_of):
        return Ineligible(
            "insufficient_evidence",
            f"The latest {what} reading ({fmt_day(iso_z(snap.collected_at))}) is older than "
            f"{policy.STALE_HOURS} hours.",
        )
    return None


def check(
    repo: FleetRepository,
    as_of: datetime,
    action_type: ActionType,
    device_id: str,
    *,
    check_id: str | None = None,
    component: str | None = None,
    topic: str | None = None,
    employee_id: str | None = None,
) -> Eligible | Ineligible:
    """Eligibility of one action on one device. Every text in the result names the device by `display_hostname`.

    `employee_id` is the recipient a notification claims to be for. The recipient is always the employee the
    tenant-bound device row names, so a claim that differs is refused as the same uniform `not_found` as an
    unknown or foreign device, whatever else is true of the device.
    """
    try:
        device = repo.get_device(device_id)
    except NotFound:
        return Ineligible("not_found", texts.NOT_FOUND)
    if employee_id is not None and employee_id != device.employee_id:
        return Ineligible("not_found", texts.NOT_FOUND)
    if action_type == "remediation_ticket":
        return _ticket(repo, as_of, device, check_id)
    if action_type == "replacement_flag":
        return _replacement(repo, as_of, device)
    if action_type == "upgrade_order":
        return _upgrade(repo, as_of, device, component)
    if action_type == "employee_notification":
        return _notification(repo, as_of, device, topic, check_id)
    return _no("That action type is not supported.")


def _ticket(
    repo: FleetRepository, as_of: datetime, device: Device, check_id: str | None
) -> Eligible | Ineligible:
    if not check_id:
        return _missing("check_id")
    latest = repo.latest(device.device_id)
    if (problem := _stale_or_missing(latest, as_of, "compliance")) is not None:
        return problem
    assert latest is not None
    result = latest.compliance.get(check_id)
    if result is None:
        return _no(f"The device does not report a {check_id} check.")
    if result.status != "fail":
        return _no(f"{check_id} is not failing at the latest reading.")
    path = f"compliance[{check_id}]"
    window = repo.snapshots(
        device.device_id, since=window_start(as_of, policy.COMPLIANCE_WINDOW_DAYS)
    )
    first = next(
        (
            s
            for s in window
            if s.compliance.get(check_id) and s.compliance[check_id].status == "fail"
        ),
        latest,
    )
    refs = _cap([str(latest.ref(path)), str(first.ref(path))])
    since = f"; the first failing reading in the last {policy.COMPLIANCE_WINDOW_DAYS} days is {fmt_day(iso_z(first.collected_at))}"
    return Eligible(
        "remediation_ticket",
        device.device_id,
        label(device),
        {"check_id": check_id, "severity": result.severity},
        refs,
        f"{check_id} reports fail with {result.severity} severity at the latest reading "
        f"({fmt_day(iso_z(latest.collected_at))}){since}.",
        f"A {result.severity}-severity remediation ticket for {check_id} on {label(device)} "
        "would be opened (simulated)",
    )


def _replacement(repo: FleetRepository, as_of: datetime, device: Device) -> Eligible | Ineligible:
    since = window_start(as_of, policy.BATTERY_WINDOW_DAYS)
    readings = [
        s
        for s in repo.snapshots(device.device_id, since=since)
        if s.battery is not None and s.battery.present
    ]
    if not readings:
        return _no("The device has no battery readings.")
    if (problem := _stale_or_missing(readings[-1], as_of, "battery")) is not None:
        return problem
    run: list[Snapshot] = []
    for snap in reversed(readings):
        assert snap.battery is not None
        if snap.battery.condition != SERVICE_RECOMMENDED:
            break
        run.append(snap)
    run.reverse()
    if len(run) < policy.REPLACEMENT_MIN_SNAPSHOTS:
        needed = f"at least {policy.REPLACEMENT_MIN_SNAPSHOTS} consecutive readings are needed."
        if not run:
            return _no(
                f"The latest battery reading does not report {SERVICE_RECOMMENDED}; {needed}"
            )
        return _no(
            f"Only the last {len(run)} battery {'reading reports' if len(run) == 1 else 'readings report'} "
            f"{SERVICE_RECOMMENDED}; {needed}"
        )
    refs = _cap([str(run[0].ref("battery")), *(str(s.ref("battery")) for s in run[-5:])])
    return Eligible(
        "replacement_flag",
        device.device_id,
        label(device),
        {"reason": "battery_service_recommended"},
        refs,
        f"The last {len(run)} battery readings, from {fmt_day(iso_z(run[0].collected_at))} to "
        f"{fmt_day(iso_z(run[-1].collected_at))}, report the condition {SERVICE_RECOMMENDED}.",
        f"{label(device)} would be flagged for replacement because of its battery (simulated)",
    )


def _upgrade(
    repo: FleetRepository, as_of: datetime, device: Device, component: str | None
) -> Eligible | Ineligible:
    if not component:
        return _missing("component")
    if component not in COMPONENTS:
        return Ineligible("insufficient_evidence", "component must be memory or storage")
    latest = repo.latest(device.device_id)
    if (problem := _stale_or_missing(latest, as_of, component)) is not None:
        return problem
    assert latest is not None
    days = policy.UPGRADE_TREND_WINDOW_DAYS
    if component == "memory":
        result = queries.memory_trend(
            repo,
            as_of,
            window_days=days,
            threshold_pct=policy.MEMORY_USED_PCT,
            only_devices=[device.device_id],
        )
        size_bytes = latest.mem_total_bytes
        rule = f"memory use at or above {policy.MEMORY_USED_PCT:g}%"
    else:
        result = queries.disk_trend(
            repo,
            as_of,
            window_days=days,
            threshold_pct=policy.LOW_DISK_FREE_PCT,
            only_devices=[device.device_id],
        )
        size_bytes = latest.disk_size_bytes
        rule = f"free space below {policy.LOW_DISK_FREE_PCT:g}%"
    if device.device_id in result.excluded.get("insufficient_history", ()):
        return Ineligible(
            "insufficient_evidence",
            f"There are not enough readings in the last {days} days to show a trend.",
        )
    finding = next((f for f in result.findings if f.device_id == device.device_id), None)
    if finding is None or finding.values["trend"] != "sustained":
        trend = "none" if finding is None else finding.values["trend"]
        return _no(f"{rule} was not sustained over the last {days} days (trend: {trend}).")
    target = upgrade_target(component, size_bytes)  # type: ignore[arg-type]
    if target is None:
        return _no("There is no larger standard size for this device.")
    current, new = target
    values = finding.values
    latest_text = (
        f"{fmt_pct(values['latest_value'])} used"
        if component == "memory"
        else f"{fmt_pct(values['latest_value'])} free"
    )
    return Eligible(
        "upgrade_order",
        device.device_id,
        label(device),
        {"component": component, "current": current, "target": new},
        _cap(finding.refs),
        f"{rule[0].upper()}{rule[1:]} on {values['breached']} of {values['snapshots']} readings in "
        f"the last {days} days; the latest reading is {latest_text}. Current {component} size is "
        f"{current}.",
        f"An order to upgrade {component} on {label(device)} from {current} to {new} "
        "would be placed (simulated)",
    )


def _notification(
    repo: FleetRepository,
    as_of: datetime,
    device: Device,
    topic: str | None,
    check_id: str | None,
) -> Eligible | Ineligible:
    if not topic:
        return _missing("topic")
    if topic not in TOPICS:
        return Ineligible("insufficient_evidence", f"topic must be one of: {', '.join(TOPICS)}")
    latest = repo.latest(device.device_id, require_battery=topic == "battery_service")
    what = {"low_disk": "disk", "memory_pressure": "memory", "failing_check": "compliance"}.get(
        topic, "battery"
    )
    if (problem := _stale_or_missing(latest, as_of, what)) is not None:
        return problem
    assert latest is not None
    when = fmt_day(iso_z(latest.collected_at))
    facts: dict[str, str] = {}
    if topic == "low_disk":
        pct = free_pct(latest.disk_size_bytes, latest.disk_available_bytes)
        if pct >= policy.LOW_DISK_FREE_PCT:
            return _no(
                f"Free space is not below {policy.LOW_DISK_FREE_PCT:g}% at the latest reading."
            )
        facts = {"free_pct": f"{pct:.1f}"}
        ref = str(latest.ref("disk"))
        rationale = (
            f"Free space on the primary volume is {fmt_pct(pct)} at the latest reading ({when}); "
            f"the rule is below {policy.LOW_DISK_FREE_PCT:g}%."
        )
    elif topic == "memory_pressure":
        pct = used_pct(latest.mem_total_bytes, latest.mem_used_bytes)
        if pct < policy.MEMORY_USED_PCT:
            return _no(
                f"Memory use is not at or above {policy.MEMORY_USED_PCT:g}% at the latest reading."
            )
        facts = {"used_pct": f"{pct:.1f}"}
        ref = str(latest.ref("memory"))
        rationale = (
            f"Memory use is {fmt_pct(pct)} at the latest reading ({when}); "
            f"the rule is at or above {policy.MEMORY_USED_PCT:g}%."
        )
    elif topic == "failing_check":
        failing = sorted(c for c, r in latest.compliance.items() if r.status == "fail")
        chosen = check_id or (failing[0] if failing else None)
        if chosen is None or chosen not in failing:
            return _no("No compliance check is failing at the latest reading.")
        facts = {"check_id": chosen}
        ref = str(latest.ref(f"compliance[{chosen}]"))
        rationale = f"{chosen} reports fail at the latest reading ({when})."
    else:
        battery = latest.battery
        if battery is None or not battery.present or battery.condition != SERVICE_RECOMMENDED:
            return _no(f"The latest battery reading does not report {SERVICE_RECOMMENDED}.")
        ref = str(latest.ref("battery"))
        rationale = (
            f"The latest battery reading ({when}) reports the condition {SERVICE_RECOMMENDED}."
        )
    message = notification_message(topic, label(device), facts)  # type: ignore[arg-type]
    return Eligible(
        "employee_notification",
        device.device_id,
        label(device),
        {"topic": topic, "message": message},
        (ref,),
        rationale,
        f"A notification with the approved text would be sent to the employee assigned to "
        f"{label(device)} (simulated)",
        employee_id=device.employee_id,
    )
