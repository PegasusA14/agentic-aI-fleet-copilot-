from collections import Counter

import pytest

from fleet_copilot.fleet.inventory import device_history, find_devices
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext, dataset_as_of


def repo_for(conn, tenant="acme-001"):
    return FleetRepository(conn, TenantContext(tenant, "inventory-test"))


def ids(result):
    return sorted(f.device_id for f in result.findings)


def test_find_devices_by_os(real_conn):
    result = find_devices(repo_for(real_conn), dataset_as_of(real_conn), os_name="macOS")
    assert len(result.findings) == 7
    assert result.population == 10
    assert all(f.values["os_name"] == "macOS" for f in result.findings)
    assert all(f.refs[0].endswith("#os") for f in result.findings)


def test_find_devices_by_form_factor_and_hostname(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    assert ids(find_devices(repo, as_of, form_factor="desktop")) == ["DLT35MHGTH2D"]
    assert ids(find_devices(repo, as_of, hostname="acme-macbook-2")) == ["1LYSSFD074BB"]


def test_employee_filter_works_but_the_id_is_never_echoed(real_conn):
    result = find_devices(
        repo_for(real_conn), dataset_as_of(real_conn), employee_id="emp-acme-1003"
    )
    assert ids(result) == ["9MXJVZ1I0DKA", "DLT35MHGTH2D", "MT7PJB7N5LRE"]
    assert "emp-acme" not in repr(result)


def test_find_devices_filters_combine_and_respect_only_devices(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    both = find_devices(repo, as_of, os_name="macOS", form_factor="laptop")
    assert len(both.findings) == 6
    narrowed = find_devices(
        repo, as_of, os_name="macOS", only_devices=["MT7PJB7N5LRE", "8NM23J95R5I6"]
    )
    assert ids(narrowed) == ["MT7PJB7N5LRE"]
    assert narrowed.population == 2


def test_find_devices_is_tenant_scoped(real_conn):
    result = find_devices(repo_for(real_conn, "globex-002"), dataset_as_of(real_conn))
    assert result.population == 8
    assert not any(f.device_id == "MT7PJB7N5LRE" for f in result.findings)


def test_disk_history_for_a_declining_volume(real_conn):
    result = device_history(
        repo_for(real_conn),
        dataset_as_of(real_conn),
        device_id="MT7PJB7N5LRE",
        metric="disk_free_pct",
        window_days=30,
    )
    finding = result.findings[0]
    points = finding.values["points"]
    assert finding.values["snapshots"] == len(points) == 30
    assert points[0]["value"] > points[-1]["value"]
    assert len(finding.refs) == 30 and all(r.endswith("#disk") for r in finding.refs)
    assert [p["at"] for p in points] == sorted(p["at"] for p in points)


def test_compliance_history_shows_the_transition(real_conn):
    result = device_history(
        repo_for(real_conn),
        dataset_as_of(real_conn),
        device_id="1LYSSFD074BB",
        metric="compliance",
        check_id="screen_lock",
        window_days=30,
    )
    statuses = [p["value"] for p in result.findings[0].values["points"]]
    assert Counter(statuses) == {"pass": 8, "fail": 22}
    assert statuses[:8] == ["pass"] * 8


def test_battery_history_reports_raw_capacity(real_conn):
    result = device_history(
        repo_for(real_conn),
        dataset_as_of(real_conn),
        device_id="8NM23J95R5I6",
        metric="battery_capacity",
        window_days=30,
    )
    points = result.findings[0].values["points"]
    assert (points[0]["value"], points[-1]["value"]) == (4043, 3872)


def test_history_denies_foreign_and_unknown_devices_identically(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    messages = []
    for device in ("JRZSGXVMKE6M", "NOPE"):
        with pytest.raises(NotFound) as caught:
            device_history(repo, as_of, device_id=device, metric="memory_used_pct")
        messages.append(str(caught.value))
    assert messages == ["not found", "not found"]


def test_history_without_readings_is_reported_not_invented(real_conn):
    result = device_history(
        repo_for(real_conn),
        dataset_as_of(real_conn),
        device_id="DLT35MHGTH2D",
        metric="battery_capacity",
        window_days=30,
    )
    assert result.findings == ()
    assert result.excluded == {"no_readings": ("DLT35MHGTH2D",)}


def test_compliance_history_requires_a_check_id(real_conn):
    with pytest.raises(ValueError, match="check_id"):
        device_history(
            repo_for(real_conn),
            dataset_as_of(real_conn),
            device_id="1LYSSFD074BB",
            metric="compliance",
        )
