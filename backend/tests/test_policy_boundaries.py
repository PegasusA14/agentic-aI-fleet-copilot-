"""Policy comparisons pinned exactly at their thresholds.

The real data never lands exactly on a threshold, so `<` vs `<=` or `>=` vs `>` would
go unnoticed there. These synthetic devices sit exactly on, and one step beside, each limit.
"""

from datetime import UTC, datetime

import pytest
from factories import make_conn, make_row

from fleet_copilot.domain.windows import window_start
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext
from fleet_copilot.timeutil import parse_ts

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
FRESH = ["2026-06-10T09:00:00Z", "2026-06-11T09:00:00Z", "2026-06-12T09:00:00Z"]


def repo_for(rows):
    return FleetRepository(make_conn(rows), TenantContext("acme-001", "boundary-test"))


def disk(size, available):
    return [
        {"mount_point": "/", "size_bytes": size, "available_bytes": available, "encrypted": True}
    ]


def memory(total, used):
    return {"total_memory_bytes": total, "used_memory_bytes": used}


def battery(capacity, condition="Normal"):
    return {
        "battery_present": True,
        "condition": condition,
        "cycle_count": 100,
        "full_charge_capacity": capacity,
        "percentage": 80,
    }


def ids(result):
    return [f.device_id for f in result.findings]


# ---- disk: free space below 10.0% is low; exactly 10.0% is not


@pytest.mark.parametrize(("available", "is_low"), [(100, False), (99, True)])
def test_low_disk_threshold_is_exclusive(available, is_low):
    repo = repo_for([make_row("D1", ts=FRESH[-1], disk_volumes=disk(1000, available))])
    result = q.low_disk(repo, AS_OF)
    assert result.population == 1
    assert ids(result) == (["D1"] if is_low else [])
    if is_low:
        assert result.findings[0].values["free_pct"] == pytest.approx(9.9)


@pytest.mark.parametrize(("available", "is_breach"), [(100, False), (99, True)])
def test_disk_trend_threshold_is_exclusive(available, is_breach):
    repo = repo_for([make_row("D1", ts=ts, disk_volumes=disk(1000, available)) for ts in FRESH])
    result = q.disk_trend(repo, AS_OF)
    assert ids(result) == (["D1"] if is_breach else [])
    if is_breach:
        assert result.findings[0].values["trend"] == "sustained"
        assert result.findings[0].values["breached"] == 3


# ---- memory: used at or above 85.0% is a breach; 84% is not


@pytest.mark.parametrize(("used", "is_breach"), [(85, True), (84, False)])
def test_memory_trend_threshold_is_inclusive(used, is_breach):
    repo = repo_for([make_row("D1", ts=ts, memory=memory(100, used)) for ts in FRESH])
    result = q.memory_trend(repo, AS_OF)
    assert ids(result) == (["D1"] if is_breach else [])
    if is_breach:
        assert result.findings[0].values["latest_value"] == 85.0
        assert result.findings[0].values["breached"] == 3


# ---- window start: a snapshot exactly seven days before as-of is inside the window

WINDOW_EDGE = "2026-06-05T11:00:00Z"
JUST_BEFORE = "2026-06-05T10:59:59Z"


def test_window_start_is_inclusive_in_the_repository():
    assert window_start(AS_OF, 7) == parse_ts(WINDOW_EDGE)
    repo = repo_for([make_row("D1", ts=ts) for ts in (JUST_BEFORE, WINDOW_EDGE, FRESH[-1])])
    since = window_start(AS_OF, 7)
    assert [s.collected_at for s in repo.snapshots("D1", since=since)] == [
        parse_ts(WINDOW_EDGE),
        parse_ts(FRESH[-1]),
    ]


def test_window_start_is_inclusive_in_a_trend_query():
    # three readings need the one on the edge; the one second earlier must stay out
    stamps = [JUST_BEFORE, WINDOW_EDGE, "2026-06-08T09:00:00Z", FRESH[-1]]
    repo = repo_for([make_row("D1", ts=ts, memory=memory(100, 90)) for ts in stamps])
    finding = q.memory_trend(repo, AS_OF, window_days=7).findings[0]
    assert finding.values["snapshots"] == 3
    assert all(not r.startswith(f"acme-001/D1@{JUST_BEFORE}") for r in finding.refs)
    assert any(r.startswith(f"acme-001/D1@{WINDOW_EDGE}") for r in finding.refs)


# ---- battery: capacity must fall by MORE than 1.0% to be reported


@pytest.mark.parametrize(("last_capacity", "reported"), [(9900, False), (9899, True)])
def test_battery_capacity_cutoff_is_exclusive(last_capacity, reported):
    rows = [
        make_row("L1", ts=FRESH[0], battery=battery(10000)),
        make_row("L1", ts=FRESH[-1], battery=battery(last_capacity)),
    ]
    result = q.battery_overview(repo_for(rows), AS_OF)
    assert result.population == 1
    assert ids(result) == (["L1"] if reported else [])
    if reported:
        assert result.findings[0].values["capacity_change_pct"] == pytest.approx(-1.01)
        assert result.findings[0].values["readings"] == 2


# ---- staleness: exactly 48 hours old is still current; one second older is stale


@pytest.mark.parametrize(
    ("stamp", "is_stale"), [("2026-06-10T11:00:00Z", False), ("2026-06-10T10:59:59Z", True)]
)
def test_stale_limit_is_exclusive_at_query_level(stamp, is_stale):
    repo = repo_for([make_row("D1", ts=stamp)])
    assert q.low_disk(repo, AS_OF).stale == (("D1",) if is_stale else ())
