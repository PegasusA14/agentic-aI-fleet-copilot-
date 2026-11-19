import inspect

import pytest

from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext, dataset_as_of

TENANTS = ["acme-001", "globex-002", "initech-003"]


def repo_for(conn, tenant):
    return FleetRepository(conn, TenantContext(tenant, "isolation-test"))


def every_query(repo, as_of):
    return {
        "low_disk": q.low_disk(repo, as_of),
        "macos_below": q.macos_below(repo, as_of),
        "compliance_failures": q.compliance_failures(repo, as_of),
        "compliance_failures_laptops": q.compliance_failures(repo, as_of, form_factor="laptop"),
        "memory_trend": q.memory_trend(repo, as_of, 30),
        "disk_trend": q.disk_trend(repo, as_of, 30),
        "screen_lock_trend": q.compliance_trend(repo, as_of, check_id="screen_lock"),
        "battery": q.battery_overview(repo, as_of),
    }


def mentioned(result):
    ids = {f.device_id for f in result.findings}
    for group in result.excluded.values():
        ids.update(group)
    ids.update(result.stale)
    ids.update(result.missing_latest)
    refs = [ref for f in result.findings for ref in f.refs]
    return ids, refs


@pytest.mark.parametrize("tenant", TENANTS)
def test_no_query_result_leaves_its_tenant(real_conn, tenant):
    repo = repo_for(real_conn, tenant)
    own = {d.device_id for d in repo.list_devices()}
    assert own
    for name, result in every_query(repo, dataset_as_of(real_conn)).items():
        ids, refs = mentioned(result)
        assert ids <= own, f"{name} mentioned foreign devices {ids - own}"
        assert all(r.startswith(f"{tenant}/") for r in refs), name
        assert all(EvidenceRef.parse(r).device_id in own for r in refs), name


def outcome(call, *args):
    try:
        call(*args)
    except NotFound as exc:
        return str(exc)
    return "allowed"


def real_latest_stamps(conn, device_id):
    rows = conn.execute(
        "SELECT collected_at FROM snapshots WHERE device_id = ? ORDER BY collected_at DESC LIMIT 1",
        (device_id,),
    )
    return [r["collected_at"] for r in rows]


@pytest.mark.parametrize("tenant", TENANTS)
def test_foreign_devices_look_identical_to_nonexistent_ones(real_conn, tenant):
    repo = repo_for(real_conn, tenant)
    own = {d.device_id for d in repo.list_devices()}
    foreign = {r["device_id"] for r in real_conn.execute("SELECT device_id FROM devices")} - own
    assert foreign
    for device_id in [*sorted(foreign), "DOES-NOT-EXIST"]:
        assert outcome(repo.get_device, device_id) == "not found"
        assert outcome(repo.snapshots, device_id) == "not found"
        assert outcome(repo.latest, device_id) == "not found"
        # One timestamp that matches no snapshot, and the device's real latest one, so the
        # company guard in `resolve` is what refuses a foreign reference, not a missing row.
        stamps = ["2026-06-12T09:00:00Z", *real_latest_stamps(real_conn, device_id)]
        for claimed_company in TENANTS:  # a forged company in the reference changes nothing
            for stamp in stamps:
                ref = EvidenceRef(claimed_company, device_id, stamp, "os")
                assert outcome(repo.resolve, ref) == "not found"


@pytest.mark.parametrize("tenant", TENANTS)
def test_own_evidence_still_resolves(real_conn, tenant):
    repo = repo_for(real_conn, tenant)
    device = repo.list_devices()[0]
    snapshot = repo.latest(device.device_id)
    assert repo.resolve(snapshot.ref("os")).value["hostname"] == device.hostname


def test_no_public_entry_point_accepts_a_company_argument():
    functions = [fn for name, fn in inspect.getmembers(FleetRepository, inspect.isfunction) if not name.startswith("_")]
    functions += [
        fn for name, fn in inspect.getmembers(q, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == q.__name__
    ]
    assert len(functions) >= 12
    for fn in functions:
        names = [p.lower() for p in inspect.signature(fn).parameters]
        assert not any("company" in n or "tenant" in n for n in names), fn.__qualname__
