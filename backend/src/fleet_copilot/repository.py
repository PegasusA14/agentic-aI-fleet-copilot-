from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.timeutil import iso_z, parse_ts


class NotFound(Exception):
    """Raised for missing AND out-of-tenant records, with one fixed message,
    so a caller can never tell whether a record exists in another tenant."""

    def __init__(self, *args: object) -> None:
        # Ignoring `args` keeps the fixed message and lets copy and pickle rebuild the exception
        # from `self.args` (which holds that message) without changing it.
        super().__init__("not found")


@dataclass(frozen=True)
class TenantContext:
    company_id: str
    actor_id: str


@dataclass(frozen=True)
class Device:
    company_id: str
    device_id: str
    employee_id: str
    hostname: str
    os_name: str
    model_name: str
    form_factor: str


@dataclass(frozen=True)
class BatteryBlock:
    present: bool
    condition: str | None
    cycle_count: int | None
    full_charge_capacity: int | None
    percentage: int | None


@dataclass(frozen=True)
class ComplianceCheck:
    status: str
    severity: str


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    company_id: str
    device_id: str
    employee_id: str
    collected_at: datetime
    hostname: str
    os_name: str
    os_version: str
    model_name: str
    disk_mount: str
    disk_size_bytes: int
    disk_available_bytes: int
    disk_encrypted: bool
    mem_total_bytes: int
    mem_used_bytes: int
    battery: BatteryBlock | None
    compliance: dict[str, ComplianceCheck]

    def ref(self, path: str) -> EvidenceRef:
        return EvidenceRef(self.company_id, self.device_id, iso_z(self.collected_at), path)


@dataclass(frozen=True)
class EvidenceValue:
    ref: str
    collected_at: str
    value: dict[str, Any]


_COMPLIANCE_PATH = re.compile(r"compliance\[([A-Za-z0-9_.-]+)\]")


def dataset_as_of(conn: sqlite3.Connection) -> datetime:
    """The pinned 'now': the latest `collected_at` in the dataset."""
    row = conn.execute("SELECT value FROM dataset_meta WHERE key = 'max_collected_at'").fetchone()
    return parse_ts(row["value"])


def _device(row: sqlite3.Row) -> Device:
    return Device(
        row["company_id"], row["device_id"], row["employee_id"], row["hostname"],
        row["os_name"], row["model_name"], row["form_factor"],
    )


def _evidence_value(snapshot: Snapshot, path: str) -> dict[str, Any]:
    if path == "os":
        return {
            "os_name": snapshot.os_name,
            "os_version": snapshot.os_version,
            "hostname": snapshot.hostname,
        }
    if path == "disk":
        return {
            "mount_point": snapshot.disk_mount,
            "size_bytes": snapshot.disk_size_bytes,
            "available_bytes": snapshot.disk_available_bytes,
            "encrypted": snapshot.disk_encrypted,
        }
    if path == "memory":
        return {"total_bytes": snapshot.mem_total_bytes, "used_bytes": snapshot.mem_used_bytes}
    if path == "battery" and snapshot.battery is not None:
        b = snapshot.battery
        return {
            "condition": b.condition,
            "cycle_count": b.cycle_count,
            "full_charge_capacity": b.full_charge_capacity,
            "percentage": b.percentage,
        }
    match = _COMPLIANCE_PATH.fullmatch(path)
    if match and match.group(1) in snapshot.compliance:
        check = snapshot.compliance[match.group(1)]
        return {"check_id": match.group(1), "status": check.status, "severity": check.severity}
    raise NotFound()


class FleetRepository:
    """Every read is filtered by the context's company. There is no way to pass another one."""

    def __init__(self, conn: sqlite3.Connection, ctx: TenantContext) -> None:
        self._conn = conn
        self._ctx = ctx

    @property
    def context(self) -> TenantContext:
        return self._ctx

    def list_devices(self) -> list[Device]:
        rows = self._conn.execute(
            "SELECT * FROM devices WHERE company_id = ? ORDER BY device_id", (self._ctx.company_id,)
        ).fetchall()
        return [_device(r) for r in rows]

    def get_device(self, device_id: str) -> Device:
        row = self._conn.execute(
            "SELECT * FROM devices WHERE company_id = ? AND device_id = ?",
            (self._ctx.company_id, device_id),
        ).fetchone()
        if row is None:
            raise NotFound()
        return _device(row)

    def snapshots(self, device_id: str, since: datetime | None = None) -> list[Snapshot]:
        """Snapshots in chronological order, optionally from `since` (inclusive)."""
        self.get_device(device_id)
        sql = "SELECT * FROM snapshots WHERE company_id = ? AND device_id = ?"
        params: list[Any] = [self._ctx.company_id, device_id]
        if since is not None:
            sql += " AND collected_at >= ?"
            params.append(iso_z(since))
        sql += " ORDER BY collected_at"
        return self._hydrate(self._conn.execute(sql, params).fetchall())

    def latest(self, device_id: str, *, require_battery: bool = False) -> Snapshot | None:
        """Newest snapshot, or the newest one that has a battery block."""
        self.get_device(device_id)
        sql = "SELECT * FROM snapshots WHERE company_id = ? AND device_id = ?"
        if require_battery:
            sql += " AND battery_present IS NOT NULL"
        sql += " ORDER BY collected_at DESC LIMIT 1"
        rows = self._conn.execute(sql, (self._ctx.company_id, device_id)).fetchall()
        return self._hydrate(rows)[0] if rows else None

    def resolve(self, ref: EvidenceRef) -> EvidenceValue:
        """Return the observation a reference points at, or `NotFound` for anything unprovable."""
        if ref.company_id != self._ctx.company_id:
            raise NotFound()
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE company_id = ? AND device_id = ? AND collected_at = ?",
            (self._ctx.company_id, ref.device_id, ref.collected_at),
        ).fetchone()
        if row is None:
            raise NotFound()
        snapshot = self._hydrate([row])[0]
        return EvidenceValue(ref.format(), ref.collected_at, _evidence_value(snapshot, ref.path))

    def _hydrate(self, rows: list[sqlite3.Row]) -> list[Snapshot]:
        if not rows:
            return []
        ids = [r["snapshot_id"] for r in rows]
        marks = ",".join("?" for _ in ids)
        checks: dict[int, dict[str, ComplianceCheck]] = {i: {} for i in ids}
        # `compliance` has no company column, so it is tenant-filtered through its snapshot
        for c in self._conn.execute(
            "SELECT c.snapshot_id, c.check_id, c.status, c.severity "
            "FROM compliance c JOIN snapshots s ON s.snapshot_id = c.snapshot_id "
            f"WHERE s.company_id = ? AND c.snapshot_id IN ({marks})",
            [self._ctx.company_id, *ids],
        ):
            checks[c["snapshot_id"]][c["check_id"]] = ComplianceCheck(c["status"], c["severity"])
        out = []
        for r in rows:
            battery = None
            if r["battery_present"] is not None:
                battery = BatteryBlock(
                    bool(r["battery_present"]), r["battery_condition"], r["battery_cycle_count"],
                    r["battery_full_charge_capacity"], r["battery_percentage"],
                )
            out.append(
                Snapshot(
                    r["snapshot_id"], r["company_id"], r["device_id"], r["employee_id"],
                    parse_ts(r["collected_at"]), r["hostname"], r["os_name"], r["os_version"],
                    r["model_name"], r["disk_mount"], r["disk_size_bytes"], r["disk_available_bytes"],
                    bool(r["disk_encrypted"]), r["mem_total_bytes"], r["mem_used_bytes"],
                    battery, checks[r["snapshot_id"]],
                )
            )
        return out
