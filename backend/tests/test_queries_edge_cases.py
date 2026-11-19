from factories import make_conn, make_row

from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext
from fleet_copilot.timeutil import parse_ts

AS_OF = parse_ts("2026-06-12T11:00:00Z")


def repo_for(rows, company="acme-001"):
    return FleetRepository(make_conn(rows), TenantContext(company, "test"))


def with_status(row, check_id, status):
    for check in row["compliance_results"]:
        if check["check_id"] == check_id:
            check["status"] = status
    return row


def test_unknown_and_non_macos_versions_are_reported_separately():
    windows = make_row("W", os={"product_name": "Windows", "product_version": "10 22H2", "hostname": "w"})
    unknown = make_row("U", os={"product_name": "macOS", "product_version": "Sonoma", "hostname": "u"})
    result = q.macos_below(repo_for([make_row("A"), unknown, windows]), AS_OF, "15")
    assert [f.device_id for f in result.findings] == ["A"]
    assert result.excluded == {"out_of_scope": ("W",), "unknown_version": ("U",)}
    assert result.population == 2  # macOS devices only


def test_stale_devices_are_flagged_not_hidden():
    rows = [make_row("FRESH"), make_row("OLD", ts="2026-06-01T09:00:00Z")]
    result = q.low_disk(repo_for(rows), AS_OF, threshold_pct=99.0)  # both are 50% free
    assert sorted(f.device_id for f in result.findings) == ["FRESH", "OLD"]
    assert result.stale == ("OLD",)


def test_empty_result_keeps_its_population():
    result = q.compliance_failures(repo_for([make_row("A"), make_row("B")]), AS_OF, severity="high")
    assert result.findings == ()
    assert result.population == 2


def test_compliance_filters_combine():
    failing = with_status(make_row("A"), "screen_lock", "fail")
    mini = with_status(make_row("MINI", battery=None), "screen_lock", "fail")
    repo = repo_for([failing, mini])
    both = q.compliance_failures(repo, AS_OF, check_id="screen_lock")
    laptops = q.compliance_failures(repo, AS_OF, check_id="screen_lock", form_factor="laptop")
    assert [f.device_id for f in both.findings] == ["A", "MINI"]
    assert [f.device_id for f in laptops.findings] == ["A"]
    assert laptops.findings[0].refs == ("acme-001/A@2026-06-12T09:00:00Z#compliance[screen_lock]",)


def test_battery_missing_in_the_latest_snapshot_is_disclosed():
    rows = [
        make_row("L", ts="2026-06-10T09:00:00Z"),
        make_row("L", ts="2026-06-11T09:00:00Z"),
        make_row("L", ts="2026-06-12T09:00:00Z", battery=None),
    ]
    result = q.battery_overview(repo_for(rows), AS_OF, 30)
    assert result.missing_latest == ("L",)
    assert result.findings == ()  # healthy and flat: no claim made
    assert "no_reading" not in result.excluded


def test_laptop_with_no_battery_readings_in_the_window_is_excluded():
    rows = [make_row("L", ts="2026-04-01T09:00:00Z"), make_row("L", ts="2026-06-12T09:00:00Z", battery=None)]
    result = q.battery_overview(repo_for(rows), AS_OF, 7)
    assert result.excluded["no_reading"] == ("L",)


def test_sparse_history_is_insufficient_not_a_trend():
    result = q.memory_trend(repo_for([make_row("A"), make_row("B", ts="2026-06-11T09:00:00Z")]), AS_OF, 7, 1.0)
    assert result.findings == ()
    assert result.excluded["insufficient_history"] == ("A", "B")


def test_sustained_breach_cites_every_breached_snapshot():
    rows = [make_row("A", ts=f"2026-06-{day:02d}T09:00:00Z") for day in (8, 9, 10, 11, 12)]
    result = q.memory_trend(repo_for(rows), AS_OF, 7, threshold_pct=40.0)  # used is 50%
    finding = result.findings[0]
    assert finding.values["trend"] == "sustained"
    assert finding.values["breached"] == 5 and finding.values["snapshots"] == 5
    assert len(finding.refs) == 5


def test_compliance_transition_cites_the_last_pass_the_first_fail_and_the_latest():
    rows = [
        make_row("A", ts="2026-06-10T09:00:00Z"),
        with_status(make_row("A", ts="2026-06-11T09:00:00Z"), "screen_lock", "fail"),
        with_status(make_row("A", ts="2026-06-12T09:00:00Z"), "screen_lock", "fail"),
    ]
    finding = q.compliance_trend(repo_for(rows), AS_OF, check_id="screen_lock", window_days=7).findings[0]
    assert finding.values["kind"] == "transition"
    assert finding.values["change_at"] == "2026-06-11T09:00:00Z"
    assert finding.refs == (
        "acme-001/A@2026-06-10T09:00:00Z#compliance[screen_lock]",
        "acme-001/A@2026-06-11T09:00:00Z#compliance[screen_lock]",
        "acme-001/A@2026-06-12T09:00:00Z#compliance[screen_lock]",
    )


def battery(condition="Normal", capacity=5000, cycles=100):
    return {
        "battery_present": True,
        "condition": condition,
        "cycle_count": cycles,
        "full_charge_capacity": capacity,
        "percentage": 80,
    }


def day(n, hour=9):
    return f"2026-06-{n:02d}T{hour:02d}:00:00Z"


def test_battery_service_streak_start_is_cited():
    conditions = ["Normal", "Normal", "Service Recommended", "Service Recommended", "Service Recommended"]
    rows = [make_row("L", ts=day(8 + i), battery=battery(c)) for i, c in enumerate(conditions)]
    finding = q.battery_overview(repo_for(rows), AS_OF, 30).findings[0]
    assert finding.values["service_streak"] == 3
    assert finding.values["first_service_at"] == day(10)
    assert "acme-001/L@2026-06-10T09:00:00Z#battery" in finding.refs
    assert len(finding.refs) == len(set(finding.refs))


def test_battery_capacity_baseline_and_latest_are_cited_when_early_readings_lack_capacity():
    rows = [
        make_row("L", ts=day(10), battery=battery(capacity=None)),
        make_row("L", ts=day(11), battery=battery(capacity=5000)),
        make_row("L", ts=day(12), battery=battery(capacity=4000)),
    ]
    finding = q.battery_overview(repo_for(rows), AS_OF, 30).findings[0]
    assert finding.values["capacity_change_pct"] == -20.0
    assert "acme-001/L@2026-06-11T09:00:00Z#battery" in finding.refs  # the 5000 baseline
    assert "acme-001/L@2026-06-12T09:00:00Z#battery" in finding.refs  # the 4000 reading
    assert "acme-001/L@2026-06-10T09:00:00Z#battery" not in finding.refs  # reported no capacity


def test_single_service_reading_yields_no_duplicate_refs():
    rows = [make_row("L", ts=day(12), battery=battery("Service Recommended"))]
    finding = q.battery_overview(repo_for(rows), AS_OF, 30).findings[0]
    assert finding.refs == ("acme-001/L@2026-06-12T09:00:00Z#battery",)
    assert len(finding.refs) == len(set(finding.refs))


def test_latest_value_is_cited_even_when_the_latest_snapshot_is_not_in_breach():
    rows = [make_row("A", ts=day(d)) for d in (8, 9, 10, 11)]
    rows.append(make_row("A", ts=day(12), memory={"total_memory_bytes": 100, "used_memory_bytes": 10}))
    finding = q.memory_trend(repo_for(rows), AS_OF, 7, threshold_pct=40.0).findings[0]
    assert finding.values["trend"] == "recovering"
    assert finding.values["latest_value"] == 10.0
    assert finding.refs[-1] == "acme-001/A@2026-06-12T09:00:00Z#memory"
    assert len(finding.refs) == 5
    assert len(finding.refs) == len(set(finding.refs))


def test_disk_trend_cites_disk_snapshots():
    rows = [make_row("A", ts=day(d)) for d in (8, 9, 10, 11, 12)]  # default disk is 50% free
    finding = q.disk_trend(repo_for(rows), AS_OF, 7, threshold_pct=60.0).findings[0]
    assert finding.values["trend"] == "sustained"
    assert len(finding.refs) == 5
    assert all(ref.endswith("#disk") for ref in finding.refs)


def test_trend_queries_disclose_staleness():
    old = [make_row("OLD", ts=day(d)) for d in (1, 2, 3, 4, 5)]
    old[2:] = [with_status(r, "screen_lock", "fail") for r in old[2:]]
    fresh = [make_row("FRESH", ts=day(d)) for d in (8, 9, 10, 11, 12)]
    repo = repo_for(old + fresh)
    memory = q.memory_trend(repo, AS_OF, window_days=30, threshold_pct=40.0)
    assert [f.device_id for f in memory.findings] == ["FRESH", "OLD"]
    assert memory.stale == ("OLD",)
    disk = q.disk_trend(repo, AS_OF, window_days=30, threshold_pct=60.0)
    assert disk.stale == ("OLD",)
    compliance = q.compliance_trend(repo, AS_OF, check_id="screen_lock", window_days=30)
    assert [f.device_id for f in compliance.findings] == ["OLD"]
    assert compliance.findings[0].values["kind"] == "transition"
    assert compliance.stale == ("OLD",)
