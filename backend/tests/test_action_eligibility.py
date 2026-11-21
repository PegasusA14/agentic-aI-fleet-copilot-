from __future__ import annotations

import json
import re

import pytest
from factories import make_conn, make_row

from fleet_copilot import policy
from fleet_copilot.actions import eligibility as el
from fleet_copilot.agent import texts
from fleet_copilot.config import REPO_ROOT, Settings
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of
from fleet_copilot.timeutil import parse_ts

GOLDEN = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
GIB = 2**30
AS_OF = parse_ts("2026-06-12T11:00:00Z")
JUDGMENT = re.compile(
    r"\b(bad|poor|good|great|healthy|unhealthy|worrying|serious|severe|critical|urgent|"
    r"dangerous|risky|unsafe|terrible|alarming|concerning|fine|ok|okay)\b",
    re.IGNORECASE,
)


def repo_for(conn, tenant):
    return FleetRepository(conn, TenantContext(tenant, "eligibility-test"))


def synthetic(rows, company="acme-001"):
    return FleetRepository(make_conn(rows), TenantContext(company, "eligibility-test"))


def assert_refs_resolve(repo, eligible):
    assert 1 <= len(eligible.refs) <= policy.MAX_REFS_PER_FINDING
    for ref in eligible.refs:
        assert repo.resolve(EvidenceRef.parse(ref)).ref == ref


def assert_texts_clean(eligible):
    assert eligible.expected_effect.endswith("(simulated)")
    assert eligible.rationale
    assert not JUDGMENT.search(eligible.rationale)
    assert not JUDGMENT.search(eligible.expected_effect)


@pytest.fixture(scope="module")
def as_of(real_conn):
    return dataset_as_of(real_conn)


# ---- ticket -------------------------------------------------------------------------------------


@pytest.mark.parametrize("tenant", sorted(GOLDEN["tenants"]))
def test_every_device_failing_screen_lock_is_eligible_for_a_ticket(real_conn, as_of, tenant):
    repo = repo_for(real_conn, tenant)
    failing = GOLDEN["tenants"][tenant]["failing_latest"]["all"]["screen_lock"]
    assert failing
    for device in repo.list_devices():
        got = el.check(repo, as_of, "remediation_ticket", device.device_id, check_id="screen_lock")
        if device.device_id not in failing:
            assert got == el.Ineligible("not_eligible", got.detail)
            continue
        assert isinstance(got, el.Eligible), (device.device_id, got)
        assert got.action_type == "remediation_ticket"
        assert got.target_label == device.hostname
        assert got.params == {"check_id": "screen_lock", "severity": "medium"}
        assert got.refs[0].endswith("#compliance[screen_lock]")
        assert got.employee_id is None
        assert_refs_resolve(repo, got)
        assert_texts_clean(got)


def test_a_ticket_cites_the_first_failure_in_the_window_when_it_differs(real_conn, as_of):
    repo = repo_for(real_conn, "acme-001")
    got = el.check(repo, as_of, "remediation_ticket", "8NM23J95R5I6", check_id="screen_lock")
    assert isinstance(got, el.Eligible)
    stamps = {ref.split("@")[1].split("#")[0] for ref in got.refs}
    assert len(got.refs) == len(stamps) <= 2


def test_a_check_that_passes_everywhere_is_not_eligible(real_conn, as_of):
    repo = repo_for(real_conn, "acme-001")
    for device in repo.list_devices():
        got = el.check(
            repo, as_of, "remediation_ticket", device.device_id, check_id="disk_encryption"
        )
        assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"


def test_a_ticket_needs_a_check_id(real_conn, as_of):
    got = el.check(repo_for(real_conn, "acme-001"), as_of, "remediation_ticket", "8NM23J95R5I6")
    assert got == el.Ineligible("insufficient_evidence", "check_id is required")


def test_an_unknown_check_is_not_eligible(real_conn, as_of):
    got = el.check(
        repo_for(real_conn, "acme-001"),
        as_of,
        "remediation_ticket",
        "8NM23J95R5I6",
        check_id="nope",
    )
    assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"


# ---- replacement --------------------------------------------------------------------------------


def raw_service_runs():
    """Acme devices whose last three battery readings are all 'Service Recommended' (raw NDJSON)."""
    readings: dict[str, list[tuple[str, str]]] = {}
    for line in Settings.from_env().data_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        battery = row.get("battery")
        if row["company_id"] != "acme-001" or not battery or not battery.get("battery_present"):
            continue
        readings.setdefault(row["device_id"], []).append(
            (row["collected_at"], battery.get("condition"))
        )
    return {
        d: sorted(r)
        for d, r in readings.items()
        if all(c == "Service Recommended" for _, c in sorted(r)[-3:])
    }


def test_the_raw_data_has_exactly_three_service_recommended_runs():
    assert set(raw_service_runs()) == {"7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"}


def test_replacement_is_eligible_for_exactly_the_raw_service_runs(real_conn, as_of):
    repo = repo_for(real_conn, "acme-001")
    expected = set(raw_service_runs())
    eligible = set()
    for device in repo.list_devices():
        got = el.check(repo, as_of, "replacement_flag", device.device_id)
        if isinstance(got, el.Eligible):
            eligible.add(device.device_id)
            assert got.params == {"reason": "battery_service_recommended"}
            assert all(ref.endswith("#battery") for ref in got.refs)
            assert_refs_resolve(repo, got)
            assert_texts_clean(got)
            assert "Service Recommended" in got.rationale
        else:
            assert got.reason == "not_eligible"
    assert eligible == expected


def test_a_device_without_a_battery_is_not_eligible_for_replacement(real_conn, as_of):
    repo = repo_for(real_conn, "acme-001")
    assert repo.latest("DSYPSES7SKBB") is not None
    laptops = set(GOLDEN["tenants"]["acme-001"]["laptops"])
    minis = [d.device_id for d in repo.list_devices() if d.device_id not in laptops]
    assert minis
    for device_id in minis:
        got = el.check(repo, as_of, "replacement_flag", device_id)
        assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"


def service(condition):
    return {
        "battery_present": True,
        "condition": condition,
        "cycle_count": 900,
        "full_charge_capacity": 4000,
        "percentage": 50,
    }


def day(n, hour=9):
    return f"2026-06-{n:02d}T{hour:02d}:00:00Z"


def test_a_service_run_shorter_than_the_minimum_or_broken_at_the_end_is_not_eligible():
    short = [
        make_row("L", ts=day(10), battery=service("Normal")),
        make_row("L", ts=day(11), battery=service("Service Recommended")),
        make_row("L", ts=day(12), battery=service("Service Recommended")),
    ]
    assert policy.REPLACEMENT_MIN_SNAPSHOTS == 3
    got = el.check(synthetic(short), AS_OF, "replacement_flag", "L")
    assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"
    ended = [
        make_row("L", ts=day(n), battery=service("Service Recommended")) for n in (8, 9, 10, 11)
    ]
    ended.append(make_row("L", ts=day(12), battery=service("Normal")))
    got = el.check(synthetic(ended), AS_OF, "replacement_flag", "L")
    assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"


def test_a_service_run_of_three_is_eligible_and_cites_the_run():
    rows = [make_row("L", ts=day(n), battery=service("Service Recommended")) for n in (10, 11, 12)]
    repo = synthetic(rows)
    got = el.check(repo, AS_OF, "replacement_flag", "L")
    assert isinstance(got, el.Eligible)
    assert len(got.refs) == 3
    assert_refs_resolve(repo, got)


# ---- upgrades -----------------------------------------------------------------------------------


def sustained(tenant, key, window="7"):
    return {
        d for d, trend in GOLDEN["tenants"][tenant][key][window].items() if trend == "sustained"
    }


def labels(repo, device_id):
    latest = repo.latest(device_id)
    return latest.mem_total_bytes, latest.disk_size_bytes


MEM_TARGET = {8 * GIB: ("8 GiB", "16 GiB"), 16 * GIB: ("16 GiB", "32 GiB")}
DISK_TARGET = {
    256 * GIB: ("256 GiB", "512 GiB"),
    512 * GIB: ("512 GiB", "1 TiB"),
    1024 * GIB: ("1 TiB", "2 TiB"),
}


@pytest.mark.parametrize("tenant", sorted(GOLDEN["tenants"]))
def test_memory_upgrade_is_eligible_exactly_for_sustained_seven_day_devices(
    real_conn, as_of, tenant
):
    repo = repo_for(real_conn, tenant)
    want = {
        d
        for d, t in GOLDEN["tenants"][tenant]["memory_trend"]["85"]["7"].items()
        if t == "sustained"
    }
    got_ids = set()
    for device in repo.list_devices():
        got = el.check(repo, as_of, "upgrade_order", device.device_id, component="memory")
        if isinstance(got, el.Eligible):
            got_ids.add(device.device_id)
            total, _ = labels(repo, device.device_id)
            current, target = MEM_TARGET[total]
            assert got.params == {"component": "memory", "current": current, "target": target}
            assert all(ref.endswith("#memory") for ref in got.refs)
            assert_refs_resolve(repo, got)
            assert_texts_clean(got)
            assert current in got.rationale and target in got.expected_effect
        else:
            assert got.reason == "not_eligible"
    assert got_ids == want


def test_the_documented_memory_sizes_come_from_the_data(real_conn, as_of):
    for tenant, device_id, expected in [
        ("acme-001", "M4XVHUV1MEPZ", ("16 GiB", "32 GiB")),
        ("initech-003", "I3JLKILKQTAC", ("8 GiB", "16 GiB")),
        ("globex-002", "JRZSGXVMKE6M", ("16 GiB", "32 GiB")),
    ]:
        got = el.check(
            repo_for(real_conn, tenant), as_of, "upgrade_order", device_id, component="memory"
        )
        assert isinstance(got, el.Eligible)
        assert (got.params["current"], got.params["target"]) == expected


def test_a_transient_memory_device_is_not_eligible(real_conn, as_of):
    got = el.check(
        repo_for(real_conn, "globex-002"),
        as_of,
        "upgrade_order",
        "CUXW0BQSV1B7",
        component="memory",
    )
    assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"


@pytest.mark.parametrize("tenant", sorted(GOLDEN["tenants"]))
def test_storage_upgrade_is_eligible_exactly_for_sustained_seven_day_devices(
    real_conn, as_of, tenant
):
    repo = repo_for(real_conn, tenant)
    want = sustained(tenant, "disk_trend_lt10")
    assert want
    got_ids = set()
    for device in repo.list_devices():
        got = el.check(repo, as_of, "upgrade_order", device.device_id, component="storage")
        if isinstance(got, el.Eligible):
            got_ids.add(device.device_id)
            _, size = labels(repo, device.device_id)
            current, target = DISK_TARGET[size]
            assert got.params == {"component": "storage", "current": current, "target": target}
            assert all(ref.endswith("#disk") for ref in got.refs)
            assert_refs_resolve(repo, got)
            assert_texts_clean(got)
        else:
            assert got.reason == "not_eligible"
    assert got_ids == want


def test_upgrade_target_policy():
    assert el.upgrade_target("memory", 8 * GIB) == ("8 GiB", "16 GiB")
    assert el.upgrade_target("memory", 16 * GIB) == ("16 GiB", "32 GiB")
    assert el.upgrade_target("memory", 32 * GIB) == ("32 GiB", "64 GiB")
    assert el.upgrade_target("storage", 256 * GIB) == ("256 GiB", "512 GiB")
    assert el.upgrade_target("storage", 512 * GIB) == ("512 GiB", "1 TiB")
    assert el.upgrade_target("storage", 1024 * GIB) == ("1 TiB", "2 TiB")
    assert el.upgrade_target("storage", 2048 * GIB) is None
    assert el.upgrade_target("storage", 4096 * GIB) is None
    assert el.upgrade_target("storage", 0) is None
    assert el.upgrade_target("memory", 0) is None


def test_a_sustained_two_tib_disk_is_not_eligible_for_a_storage_upgrade():
    full = [
        {
            "mount_point": "/",
            "size_bytes": 2048 * GIB,
            "available_bytes": 10 * GIB,
            "encrypted": True,
        }
    ]
    rows = [make_row("BIG", ts=day(n), disk_volumes=full) for n in (8, 9, 10, 11, 12)]
    got = el.check(synthetic(rows), AS_OF, "upgrade_order", "BIG", component="storage")
    assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"
    assert "no larger standard size" in got.detail


def test_an_upgrade_needs_a_component(real_conn, as_of):
    got = el.check(repo_for(real_conn, "acme-001"), as_of, "upgrade_order", "M4XVHUV1MEPZ")
    assert got == el.Ineligible("insufficient_evidence", "component is required")


def test_too_little_history_for_a_trend_is_insufficient_evidence():
    rows = [
        make_row(
            "M", ts=day(n), memory={"total_memory_bytes": 16 * GIB, "used_memory_bytes": 15 * GIB}
        )
        for n in (11, 12)
    ]
    got = el.check(synthetic(rows), AS_OF, "upgrade_order", "M", component="memory")
    assert isinstance(got, el.Ineligible) and got.reason == "insufficient_evidence"


# ---- notification -------------------------------------------------------------------------------


def test_low_disk_notification_carries_the_employee_and_the_free_percentage(real_conn, as_of):
    repo = repo_for(real_conn, "acme-001")
    device = repo.get_device("MT7PJB7N5LRE")
    got = el.check(repo, as_of, "employee_notification", "MT7PJB7N5LRE", topic="low_disk")
    assert isinstance(got, el.Eligible)
    assert got.employee_id == device.employee_id
    assert got.params["topic"] == "low_disk"
    assert "2.0% free" in got.params["message"]
    assert got.params["message"] == el.notification_message(
        "low_disk", device.hostname, {"free_pct": "2.0"}
    )
    assert device.hostname in got.params["message"]
    assert set(got.params) == {"topic", "message"}
    assert got.refs and all(ref.endswith("#disk") for ref in got.refs)
    assert_refs_resolve(repo, got)
    assert_texts_clean(got)


def test_a_healthy_device_is_not_eligible_for_a_low_disk_notification(real_conn, as_of):
    repo = repo_for(real_conn, "acme-001")
    got = el.check(repo, as_of, "employee_notification", "Q3VUITEGQ400", topic="low_disk")
    assert isinstance(got, el.Ineligible) and got.reason == "not_eligible"


def test_a_notification_needs_a_topic(real_conn, as_of):
    got = el.check(repo_for(real_conn, "acme-001"), as_of, "employee_notification", "MT7PJB7N5LRE")
    assert got == el.Ineligible("insufficient_evidence", "topic is required")


def test_notification_templates_are_fixed():
    assert (
        el.notification_message("low_disk", "h1", {"free_pct": "2.0"})
        == "Your device h1 is low on disk space (2.0% free). IT may contact you to help free up space."
    )
    assert (
        el.notification_message("memory_pressure", "h1", {"used_pct": "91.5"})
        == "Your device h1 is running with high memory use (91.5% used). IT may contact you about it."
    )
    assert (
        el.notification_message("failing_check", "h1", {"check_id": "screen_lock"})
        == "Your device h1 is failing the screen_lock compliance check. IT may contact you to fix it."
    )
    assert (
        el.notification_message("battery_service", "h1", {})
        == "The battery in your device h1 reports Service Recommended. IT may contact you about a replacement."
    )


def test_the_other_notification_topics(real_conn, as_of):
    acme = repo_for(real_conn, "acme-001")
    memory = el.check(acme, as_of, "employee_notification", "M4XVHUV1MEPZ", topic="memory_pressure")
    assert isinstance(memory, el.Eligible)
    assert "% used" in memory.params["message"] and memory.refs[0].endswith("#memory")
    assert_refs_resolve(acme, memory)
    battery = el.check(
        acme, as_of, "employee_notification", "7VP16KHV88LM", topic="battery_service"
    )
    assert isinstance(battery, el.Eligible)
    assert battery.refs[0].endswith("#battery")
    assert_refs_resolve(acme, battery)
    failing = el.check(acme, as_of, "employee_notification", "DLT35MHGTH2D", topic="failing_check")
    assert isinstance(failing, el.Eligible)
    assert "screen_lock" in failing.params["message"]
    assert "#compliance[screen_lock]" in failing.refs[0]
    assert_refs_resolve(acme, failing)
    for got in (memory, battery, failing):
        assert_texts_clean(got)
    healthy = "Q3VUITEGQ400"
    for topic in ("memory_pressure", "battery_service"):
        refused = el.check(acme, as_of, "employee_notification", healthy, topic=topic)
        assert isinstance(refused, el.Ineligible) and refused.reason == "not_eligible"
    refused = el.check(
        acme, as_of, "employee_notification", "1LYSSFD074BB", topic="battery_service"
    )
    assert isinstance(refused, el.Ineligible)
    unknown = el.check(acme, as_of, "employee_notification", "MT7PJB7N5LRE", topic="gossip")
    assert isinstance(unknown, el.Ineligible)


# ---- isolation ----------------------------------------------------------------------------------

CALLS = [
    ("remediation_ticket", {"check_id": "screen_lock"}),
    ("replacement_flag", {}),
    ("upgrade_order", {"component": "memory"}),
    ("upgrade_order", {"component": "storage"}),
    ("employee_notification", {"topic": "low_disk"}),
]


@pytest.mark.parametrize(("action", "extra"), CALLS)
def test_foreign_and_unknown_devices_are_indistinguishable(real_conn, as_of, action, extra):
    repo = repo_for(real_conn, "acme-001")
    foreign = el.check(repo, as_of, action, "CUXW0BQSV1B7", **extra)
    unknown = el.check(repo, as_of, action, "NOSUCHDEVICE", **extra)
    assert foreign == unknown == el.Ineligible("not_found", texts.NOT_FOUND)


# ---- stale readings -----------------------------------------------------------------------------


def stale_rows():
    """Every condition true, but the newest reading is more than 48 hours before the as-of."""
    full = [{"mount_point": "/", "size_bytes": 1000, "available_bytes": 20, "encrypted": True}]
    memory = {"total_memory_bytes": 16 * GIB, "used_memory_bytes": 15 * GIB}
    checks = [
        {"check_id": "disk_encryption", "status": "pass", "severity": "high"},
        {"check_id": "screen_lock", "status": "fail", "severity": "medium"},
        {"check_id": "os_up_to_date", "status": "pass", "severity": "medium"},
    ]
    return [
        make_row(
            "S",
            ts=day(n),
            battery=service("Service Recommended"),
            disk_volumes=full,
            memory=memory,
            compliance_results=checks,
        )
        for n in (5, 6, 7, 8, 9)
    ]


def test_the_stale_fixture_is_eligible_when_fresh():
    repo = synthetic(stale_rows())
    fresh = parse_ts("2026-06-09T12:00:00Z")
    for action, extra in CALLS:
        got = el.check(repo, fresh, action, "S", **extra)
        assert isinstance(got, el.Eligible), (action, extra, got)


@pytest.mark.parametrize(("action", "extra"), CALLS)
def test_a_reading_older_than_the_stale_limit_is_insufficient_evidence(action, extra):
    got = el.check(synthetic(stale_rows()), AS_OF, action, "S", **extra)
    assert isinstance(got, el.Ineligible) and got.reason == "insufficient_evidence"
    assert not JUDGMENT.search(got.detail)
