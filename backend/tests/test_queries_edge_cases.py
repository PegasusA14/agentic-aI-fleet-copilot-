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
