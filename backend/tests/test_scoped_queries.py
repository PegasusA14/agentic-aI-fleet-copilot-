import pytest
from factories import make_conn, make_row

from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of
from fleet_copilot.timeutil import parse_ts

ONE = "MT7PJB7N5LRE"  # acme-001 MacBook Pro: low disk, macOS 14.5, screen_lock transition, laptop
FOREIGN = "JRZSGXVMKE6M"  # a globex-002 device

QUERIES = {
    "low_disk": lambda repo, as_of, only: q.low_disk(repo, as_of, only_devices=only),
    "macos_below": lambda repo, as_of, only: q.macos_below(repo, as_of, only_devices=only),
    "compliance_failures": lambda repo, as_of, only: q.compliance_failures(
        repo, as_of, only_devices=only
    ),
    "memory_state": lambda repo, as_of, only: q.memory_state(repo, as_of, only_devices=only),
    "memory_trend": lambda repo, as_of, only: q.memory_trend(repo, as_of, only_devices=only),
    "disk_trend": lambda repo, as_of, only: q.disk_trend(repo, as_of, only_devices=only),
    "compliance_trend": lambda repo, as_of, only: q.compliance_trend(
        repo, as_of, check_id="screen_lock", only_devices=only
    ),
    "battery_overview": lambda repo, as_of, only: q.battery_overview(
        repo, as_of, only_devices=only
    ),
}


def repo_for(conn, tenant="acme-001"):
    return FleetRepository(conn, TenantContext(tenant, "scoped-test"))


@pytest.mark.parametrize("name", QUERIES)
def test_only_devices_narrows_to_the_requested_device(real_conn, name):
    result = QUERIES[name](repo_for(real_conn), dataset_as_of(real_conn), [ONE])
    assert result.population == 1
    assert {f.device_id for f in result.findings} <= {ONE}


@pytest.mark.parametrize("name", QUERIES)
def test_foreign_and_unknown_ids_are_silently_ignored(real_conn, name):
    result = QUERIES[name](repo_for(real_conn), dataset_as_of(real_conn), [FOREIGN, "NOPE"])
    assert result.population == 0
    assert result.findings == ()
    everything_mentioned = {i for ids in result.excluded.values() for i in ids} | set(result.stale)
    assert FOREIGN not in everything_mentioned


@pytest.mark.parametrize("name", QUERIES)
def test_empty_collection_means_nothing_and_none_means_everything(real_conn, name):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    nothing = QUERIES[name](repo, as_of, [])
    assert nothing.population == 0 and nothing.findings == ()
    assert QUERIES[name](repo, as_of, None).population > 0


@pytest.mark.parametrize(
    ("tenant", "expected"),
    [
        ("acme-001", ["M4XVHUV1MEPZ"]),
        ("globex-002", ["CUXW0BQSV1B7", "JRZSGXVMKE6M"]),
        ("initech-003", ["I3JLKILKQTAC"]),
    ],
)
def test_memory_state_at_the_default_threshold(real_conn, tenant, expected):
    result = q.memory_state(repo_for(real_conn, tenant), dataset_as_of(real_conn))
    assert sorted(f.device_id for f in result.findings) == expected
    used = [f.values["used_pct"] for f in result.findings]
    assert used == sorted(used, reverse=True)
    assert all(f.refs[0].endswith("#memory") for f in result.findings)


def test_memory_state_threshold_is_inclusive():
    row = make_row("A", memory={"total_memory_bytes": 100, "used_memory_bytes": 85})
    below = make_row("B", memory={"total_memory_bytes": 100, "used_memory_bytes": 84})
    repo = repo_for(make_conn([row, below]))
    result = q.memory_state(repo, parse_ts("2026-06-12T11:00:00Z"))
    assert [f.device_id for f in result.findings] == ["A"]
    assert result.population == 2
