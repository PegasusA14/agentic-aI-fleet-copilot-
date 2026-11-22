from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fleet_copilot import policy
from fleet_copilot.domain.battery import (
    BatteryReading,
    BatterySignals,
    battery_severity,
    battery_signals,
    capacity_index,
    fit_capacity_index,
)
from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.domain.osversion import macos_older_than
from fleet_copilot.domain.trends import MIN_SNAPSHOTS, classify_breach_trend, classify_compliance
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
    # reason -> device id -> (hostname, why this device was not covered, in words). Same devices as
    # `excluded`; presentation only, so it never reaches the model.
    excluded_detail: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)


def _snapshot_count(n: int) -> str:
    """Why a trend skipped a device: how little history it had."""
    if n == 0:
        return "No snapshots in the window"
    return f"Only {n} {'snapshot' if n == 1 else 'snapshots'} in the window"


def _not_a_mac(os_name: str, os_version: str) -> str:
    """`Windows 11, not a Mac`: the device's own OS name and its leading version token."""
    token = os_version.split()[0] if os_version.split() else ""
    label = f"{os_name} {token}".strip()
    return f"{label}, not a Mac" if os_name else "Not a Mac"


_SAFE_VERSION = re.compile(r"[A-Za-z0-9._ -]{1,20}")


def _unrecognised(os_version: str) -> str:
    safe = os_version if _SAFE_VERSION.fullmatch(os_version) else ""
    return f"macOS version {safe!r} not recognised" if safe else "macOS version not recognised"


def _latest_pairs(repo: FleetRepository, devices: list[Device]) -> list[tuple[Device, Snapshot]]:
    pairs = []
    for device in devices:
        snapshot = repo.latest(device.device_id)
        if snapshot is not None:
            pairs.append((device, snapshot))
    return pairs


def _stale(pairs: list[tuple[Device, Snapshot]], as_of: datetime) -> tuple[str, ...]:
    return tuple(sorted(d.device_id for d, s in pairs if is_stale(s.collected_at, as_of)))


def _devices(repo: FleetRepository, only: Collection[str] | None) -> list[Device]:
    """Tenant devices, optionally narrowed to `only`. Ids outside the tenant are ignored."""
    devices = repo.list_devices()
    if only is None:
        return devices
    wanted = set(only)
    return [d for d in devices if d.device_id in wanted]


def low_disk(
    repo: FleetRepository,
    as_of: datetime,
    threshold_pct: float = policy.LOW_DISK_FREE_PCT,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    pairs = _latest_pairs(repo, _devices(repo, only_devices))
    findings = []
    for device, snap in pairs:
        pct = free_pct(snap.disk_size_bytes, snap.disk_available_bytes)
        if pct < threshold_pct:
            findings.append(
                Finding(
                    device.device_id,
                    device.hostname,
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
        tuple(findings),
        len(pairs),
        as_of,
        f"primary volume free space < {threshold_pct:g}% at the latest snapshot",
        stale=_stale(pairs, as_of),
    )


def macos_below(
    repo: FleetRepository,
    as_of: datetime,
    target: str = "15",
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    pairs = _latest_pairs(repo, _devices(repo, only_devices))
    findings, out_of_scope, unknown, in_scope = [], [], [], 0
    detail: dict[str, dict[str, tuple[str, str]]] = {}
    for device, snap in pairs:
        verdict = macos_older_than(snap.os_name, snap.os_version, target)
        if verdict == "out_of_scope":
            out_of_scope.append(device.device_id)
            detail.setdefault("out_of_scope", {})[device.device_id] = (
                device.hostname,
                _not_a_mac(snap.os_name, snap.os_version),
            )
            continue
        in_scope += 1
        if verdict == "unknown":
            unknown.append(device.device_id)
            detail.setdefault("unknown_version", {})[device.device_id] = (
                device.hostname,
                _unrecognised(snap.os_version),
            )
        elif verdict == "older":
            findings.append(
                Finding(
                    device.device_id,
                    device.hostname,
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
        tuple(findings),
        in_scope,
        as_of,
        f"macOS devices with version < {target}",
        excluded=excluded,
        stale=_stale(pairs, as_of),
        excluded_detail=detail,
    )


def compliance_failures(
    repo: FleetRepository,
    as_of: datetime,
    *,
    check_id: str | None = None,
    severity: str | None = None,
    form_factor: str | None = None,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    devices = [
        d
        for d in _devices(repo, only_devices)
        if form_factor is None or d.form_factor == form_factor
    ]
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
                    device.device_id,
                    device.hostname,
                    {
                        "failing": [
                            {"check_id": cid, "severity": c.severity} for cid, c in failing
                        ],
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
    return QueryResult(
        tuple(findings), len(pairs), as_of, ", ".join(parts), stale=_stale(pairs, as_of)
    )


def _breach_trend(
    repo: FleetRepository,
    as_of: datetime,
    devices: list[Device],
    window_days: int,
    rule: str,
    path: str,
    breached: Callable[[Snapshot], bool],
    value: Callable[[Snapshot], float],
) -> QueryResult:
    since = window_start(as_of, window_days)
    findings, insufficient, stale = [], [], []
    detail: dict[str, tuple[str, str]] = {}
    for device in devices:
        snaps = repo.snapshots(device.device_id, since=since)
        if snaps and is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        flags = [breached(s) for s in snaps]
        trend = classify_breach_trend(flags)
        if trend == "insufficient":
            insufficient.append(device.device_id)
            detail[device.device_id] = (device.hostname, _snapshot_count(len(snaps)))
        elif trend != "none":
            findings.append(
                Finding(
                    device.device_id,
                    device.hostname,
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
        tuple(findings),
        len(devices),
        as_of,
        rule,
        excluded=excluded,
        stale=tuple(sorted(stale)),
        excluded_detail={"insufficient_history": detail} if detail else {},
    )


def memory_trend(
    repo: FleetRepository,
    as_of: datetime,
    window_days: int = policy.TREND_WINDOW_DAYS,
    threshold_pct: float = policy.MEMORY_USED_PCT,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    def used(s: Snapshot) -> float:
        return used_pct(s.mem_total_bytes, s.mem_used_bytes)

    return _breach_trend(
        repo,
        as_of,
        _devices(repo, only_devices),
        window_days,
        f"memory used >= {threshold_pct:g}% over the last {window_days} days",
        "memory",
        lambda s: used(s) >= threshold_pct,
        used,
    )


def disk_trend(
    repo: FleetRepository,
    as_of: datetime,
    window_days: int = policy.TREND_WINDOW_DAYS,
    threshold_pct: float = policy.LOW_DISK_FREE_PCT,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    def free(s: Snapshot) -> float:
        return free_pct(s.disk_size_bytes, s.disk_available_bytes)

    return _breach_trend(
        repo,
        as_of,
        _devices(repo, only_devices),
        window_days,
        f"primary volume free space < {threshold_pct:g}% over the last {window_days} days",
        "disk",
        lambda s: free(s) < threshold_pct,
        free,
    )


def compliance_trend(
    repo: FleetRepository,
    as_of: datetime,
    *,
    check_id: str,
    window_days: int = policy.COMPLIANCE_WINDOW_DAYS,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    since = window_start(as_of, window_days)
    devices = _devices(repo, only_devices)
    findings, insufficient, stale = [], [], []
    detail: dict[str, tuple[str, str]] = {}
    for device in devices:
        snaps = [
            s for s in repo.snapshots(device.device_id, since=since) if check_id in s.compliance
        ]
        if snaps and is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        trend = classify_compliance([s.compliance[check_id].status for s in snaps])
        if trend.kind == "insufficient":
            insufficient.append(device.device_id)
            detail[device.device_id] = (device.hostname, _snapshot_count(len(snaps)))
            continue
        if trend.kind == "passing":
            continue
        path = f"compliance[{check_id}]"
        if trend.change_index is None:  # persistent failure: first and latest observation
            refs = (snaps[0].ref(path), snaps[-1].ref(path))
            change_at = None
        else:  # transition or recovery: last of the old state, first of the new, latest
            refs = (
                snaps[trend.change_index - 1].ref(path),
                snaps[trend.change_index].ref(path),
                snaps[-1].ref(path),
            )
            change_at = iso_z(snaps[trend.change_index].collected_at)
        findings.append(
            Finding(
                device.device_id,
                device.hostname,
                {"kind": trend.kind, "change_at": change_at, "snapshots": len(snaps)},
                tuple(dict.fromkeys(str(r) for r in refs)),
            )
        )
    findings.sort(key=lambda f: f.device_id)
    excluded = {"insufficient_history": tuple(sorted(insufficient))} if insufficient else {}
    return QueryResult(
        tuple(findings),
        len(devices),
        as_of,
        f"{check_id} over the last {window_days} days",
        excluded=excluded,
        stale=tuple(sorted(stale)),
        excluded_detail={"insufficient_history": detail} if detail else {},
    )


def _battery_insight_values(
    snaps: list[Snapshot], signals: BatterySignals, as_of: datetime
) -> dict[str, Any]:
    """The capacity index series, its straight-line fit, the estimate and the severity for one battery."""
    readings = [
        BatteryReading(
            s.collected_at,
            s.battery.condition,
            s.battery.cycle_count,
            s.battery.full_charge_capacity,
        )
        for s in snaps
    ]
    index = capacity_index(readings)
    fitted = fit_capacity_index(index, as_of)
    projection = fitted.projection
    severity = battery_severity(
        service_streak=signals.service_streak,
        capacity_change_pct=signals.capacity_change_pct,
        projection=projection,
    )
    return {
        "severity": severity,
        "index_series": [{"at": iso_z(at), "value": round(value, 3)} for at, value in index],
        "rate_per_day": fitted.rate_per_day,
        "fit": None
        if fitted.fit is None
        else {
            "slope_per_day": fitted.fit.slope,
            "r2": fitted.fit.r2,
            "residual_sd": fitted.fit.residual_sd,
            "n": fitted.fit.n,
        },
        "projection": {
            "status": projection.status,
            "estimate": None if projection.estimate is None else projection.estimate.isoformat(),
            "earliest": None if projection.earliest is None else projection.earliest.isoformat(),
            "latest": None if projection.latest is None else projection.latest.isoformat(),
            "days_to_estimate": projection.days_to_estimate,
        },
        "service_index": policy.BATTERY_SERVICE_INDEX,
    }


def battery_overview(
    repo: FleetRepository,
    as_of: datetime,
    window_days: int = policy.BATTERY_WINDOW_DAYS,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    """Battery wear per laptop: capacity against its own first reading, a fitted rate and an estimate.

    A laptop with fewer than `MIN_SNAPSHOTS` battery readings in the window is excluded as having
    insufficient history. A laptop is a finding when its capacity is down more than `BATTERY_DECLINE_PCT`,
    it carries a Service Recommended flag, or its severity is not "ok".
    """
    since = window_start(as_of, window_days)
    laptops = [d for d in _devices(repo, only_devices) if d.form_factor == "laptop"]
    findings, no_reading, thin, missing_latest, stale = [], [], [], [], []
    detail: dict[str, tuple[str, str]] = {}
    thin_detail: dict[str, tuple[str, str]] = {}
    for device in laptops:
        newest = repo.latest(device.device_id)
        if newest is not None and newest.battery is None:
            missing_latest.append(device.device_id)
        snaps = [
            s
            for s in repo.snapshots(device.device_id, since=since)
            if s.battery is not None and s.battery.present
        ]
        if not snaps:
            no_reading.append(device.device_id)
            detail[device.device_id] = (device.hostname, "No battery reading")
            continue
        if is_stale(snaps[-1].collected_at, as_of):
            stale.append(device.device_id)
        if len(snaps) < MIN_SNAPSHOTS:
            thin.append(device.device_id)
            n = len(snaps)
            thin_detail[device.device_id] = (
                device.hostname,
                f"Only {n} battery {'reading' if n == 1 else 'readings'} in the window",
            )
            continue
        signals = battery_signals(
            [
                BatteryReading(
                    s.collected_at,
                    s.battery.condition,
                    s.battery.cycle_count,
                    s.battery.full_charge_capacity,
                )
                for s in snaps
            ]
        )
        insight = _battery_insight_values(snaps, signals, as_of)
        declining = (
            signals.capacity_change_pct is not None
            and signals.capacity_change_pct < -policy.BATTERY_DECLINE_PCT
        )
        if declining or signals.service_streak > 0 or insight["severity"] != "ok":
            # cite what each value rests on: the two readings the capacity change was measured between,
            # the latest reading (cycles, condition) and the snapshot where the service streak began
            with_capacity = [s for s in snaps if s.battery.full_charge_capacity is not None]
            cited = [with_capacity[0], with_capacity[-1]] if with_capacity else []
            cited.append(snaps[-1])
            if signals.service_streak > 0:
                cited.append(snaps[len(snaps) - signals.service_streak])
            findings.append(
                Finding(
                    device.device_id,
                    device.hostname,
                    {
                        "capacity_change_pct": signals.capacity_change_pct,
                        "cycles_latest": signals.cycles_latest,
                        "latest_condition": signals.latest_condition,
                        "service_streak": signals.service_streak,
                        "first_service_at": None
                        if signals.first_service_at is None
                        else iso_z(signals.first_service_at),
                        "readings": signals.readings,
                        "latest_reading_at": iso_z(snaps[-1].collected_at),
                        **insight,
                    },
                    tuple(dict.fromkeys(str(s.ref("battery")) for s in cited)),
                )
            )
    findings.sort(key=lambda f: f.device_id)
    excluded: dict[str, tuple[str, ...]] = {}
    excluded_detail: dict[str, dict[str, tuple[str, str]]] = {}
    if thin:
        excluded["insufficient_history"] = tuple(sorted(thin))
        excluded_detail["insufficient_history"] = thin_detail
    if no_reading:
        excluded["no_reading"] = tuple(sorted(no_reading))
        excluded_detail["no_reading"] = detail
    return QueryResult(
        tuple(findings),
        len(laptops),
        as_of,
        f"laptops with capacity down more than {policy.BATTERY_DECLINE_PCT:g}% or a Service Recommended flag over the last {window_days} days",
        excluded=excluded,
        stale=tuple(sorted(stale)),
        missing_latest=tuple(sorted(missing_latest)),
        excluded_detail=excluded_detail,
    )


def memory_state(
    repo: FleetRepository,
    as_of: datetime,
    threshold_pct: float = policy.MEMORY_USED_PCT,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    """Devices whose memory use at the latest snapshot is at or above the threshold."""
    pairs = _latest_pairs(repo, _devices(repo, only_devices))
    findings = []
    for device, snap in pairs:
        pct = used_pct(snap.mem_total_bytes, snap.mem_used_bytes)
        if pct >= threshold_pct:
            findings.append(
                Finding(
                    device.device_id,
                    device.hostname,
                    {
                        "used_pct": pct,
                        "total_bytes": snap.mem_total_bytes,
                        "used_bytes": snap.mem_used_bytes,
                        "collected_at": iso_z(snap.collected_at),
                    },
                    (str(snap.ref("memory")),),
                )
            )
    findings.sort(key=lambda f: (-f.values["used_pct"], f.device_id))
    return QueryResult(
        tuple(findings),
        len(pairs),
        as_of,
        f"memory used >= {threshold_pct:g}% at the latest snapshot",
        stale=_stale(pairs, as_of),
    )


# ---- installed software -------------------------------------------------------------------------------


def _in_window(
    repo: FleetRepository, devices: list[Device], as_of: datetime, window_days: int
) -> dict[str, list[Snapshot]]:
    """Each device's snapshots inside the window, oldest first. Devices with none are left out."""
    since = window_start(as_of, window_days)
    found = {}
    for device in devices:
        snapshots = repo.snapshots(device.device_id, since=since)
        if snapshots:
            found[device.device_id] = snapshots
    return found


def _common_set(history: dict[str, list[Snapshot]]) -> list[str]:
    """Apps present on EVERY device in that device's earliest snapshot inside the window."""
    common: set[str] | None = None
    for snapshots in history.values():
        first = {item.name for item in snapshots[0].software}
        common = first if common is None else common & first
    return sorted(common or ())


def common_software(
    repo: FleetRepository,
    as_of: datetime,
    *,
    window_days: int = policy.SOFTWARE_WINDOW_DAYS,
) -> list[str]:
    """The tenant's common software set for the window (app names, sorted)."""
    return _common_set(_in_window(repo, repo.list_devices(), as_of, window_days))


def software_installed(
    repo: FleetRepository,
    as_of: datetime,
    *,
    name: str | None = None,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    """Installed software at each device's latest snapshot.

    With `name` (matched exactly, without regard to case) the findings are the devices that hold the app.
    Without it every device with at least one app is a finding that lists its apps, and the apps that are
    not on every device of the fleet that reports software (same name and version) are cited. A device that reports no apps is
    reported as having no readings. An empty or unknown `name` matches nothing; it never means "all".
    """
    pairs = _latest_pairs(repo, _devices(repo, only_devices))
    findings: list[Finding] = []
    excluded: dict[str, tuple[str, ...]] = {}
    detail: dict[str, tuple[str, str]] = {}
    if name is not None:
        wanted = name.strip().lower()
        for device, snap in pairs:
            for item in snap.software:
                if item.name.lower() == wanted:
                    findings.append(
                        Finding(
                            device.device_id,
                            device.hostname,
                            {
                                "name": item.name,
                                "version": item.version,
                                "publisher": item.publisher,
                                "collected_at": iso_z(snap.collected_at),
                            },
                            (str(snap.ref(f"software[{item.name}]")),),
                        )
                    )
        rule = "devices with the requested app installed at the latest snapshot"
    else:
        # The shared set is fleet-wide (every tenant device, not just the scope) and ignores devices that
        # report no apps, so narrowing to one device never makes its apps look shared.
        held = [
            {(i.name, i.version) for i in snap.software}
            for _, snap in _latest_pairs(repo, repo.list_devices())
            if snap.software
        ]
        shared = set.intersection(*held) if held else set()
        empty = []
        for device, snap in pairs:
            if not snap.software:
                empty.append(device.device_id)
                detail[device.device_id] = (device.hostname, "No installed software reported")
                continue
            extra = [i for i in snap.software if (i.name, i.version) not in shared]
            cited = extra or list(snap.software[:1])
            findings.append(
                Finding(
                    device.device_id,
                    device.hostname,
                    {
                        "apps": [{"name": i.name, "version": i.version} for i in snap.software],
                        "extra": [{"name": i.name, "version": i.version} for i in extra],
                        "collected_at": iso_z(snap.collected_at),
                    },
                    tuple(str(snap.ref(f"software[{i.name}]")) for i in cited),
                )
            )
        if empty:
            excluded["no_readings"] = tuple(sorted(empty))
        listing = ", ".join(sorted(f"{n} {v}" for n, v in shared)) or "none"
        rule = (
            "installed software at the latest snapshot; apps (name and version) on every device of "
            f"the fleet that reports software: {listing}"
        )
    findings.sort(key=lambda f: f.device_id)
    return QueryResult(
        tuple(findings),
        len(pairs),
        as_of,
        rule,
        excluded=excluded,
        stale=_stale(pairs, as_of),
        excluded_detail={"no_readings": detail} if detail else {},
    )


def new_software(
    repo: FleetRepository,
    as_of: datetime,
    *,
    window_days: int = policy.SOFTWARE_WINDOW_DAYS,
    name: str | None = None,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    """Apps newly installed inside the window: one finding per (device, app).

    "Not in the common set" means the app was not on every device at that device's first snapshot in the
    window. "Newly installed" means that, on this device, the app first appears after the device's first
    snapshot in the window. The common set is the whole tenant's, even when `only_devices` narrows the
    findings. Nothing here says an app is unapproved or risky: the telemetry has no such label.
    """
    tenant_devices = repo.list_devices()
    history = _in_window(repo, tenant_devices, as_of, window_days)
    common = set(_common_set(history))
    hostnames = {d.device_id: d.hostname for d in tenant_devices}
    scope = {d.device_id for d in _devices(repo, only_devices)}
    wanted = None if name is None else name.strip().lower()
    findings: list[Finding] = []
    for device_id in sorted(scope & history.keys()):
        snapshots = history[device_id]
        sightings: dict[str, list[Snapshot]] = {}
        for snap in snapshots:
            for item in snap.software:
                sightings.setdefault(item.name, []).append(snap)
        for app in sorted(sightings):
            seen = sightings[app]
            if app in common or seen[0] is snapshots[0]:
                continue
            if wanted is not None and app.lower() != wanted:
                continue
            first, last = seen[0], seen[-1]
            path = f"software[{app}]"
            version = next(i.version for i in first.software if i.name == app)
            latest_version = next(i.version for i in last.software if i.name == app)
            findings.append(
                Finding(
                    device_id,
                    hostnames[device_id],
                    {
                        "app": app,
                        "version": version,
                        "latest_version": latest_version,
                        "first_seen": iso_z(first.collected_at),
                        "last_seen": iso_z(last.collected_at),
                        "still_present": last is snapshots[-1],
                        "window_days": window_days,
                    },
                    tuple(dict.fromkeys((str(first.ref(path)), str(last.ref(path))))),
                )
            )
    findings.sort(key=lambda f: (f.device_id, f.values["app"]))
    findings.sort(key=lambda f: f.values["first_seen"], reverse=True)  # newest install first
    evaluated = scope & history.keys()
    missing = sorted(scope - history.keys())
    stale = sorted(d for d in evaluated if is_stale(history[d][-1].collected_at, as_of))
    listing = ", ".join(sorted(common)) or "none"
    rule = (
        f"software newly installed in the last {window_days} days: not in the common set and first "
        "seen after the device's first snapshot in the window. The common set is the apps on every "
        f"device at its first snapshot in the window: {listing}"
    )
    return QueryResult(
        tuple(findings),
        len(evaluated),
        as_of,
        rule,
        excluded={"no_readings": tuple(missing)} if missing else {},
        stale=tuple(stale),
        excluded_detail={
            "no_readings": {
                d: (hostnames[d], "No snapshots in the window") for d in missing if d in hostnames
            }
        }
        if missing
        else {},
    )
