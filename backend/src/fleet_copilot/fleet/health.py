"""One ranked overview of a company's fleet across battery, disk, memory and compliance.

It reuses the trend queries and their classifications; it only decides how serious each finding is and
puts them in one list. A finding is one device in one category (and one check, for compliance):

  * battery: `domain.battery.battery_severity` (see there);
  * disk and memory: a sustained breach is `act_soon`; an onset or intermittent breach is `watch`;
    recovering and isolated breaches are not reported;
  * compliance: a check that fails at the latest snapshot with high or critical severity is `act_soon`;
    a pass-to-fail transition inside the window is `watch`. Other persistent failures are not reported.

Nothing here writes words for a reader: the values carry the numbers and the composer writes the text.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any

from fleet_copilot import policy
from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.domain.trends import MIN_SNAPSHOTS
from fleet_copilot.domain.windows import is_stale, window_start
from fleet_copilot.fleet import queries as q
from fleet_copilot.fleet.queries import Finding, QueryResult
from fleet_copilot.repository import Device, FleetRepository, Snapshot
from fleet_copilot.timeutil import iso_z

CATEGORY_ORDER = ("battery", "disk", "memory", "compliance")
SEVERITY_ORDER = ("act_soon", "watch", "ok")
CHECK_IDS = ("disk_encryption", "screen_lock", "os_up_to_date")
_HIGH = frozenset({"high", "critical"})
_BREACH_SEVERITY = {"sustained": "act_soon", "onset": "watch", "intermittent": "watch"}


def rank_key(values: dict[str, Any], device_id: str) -> tuple[int, int, str, str]:
    """Most urgent first, then category order, then device id (then check)."""
    return (
        SEVERITY_ORDER.index(values["severity"]),
        CATEGORY_ORDER.index(values["category"]),
        device_id,
        str(values.get("check_id") or ""),
    )


def _series(
    repo: FleetRepository, device_id: str, since: datetime, value: Any
) -> list[dict[str, Any]]:
    return [
        {"at": iso_z(s.collected_at), "value": round(float(value(s)), 3)}
        for s in repo.snapshots(device_id, since=since)
    ]


def _breach_findings(
    result: q.QueryResult,
    repo: FleetRepository,
    since: datetime,
    category: str,
    threshold: float,
    value: Any,
) -> list[Finding]:
    found = []
    for f in result.findings:
        severity = _BREACH_SEVERITY.get(f.values["trend"])
        if severity is None:
            continue
        found.append(
            Finding(
                f.device_id,
                f.hostname,
                {
                    **f.values,
                    "category": category,
                    "severity": severity,
                    "threshold": threshold,
                    "series": _series(repo, f.device_id, since, value),
                },
                f.refs,
            )
        )
    return found


def _compliance_findings(
    repo: FleetRepository, as_of: datetime, since: datetime, window_days: int, only: Any
) -> list[Finding]:
    found = []
    for check in CHECK_IDS:
        result = q.compliance_trend(
            repo, as_of, check_id=check, window_days=window_days, only_devices=only
        )
        for f in result.findings:
            kind = f.values["kind"]
            if kind not in ("persistent_failure", "transition"):
                continue
            latest = repo.latest(f.device_id)
            severity_label = (
                latest.compliance[check].severity if latest and check in latest.compliance else ""
            )
            if severity_label in _HIGH:
                severity = "act_soon"
            elif kind == "transition":
                severity = "watch"
            else:
                continue
            series = [
                {
                    "at": iso_z(s.collected_at),
                    "value": 1.0 if s.compliance[check].status == "pass" else 0.0,
                }
                for s in repo.snapshots(f.device_id, since=since)
                if check in s.compliance
            ]
            found.append(
                Finding(
                    f.device_id,
                    f.hostname,
                    {
                        **f.values,
                        "category": "compliance",
                        "severity": severity,
                        "check_id": check,
                        "check_severity": severity_label,
                        "series": series,
                    },
                    f.refs,
                )
            )
    return found


def fleet_health(
    repo: FleetRepository,
    as_of: datetime,
    window_days: int = policy.BATTERY_WINDOW_DAYS,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    """Ranked findings across battery, disk, memory and compliance over the trailing window."""
    since = window_start(as_of, window_days)
    only = None if only_devices is None else set(only_devices)
    devices: list[Device] = [d for d in repo.list_devices() if only is None or d.device_id in only]
    battery = q.battery_overview(repo, as_of, window_days, only_devices=only)
    disk = q.disk_trend(repo, as_of, window_days, policy.LOW_DISK_FREE_PCT, only_devices=only)
    memory = q.memory_trend(repo, as_of, window_days, policy.MEMORY_USED_PCT, only_devices=only)

    findings: list[Finding] = [
        Finding(f.device_id, f.hostname, {**f.values, "category": "battery"}, f.refs)
        for f in battery.findings
        if f.values["severity"] != "ok"
    ]
    findings += _breach_findings(
        disk,
        repo,
        since,
        "disk",
        policy.LOW_DISK_FREE_PCT,
        lambda s: free_pct(s.disk_size_bytes, s.disk_available_bytes),
    )
    findings += _breach_findings(
        memory,
        repo,
        since,
        "memory",
        policy.MEMORY_USED_PCT,
        lambda s: used_pct(s.mem_total_bytes, s.mem_used_bytes),
    )
    findings += _compliance_findings(repo, as_of, since, window_days, only)
    findings.sort(key=lambda f: rank_key(f.values, f.device_id))

    # Devices with too little history for any trend are said so, once.
    thin: dict[str, tuple[str, str]] = {}
    stale: list[str] = []
    for device in devices:
        snaps: list[Snapshot] = repo.snapshots(device.device_id, since=since)
        if snaps and is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        if len(snaps) < MIN_SNAPSHOTS:
            n = len(snaps)
            thin[device.device_id] = (
                device.hostname,
                "No snapshots in the window"
                if n == 0
                else f"Only {n} {'snapshot' if n == 1 else 'snapshots'} in the window",
            )
    return QueryResult(
        tuple(findings),
        len(devices),
        as_of,
        (
            f"battery wear against our {policy.BATTERY_SERVICE_INDEX:g}% service threshold, low disk "
            f"(under {policy.LOW_DISK_FREE_PCT:g}% free), memory pressure ({policy.MEMORY_USED_PCT:g}% "
            f"used or more) and compliance failures and changes over the last {window_days} days"
        ),
        excluded={"insufficient_history": tuple(sorted(thin))} if thin else {},
        stale=tuple(sorted(stale)),
        missing_latest=battery.missing_latest,
        excluded_detail={"insufficient_history": thin} if thin else {},
    )
