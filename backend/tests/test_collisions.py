"""The same device id in two tenants, with different data at the same instants.

The real dataset has disjoint device ids and `get_device` guards most entry points, so a
missing `company_id` filter in a SQL statement can hide there. Here a missing filter
cannot hide: every row of the other tenant has the same device id and the same timestamp.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from factories import make_conn, make_row

from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext
from fleet_copilot.timeutil import parse_ts

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
DUP = "DUP"
STAMPS = [
    "2026-06-09T09:00:00Z",
    "2026-06-10T09:00:00Z",
    "2026-06-11T09:00:00Z",
    "2026-06-12T09:00:00Z",
]
SEVERITY = {"disk_encryption": "high", "screen_lock": "medium", "os_up_to_date": "medium"}


@dataclass(frozen=True)
class Spec:
    """Raw telemetry for DUP in one tenant, plus what the queries must say about it."""

    company: str
    hostname: str
    os_version: str
    disk_size: int
    disk_available: list[int]
    mem_total: int
    mem_used: list[int]
    capacities: list[int]
    conditions: list[str]
    cycles: list[int]
    screen_lock: list[str]
    disk_encryption: list[str]
    # expectations
    low_free_pct: float
    mem_latest_pct: float
    disk_latest_pct: float
    capacity_change_pct: float
    service_streak: int
    failing_now: list[str]


SPECS = {
    "acme-001": Spec(
        company="acme-001",
        hostname="acme-dup-mac",
        os_version="14.5",
        disk_size=1000,
        disk_available=[50, 45, 40, 35],
        mem_total=1000,
        mem_used=[900, 910, 920, 930],
        capacities=[10000, 9800, 9600, 9400],
        conditions=["Normal"] * 4,
        cycles=[100, 110, 120, 130],
        screen_lock=["pass", "pass", "fail", "fail"],
        disk_encryption=["pass"] * 4,
        low_free_pct=3.5,
        mem_latest_pct=93.0,
        disk_latest_pct=3.5,
        capacity_change_pct=-6.0,
        service_streak=0,
        failing_now=["screen_lock"],
    ),
    "globex-002": Spec(
        company="globex-002",
        hostname="globex-dup-mac",
        os_version="13.2",
        disk_size=2000,
        disk_available=[160, 150, 140, 130],
        mem_total=2000,
        mem_used=[1900, 1910, 1920, 1930],
        capacities=[8000, 7600, 7200, 6800],
        conditions=["Normal", "Normal", "Service Recommended", "Service Recommended"],
        cycles=[300, 310, 320, 330],
        screen_lock=["pass"] * 4,
        disk_encryption=["fail"] * 4,
        low_free_pct=6.5,
        mem_latest_pct=96.5,
        disk_latest_pct=6.5,
        capacity_change_pct=-15.0,
        service_streak=2,
        failing_now=["disk_encryption"],
    ),
}
TENANTS = list(SPECS)


def other(tenant):
    return next(t for t in TENANTS if t != tenant)


def rows_for(spec):
    rows = []
    for i, stamp in enumerate(STAMPS):
        statuses = {
            "screen_lock": spec.screen_lock[i],
            "disk_encryption": spec.disk_encryption[i],
            "os_up_to_date": "pass",
        }
        rows.append(
            make_row(
                DUP,
                spec.company,
                stamp,
                os={
                    "product_name": "macOS",
                    "product_version": spec.os_version,
                    "hostname": spec.hostname,
                },
                memory={
                    "total_memory_bytes": spec.mem_total,
                    "used_memory_bytes": spec.mem_used[i],
                },
                disk_volumes=[
                    {
                        "mount_point": "/",
                        "size_bytes": spec.disk_size,
                        "available_bytes": spec.disk_available[i],
                        "encrypted": True,
                    }
                ],
                battery={
                    "battery_present": True,
                    "condition": spec.conditions[i],
                    "cycle_count": spec.cycles[i],
                    "full_charge_capacity": spec.capacities[i],
                    "percentage": 50,
                },
                compliance_results=[
                    {"check_id": cid, "status": status, "severity": SEVERITY[cid]}
                    for cid, status in statuses.items()
                ],
            )
        )
    return rows


@pytest.fixture(scope="module")
def conn():
    # interleaved so neither tenant's rows are contiguous or first by accident of insert order
    acme, globex = rows_for(SPECS["acme-001"]), rows_for(SPECS["globex-002"])
    rows = [row for pair in zip(globex, acme, strict=True) for row in pair]
    return make_conn(rows)


def repo_for(conn, tenant):
    return FleetRepository(conn, TenantContext(tenant, "collision-test"))


def dup_finding(result, tenant):
    own = [f for f in result.findings if f.device_id == DUP]
    assert len(own) <= 1, f"{tenant}: DUP reported {len(own)} times"
    return own[0] if own else None


def ref(tenant, stamp, path):
    return f"{tenant}/{DUP}@{stamp}#{path}"


def test_fixture_really_collides(conn):
    rows = conn.execute(
        "SELECT company_id, collected_at FROM snapshots WHERE device_id = ?", (DUP,)
    )
    seen = {(r["company_id"], r["collected_at"]) for r in rows}
    assert seen == {(t, s) for t in TENANTS for s in STAMPS}
    assert conn.execute("SELECT COUNT(DISTINCT hostname) FROM snapshots").fetchone()[0] == 2


@pytest.mark.parametrize("tenant", TENANTS)
def test_device_lookups_return_only_the_tenants_own_record(conn, tenant):
    repo, spec = repo_for(conn, tenant), SPECS[tenant]
    device = repo.get_device(DUP)
    assert (device.company_id, device.hostname) == (tenant, spec.hostname)
    assert [(d.company_id, d.device_id) for d in repo.list_devices()] == [(tenant, DUP)]


@pytest.mark.parametrize("tenant", TENANTS)
def test_snapshots_return_only_the_tenants_own_rows(conn, tenant):
    repo, spec = repo_for(conn, tenant), SPECS[tenant]
    snaps = repo.snapshots(DUP)
    assert [s.collected_at for s in snaps] == [parse_ts(s) for s in STAMPS]
    assert {s.company_id for s in snaps} == {tenant}
    assert {s.hostname for s in snaps} == {spec.hostname}
    assert [s.disk_size_bytes for s in snaps] == [spec.disk_size] * 4
    assert [s.disk_available_bytes for s in snaps] == spec.disk_available
    assert [s.mem_used_bytes for s in snaps] == spec.mem_used
    assert [s.battery.full_charge_capacity for s in snaps] == spec.capacities
    assert [s.compliance["screen_lock"].status for s in snaps] == spec.screen_lock
    assert [s.compliance["disk_encryption"].status for s in snaps] == spec.disk_encryption
    assert all(len(s.compliance) == 3 for s in snaps)


@pytest.mark.parametrize("tenant", TENANTS)
def test_windowed_snapshots_return_only_the_tenants_own_rows(conn, tenant):
    repo, spec = repo_for(conn, tenant), SPECS[tenant]
    snaps = repo.snapshots(DUP, since=parse_ts(STAMPS[2]))
    assert [s.company_id for s in snaps] == [tenant, tenant]
    assert [s.disk_available_bytes for s in snaps] == spec.disk_available[2:]


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("require_battery", [False, True])
def test_latest_returns_only_the_tenants_own_row(conn, tenant, require_battery):
    spec = SPECS[tenant]
    snap = repo_for(conn, tenant).latest(DUP, require_battery=require_battery)
    assert snap.company_id == tenant
    assert snap.collected_at == parse_ts(STAMPS[-1])
    assert snap.hostname == spec.hostname
    assert snap.os_version == spec.os_version
    assert snap.disk_size_bytes == spec.disk_size
    assert snap.disk_available_bytes == spec.disk_available[-1]
    assert snap.mem_used_bytes == spec.mem_used[-1]
    assert snap.battery.full_charge_capacity == spec.capacities[-1]
    assert snap.compliance["screen_lock"].status == spec.screen_lock[-1]
    assert snap.compliance["disk_encryption"].status == spec.disk_encryption[-1]


@pytest.mark.parametrize("tenant", TENANTS)
def test_resolve_returns_the_tenants_own_values(conn, tenant):
    repo, spec = repo_for(conn, tenant), SPECS[tenant]
    stamp = STAMPS[-1]

    def value(path):
        return repo.resolve(EvidenceRef.parse(ref(tenant, stamp, path))).value

    assert value("os")["hostname"] == spec.hostname
    assert value("os")["os_version"] == spec.os_version
    assert value("disk")["size_bytes"] == spec.disk_size
    assert value("disk")["available_bytes"] == spec.disk_available[-1]
    assert value("memory")["used_bytes"] == spec.mem_used[-1]
    assert value("battery")["full_charge_capacity"] == spec.capacities[-1]
    assert value("battery")["cycle_count"] == spec.cycles[-1]
    assert value("compliance[screen_lock]")["status"] == spec.screen_lock[-1]
    assert value("compliance[disk_encryption]")["status"] == spec.disk_encryption[-1]


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("path", ["os", "disk", "memory", "battery", "compliance[screen_lock]"])
def test_resolve_refuses_the_other_tenants_reference(conn, tenant, path):
    repo = repo_for(conn, tenant)
    for stamp in STAMPS:
        with pytest.raises(NotFound):
            repo.resolve(EvidenceRef.parse(ref(other(tenant), stamp, path)))


@pytest.mark.parametrize("tenant", TENANTS)
def test_hydration_never_attaches_another_tenants_compliance(conn, tenant):
    """Defence in depth: even handed a foreign row, `_hydrate` finds no foreign compliance."""
    repo = repo_for(conn, tenant)
    foreign = conn.execute(
        "SELECT * FROM snapshots WHERE company_id = ? AND device_id = ?", (other(tenant), DUP)
    ).fetchall()
    assert len(foreign) == len(STAMPS)
    assert all(s.compliance == {} for s in repo._hydrate(foreign))
    own = conn.execute(
        "SELECT * FROM snapshots WHERE company_id = ? AND device_id = ?", (tenant, DUP)
    ).fetchall()
    assert all(len(s.compliance) == 3 for s in repo._hydrate(own))


@pytest.mark.parametrize("tenant", TENANTS)
def test_low_disk_reflects_only_the_tenants_own_data(conn, tenant):
    spec = SPECS[tenant]
    result = q.low_disk(repo_for(conn, tenant), AS_OF)
    assert result.population == 1
    finding = dup_finding(result, tenant)
    assert finding.hostname == spec.hostname
    assert finding.values["free_pct"] == pytest.approx(spec.low_free_pct)
    assert finding.values["size_bytes"] == spec.disk_size
    assert finding.values["available_bytes"] == spec.disk_available[-1]
    assert finding.refs == (ref(tenant, STAMPS[-1], "disk"),)


@pytest.mark.parametrize("tenant", TENANTS)
def test_macos_below_reflects_only_the_tenants_own_data(conn, tenant):
    spec = SPECS[tenant]
    result = q.macos_below(repo_for(conn, tenant), AS_OF)
    assert result.population == 1
    finding = dup_finding(result, tenant)
    assert finding.hostname == spec.hostname
    assert finding.values["os_version"] == spec.os_version
    assert finding.refs == (ref(tenant, STAMPS[-1], "os"),)


@pytest.mark.parametrize("tenant", TENANTS)
def test_compliance_failures_reflect_only_the_tenants_own_data(conn, tenant):
    spec = SPECS[tenant]
    result = q.compliance_failures(repo_for(conn, tenant), AS_OF)
    assert result.population == 1
    finding = dup_finding(result, tenant)
    assert [f["check_id"] for f in finding.values["failing"]] == spec.failing_now
    assert finding.refs == tuple(
        ref(tenant, STAMPS[-1], f"compliance[{c}]") for c in spec.failing_now
    )


@pytest.mark.parametrize("tenant", TENANTS)
def test_memory_trend_reflects_only_the_tenants_own_data(conn, tenant):
    spec = SPECS[tenant]
    result = q.memory_trend(repo_for(conn, tenant), AS_OF)
    assert result.population == 1
    finding = dup_finding(result, tenant)
    assert (finding.values["trend"], finding.values["breached"], finding.values["snapshots"]) == (
        "sustained",
        4,
        4,
    )
    assert finding.values["latest_value"] == pytest.approx(spec.mem_latest_pct)
    assert sorted(finding.refs) == [ref(tenant, s, "memory") for s in STAMPS]


@pytest.mark.parametrize("tenant", TENANTS)
def test_disk_trend_reflects_only_the_tenants_own_data(conn, tenant):
    spec = SPECS[tenant]
    result = q.disk_trend(repo_for(conn, tenant), AS_OF)
    assert result.population == 1
    finding = dup_finding(result, tenant)
    assert (finding.values["trend"], finding.values["breached"], finding.values["snapshots"]) == (
        "sustained",
        4,
        4,
    )
    assert finding.values["latest_value"] == pytest.approx(spec.disk_latest_pct)
    assert sorted(finding.refs) == [ref(tenant, s, "disk") for s in STAMPS]


def test_compliance_trend_reflects_only_the_tenants_own_data(conn):
    acme, globex = (repo_for(conn, t) for t in TENANTS)

    # screen_lock: failing since the third snapshot in acme, always passing in globex
    result = q.compliance_trend(acme, AS_OF, check_id="screen_lock")
    finding = dup_finding(result, "acme-001")
    assert finding.values == {"kind": "transition", "change_at": STAMPS[2], "snapshots": 4}
    assert finding.refs == tuple(
        ref("acme-001", STAMPS[i], "compliance[screen_lock]") for i in (1, 2, 3)
    )
    result = q.compliance_trend(globex, AS_OF, check_id="screen_lock")
    assert dup_finding(result, "globex-002") is None
    assert result.population == 1

    # disk_encryption: always passing in acme, failing throughout in globex
    result = q.compliance_trend(acme, AS_OF, check_id="disk_encryption")
    assert dup_finding(result, "acme-001") is None
    assert result.population == 1
    finding = dup_finding(
        q.compliance_trend(globex, AS_OF, check_id="disk_encryption"), "globex-002"
    )
    assert finding.values == {"kind": "persistent_failure", "change_at": None, "snapshots": 4}
    assert finding.refs == tuple(
        ref("globex-002", STAMPS[i], "compliance[disk_encryption]") for i in (0, 3)
    )


@pytest.mark.parametrize("tenant", TENANTS)
def test_battery_overview_reflects_only_the_tenants_own_data(conn, tenant):
    spec = SPECS[tenant]
    result = q.battery_overview(repo_for(conn, tenant), AS_OF)
    assert result.population == 1
    finding = dup_finding(result, tenant)
    assert finding.hostname == spec.hostname
    assert finding.values["capacity_change_pct"] == pytest.approx(spec.capacity_change_pct)
    assert finding.values["cycles_latest"] == spec.cycles[-1]
    assert finding.values["latest_condition"] == spec.conditions[-1]
    assert finding.values["service_streak"] == spec.service_streak
    assert finding.values["readings"] == 4
    assert all(r.startswith(f"{tenant}/{DUP}@") and r.endswith("#battery") for r in finding.refs)
    assert ref(tenant, STAMPS[0], "battery") in finding.refs
    assert ref(tenant, STAMPS[-1], "battery") in finding.refs
