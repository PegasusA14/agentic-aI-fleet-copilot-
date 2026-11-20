from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Literal

from fleet_copilot import policy
from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.domain.windows import is_stale, window_start
from fleet_copilot.fleet.queries import Finding, QueryResult
from fleet_copilot.repository import Device, FleetRepository
from fleet_copilot.timeutil import iso_z

HistoryMetric = Literal["disk_free_pct", "memory_used_pct", "battery_capacity", "compliance"]


def _scoped(repo: FleetRepository, only: Collection[str] | None) -> list[Device]:
    devices = repo.list_devices()
    if only is None:
        return devices
    wanted = set(only)
    return [d for d in devices if d.device_id in wanted]


def find_devices(
    repo: FleetRepository,
    as_of: datetime,
    *,
    os_name: str | None = None,
    model_name: str | None = None,
    form_factor: str | None = None,
    hostname: str | None = None,
    employee_id: str | None = None,
    only_devices: Collection[str] | None = None,
) -> QueryResult:
    """Inventory lookup. `employee_id` narrows the match but is never returned."""
    scoped = _scoped(repo, only_devices)
    findings = []
    stale = []
    for device in scoped:
        if os_name and device.os_name != os_name:
            continue
        if model_name and device.model_name != model_name:
            continue
        if form_factor and device.form_factor != form_factor:
            continue
        if hostname and device.hostname != hostname:
            continue
        if employee_id and device.employee_id != employee_id:
            continue
        snap = repo.latest(device.device_id)
        if snap is None:
            continue
        if is_stale(snap.collected_at, as_of):
            stale.append(device.device_id)
        findings.append(
            Finding(
                device.device_id,
                device.hostname,
                {
                    "os_name": snap.os_name,
                    "os_version": snap.os_version,
                    "model_name": device.model_name,
                    "form_factor": device.form_factor,
                    "collected_at": iso_z(snap.collected_at),
                },
                (str(snap.ref("os")),),
            )
        )
    filters = [
        f"{label}={value}"
        for label, value in (
            ("os", os_name),
            ("model", model_name),
            ("form_factor", form_factor),
            ("hostname", hostname),
        )
        if value
    ]
    if employee_id:
        filters.append("employee filter applied")
    rule = "devices matching " + (", ".join(filters) if filters else "no filter (all devices)")
    findings.sort(key=lambda f: f.device_id)
    return QueryResult(tuple(findings), len(scoped), as_of, rule, stale=tuple(sorted(stale)))


def device_history(
    repo: FleetRepository,
    as_of: datetime,
    *,
    device_id: str,
    metric: HistoryMetric,
    window_days: int = policy.TREND_WINDOW_DAYS,
    check_id: str | None = None,
) -> QueryResult:
    """One device's readings for one metric over a trailing window, with a ref per point."""
    if metric == "compliance" and not check_id:
        raise ValueError("check_id is required for a compliance history")
    device = repo.get_device(device_id)  # NotFound for unknown and foreign ids alike
    snaps = repo.snapshots(device_id, since=window_start(as_of, window_days))
    points: list[dict[str, object]] = []
    refs: list[str] = []
    for snap in snaps:
        if metric == "disk_free_pct":
            value: object = free_pct(snap.disk_size_bytes, snap.disk_available_bytes)
            path = "disk"
        elif metric == "memory_used_pct":
            value = used_pct(snap.mem_total_bytes, snap.mem_used_bytes)
            path = "memory"
        elif metric == "battery_capacity":
            battery = snap.battery
            if battery is None or battery.full_charge_capacity is None:
                continue
            value = battery.full_charge_capacity
            path = "battery"
        else:
            check = snap.compliance.get(check_id or "")
            if check is None:
                continue
            value = check.status
            path = f"compliance[{check_id}]"
        points.append({"at": iso_z(snap.collected_at), "value": value})
        refs.append(str(snap.ref(path)))
    label = metric if metric != "compliance" else f"compliance {check_id}"
    rule = f"{label} for one device over the last {window_days} days"
    if not points:
        return QueryResult((), 1, as_of, rule, excluded={"no_readings": (device.device_id,)})
    finding = Finding(
        device.device_id,
        device.hostname,
        {
            "metric": metric,
            "check_id": check_id,
            "window_days": window_days,
            "snapshots": len(points),
            "points": points,
        },
        tuple(refs),
    )
    return QueryResult((finding,), 1, as_of, rule)
