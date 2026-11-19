from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fleet_copilot.domain.battery import BatteryReading, battery_signals
from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.domain.osversion import macos_older_than
from fleet_copilot.domain.trends import classify_breach_trend, classify_compliance
from fleet_copilot.domain.windows import is_stale, window_start
from fleet_copilot.repository import Device, FleetRepository, Snapshot
from fleet_copilot.timeutil import iso_z


@dataclass(frozen=True)
class Finding:
    device_id: str
    hostname: str
    values: dict[str, Any]
    refs: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    findings: tuple[Finding, ...]
    population: int  # distinct devices evaluated
    as_of: datetime
    rule: str  # the disclosed rule, in words
    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)  # reason -> device ids
    stale: tuple[str, ...] = ()  # device ids whose reading is older than the stale limit
    missing_latest: tuple[str, ...] = ()  # device ids whose latest snapshot lacks the block


def _latest_pairs(repo: FleetRepository, devices: list[Device]) -> list[tuple[Device, Snapshot]]:
    pairs = []
    for device in devices:
        snapshot = repo.latest(device.device_id)
        if snapshot is not None:
            pairs.append((device, snapshot))
    return pairs


def _stale(pairs: list[tuple[Device, Snapshot]], as_of: datetime) -> tuple[str, ...]:
    return tuple(sorted(d.device_id for d, s in pairs if is_stale(s.collected_at, as_of)))


def low_disk(repo: FleetRepository, as_of: datetime, threshold_pct: float = 10.0) -> QueryResult:
    pairs = _latest_pairs(repo, repo.list_devices())
    findings = []
    for device, snap in pairs:
        pct = free_pct(snap.disk_size_bytes, snap.disk_available_bytes)
        if pct < threshold_pct:
            findings.append(
                Finding(
                    device.device_id, device.hostname,
                    {
                        "free_pct": pct,
                        "size_bytes": snap.disk_size_bytes,
                        "available_bytes": snap.disk_available_bytes,
                        "collected_at": iso_z(snap.collected_at),
                    },
                    (str(snap.ref("disk")),),
                )
            )
    findings.sort(key=lambda f: (f.values["free_pct"], f.device_id))
    return QueryResult(
        tuple(findings), len(pairs), as_of,
        f"primary volume free space < {threshold_pct:g}% at the latest snapshot",
        stale=_stale(pairs, as_of),
    )


def macos_below(repo: FleetRepository, as_of: datetime, target: str = "15") -> QueryResult:
    pairs = _latest_pairs(repo, repo.list_devices())
    findings, out_of_scope, unknown, in_scope = [], [], [], 0
    for device, snap in pairs:
        verdict = macos_older_than(snap.os_name, snap.os_version, target)
        if verdict == "out_of_scope":
            out_of_scope.append(device.device_id)
            continue
        in_scope += 1
        if verdict == "unknown":
            unknown.append(device.device_id)
        elif verdict == "older":
            findings.append(
                Finding(
                    device.device_id, device.hostname,
                    {"os_version": snap.os_version, "collected_at": iso_z(snap.collected_at)},
                    (str(snap.ref("os")),),
                )
            )
    excluded: dict[str, tuple[str, ...]] = {}
    if out_of_scope:
        excluded["out_of_scope"] = tuple(sorted(out_of_scope))
    if unknown:
        excluded["unknown_version"] = tuple(sorted(unknown))
    findings.sort(key=lambda f: f.device_id)
    return QueryResult(
        tuple(findings), in_scope, as_of, f"macOS devices with version < {target}",
        excluded=excluded, stale=_stale(pairs, as_of),
    )


def compliance_failures(
    repo: FleetRepository,
    as_of: datetime,
    *,
    check_id: str | None = None,
    severity: str | None = None,
    form_factor: str | None = None,
) -> QueryResult:
    devices = [d for d in repo.list_devices() if form_factor is None or d.form_factor == form_factor]
    pairs = _latest_pairs(repo, devices)
    findings = []
    for device, snap in pairs:
        failing = [
            (cid, check)
            for cid, check in sorted(snap.compliance.items())
            if check.status == "fail"
            and (check_id is None or cid == check_id)
            and (severity is None or check.severity == severity)
        ]
        if failing:
            findings.append(
                Finding(
                    device.device_id, device.hostname,
                    {
                        "failing": [{"check_id": cid, "severity": c.severity} for cid, c in failing],
                        "collected_at": iso_z(snap.collected_at),
                    },
                    tuple(str(snap.ref(f"compliance[{cid}]")) for cid, _ in failing),
                )
            )
    parts = ["failing check at the latest snapshot"]
    if check_id:
        parts.append(f"check={check_id}")
    if severity:
        parts.append(f"severity={severity}")
    if form_factor:
        parts.append(f"form_factor={form_factor}")
    findings.sort(key=lambda f: f.device_id)
    return QueryResult(tuple(findings), len(pairs), as_of, ", ".join(parts), stale=_stale(pairs, as_of))


def _breach_trend(
    repo: FleetRepository,
    as_of: datetime,
    window_days: int,
    rule: str,
    path: str,
    breached: Callable[[Snapshot], bool],
    value: Callable[[Snapshot], float],
) -> QueryResult:
    since = window_start(as_of, window_days)
    devices = repo.list_devices()
    findings, insufficient, stale = [], [], []
    for device in devices:
        snaps = repo.snapshots(device.device_id, since=since)
        if snaps and is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        flags = [breached(s) for s in snaps]
        trend = classify_breach_trend(flags)
        if trend == "insufficient":
            insufficient.append(device.device_id)
        elif trend != "none":
            findings.append(
                Finding(
                    device.device_id, device.hostname,
                    {
                        "trend": trend,
                        "breached": sum(flags),
                        "snapshots": len(flags),
                        "latest_value": value(snaps[-1]),
                        "window_days": window_days,
                    },
                    # every breached snapshot, plus the latest one so `latest_value` is always cited
                    tuple(
                        dict.fromkeys(
                            [str(s.ref(path)) for s, flag in zip(snaps, flags, strict=True) if flag]
                            + [str(snaps[-1].ref(path))]
                        )
                    ),
                )
            )
    findings.sort(key=lambda f: f.device_id)
    excluded = {"insufficient_history": tuple(sorted(insufficient))} if insufficient else {}
    return QueryResult(
        tuple(findings), len(devices), as_of, rule, excluded=excluded, stale=tuple(sorted(stale))
    )


def memory_trend(
    repo: FleetRepository, as_of: datetime, window_days: int = 7, threshold_pct: float = 85.0
) -> QueryResult:
    def used(s: Snapshot) -> float:
        return used_pct(s.mem_total_bytes, s.mem_used_bytes)

    return _breach_trend(
        repo, as_of, window_days,
        f"memory used >= {threshold_pct:g}% over the last {window_days} days",
        "memory", lambda s: used(s) >= threshold_pct, used,
    )


def disk_trend(
    repo: FleetRepository, as_of: datetime, window_days: int = 7, threshold_pct: float = 10.0
) -> QueryResult:
    def free(s: Snapshot) -> float:
        return free_pct(s.disk_size_bytes, s.disk_available_bytes)

    return _breach_trend(
        repo, as_of, window_days,
        f"primary volume free space < {threshold_pct:g}% over the last {window_days} days",
        "disk", lambda s: free(s) < threshold_pct, free,
    )


def compliance_trend(
    repo: FleetRepository, as_of: datetime, *, check_id: str, window_days: int = 30
) -> QueryResult:
    since = window_start(as_of, window_days)
    devices = repo.list_devices()
    findings, insufficient, stale = [], [], []
    for device in devices:
        snaps = [s for s in repo.snapshots(device.device_id, since=since) if check_id in s.compliance]
        if snaps and is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        trend = classify_compliance([s.compliance[check_id].status for s in snaps])
        if trend.kind == "insufficient":
            insufficient.append(device.device_id)
            continue
        if trend.kind == "passing":
            continue
        path = f"compliance[{check_id}]"
        if trend.change_index is None:  # persistent failure: first and latest observation
            refs = (snaps[0].ref(path), snaps[-1].ref(path))
            change_at = None
        else:  # transition or recovery: last of the old state, first of the new, latest
            refs = (snaps[trend.change_index - 1].ref(path), snaps[trend.change_index].ref(path), snaps[-1].ref(path))
            change_at = iso_z(snaps[trend.change_index].collected_at)
        findings.append(
            Finding(
                device.device_id, device.hostname,
                {"kind": trend.kind, "change_at": change_at, "snapshots": len(snaps)},
                tuple(dict.fromkeys(str(r) for r in refs)),
            )
        )
    findings.sort(key=lambda f: f.device_id)
    excluded = {"insufficient_history": tuple(sorted(insufficient))} if insufficient else {}
    return QueryResult(
        tuple(findings), len(devices), as_of,
        f"{check_id} over the last {window_days} days", excluded=excluded, stale=tuple(sorted(stale)),
    )


def battery_overview(repo: FleetRepository, as_of: datetime, window_days: int = 30) -> QueryResult:
    since = window_start(as_of, window_days)
    laptops = [d for d in repo.list_devices() if d.form_factor == "laptop"]
    findings, no_reading, missing_latest, stale = [], [], [], []
    for device in laptops:
        newest = repo.latest(device.device_id)
        if newest is not None and newest.battery is None:
            missing_latest.append(device.device_id)
        snaps = [
            s for s in repo.snapshots(device.device_id, since=since)
            if s.battery is not None and s.battery.present
        ]
        if not snaps:
            no_reading.append(device.device_id)
            continue
        if is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        signals = battery_signals(
            [
                BatteryReading(s.collected_at, s.battery.condition, s.battery.cycle_count, s.battery.full_charge_capacity)
                for s in snaps
            ]
        )
        declining = signals.capacity_change_pct is not None and signals.capacity_change_pct < -1.0
        if declining or signals.service_streak > 0:
            # cite what each value rests on: the two readings the capacity change was measured between,
            # the latest reading (cycles, condition) and the snapshot where the service streak began
            with_capacity = [s for s in snaps if s.battery.full_charge_capacity is not None]
            cited = [with_capacity[0], with_capacity[-1]] if with_capacity else []
            cited.append(snaps[-1])
            if signals.service_streak > 0:
                cited.append(snaps[len(snaps) - signals.service_streak])
            findings.append(
                Finding(
                    device.device_id, device.hostname,
                    {
                        "capacity_change_pct": signals.capacity_change_pct,
                        "cycles_latest": signals.cycles_latest,
                        "latest_condition": signals.latest_condition,
                        "service_streak": signals.service_streak,
                        "first_service_at": None if signals.first_service_at is None else iso_z(signals.first_service_at),
                        "readings": signals.readings,
                        "latest_reading_at": iso_z(snaps[-1].collected_at),
                    },
                    tuple(dict.fromkeys(str(s.ref("battery")) for s in cited)),
                )
            )
    findings.sort(key=lambda f: f.device_id)
    excluded = {"no_reading": tuple(sorted(no_reading))} if no_reading else {}
    return QueryResult(
        tuple(findings), len(laptops), as_of,
        f"laptops with capacity down more than 1% or a Service Recommended flag over the last {window_days} days",
        excluded=excluded, stale=tuple(sorted(stale)), missing_latest=tuple(sorted(missing_latest)),
    )
