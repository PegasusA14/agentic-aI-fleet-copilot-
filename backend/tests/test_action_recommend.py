from __future__ import annotations

import json

import pytest
from factories import make_conn, make_row

from fleet_copilot import policy
from fleet_copilot.actions import eligibility as el
from fleet_copilot.actions import recommend as rec
from fleet_copilot.config import REPO_ROOT, Settings
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

GOLDEN = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
ACME = GOLDEN["tenants"]["acme-001"]
LOW_DISK = sorted(ACME["disk_trend_lt10"]["7"])  # sustained over 7 days
MEMORY = sorted(ACME["memory_trend"]["85"]["7"])
BIG = 1000  # a cap that never bites


def repo_for(conn, tenant="acme-001"):
    return FleetRepository(conn, TenantContext(tenant, "recommend-test"))


@pytest.fixture(scope="module")
def as_of(real_conn):
    return dataset_as_of(real_conn)


@pytest.fixture(scope="module")
def raw_latest():
    """The newest raw row per Acme device, read straight from the NDJSON."""
    latest: dict[str, dict] = {}
    for line in Settings.from_env().data_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["company_id"] != "acme-001":
            continue
        d = row["device_id"]
        if d not in latest or row["collected_at"] > latest[d]["collected_at"]:
            latest[d] = row
    return latest


@pytest.fixture
def uncapped(monkeypatch):
    monkeypatch.setattr(policy, "MAX_SUGGESTIONS", BIG, raising=False)


def of_type(suggestions, action_type, device_id=None):
    return [
        s
        for s in suggestions
        if s.action_type == action_type and (device_id is None or s.device_id == device_id)
    ]


def test_the_golden_expectations_the_tests_rely_on_exist():
    assert LOW_DISK == ["8NM23J95R5I6", "MT7PJB7N5LRE"]
    assert MEMORY == ["M4XVHUV1MEPZ"]


def test_sustained_low_disk_devices_get_storage_upgrades(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, LOW_DISK)
    expected = {"8NM23J95R5I6": ("1 TiB", "2 TiB"), "MT7PJB7N5LRE": ("512 GiB", "1 TiB")}
    for device_id, (current, target) in expected.items():
        (upgrade,) = [
            s
            for s in of_type(out, "upgrade_order", device_id)
            if s.params["component"] == "storage"
        ]
        assert upgrade.params == {"component": "storage", "current": current, "target": target}
        hostname = upgrade.target_label
        assert upgrade.label == f"Order a storage upgrade ({current} → {target}) for {hostname}"
        assert upgrade.request == f"Order a storage upgrade for {hostname}"
        assert hostname in upgrade.reason
        assert upgrade.reason.rstrip().endswith(".")


def test_at_most_one_notification_per_device(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, [d.device_id for d in repo.list_devices()])
    for device in repo.list_devices():
        assert len(of_type(out, "employee_notification", device.device_id)) <= 1
    assert of_type(out, "employee_notification")  # and at least some exist


def test_the_notification_is_for_the_strongest_current_finding(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, ["8NM23J95R5I6"])
    (note,) = of_type(out, "employee_notification")
    # this device is a sustained low-disk device with a service battery: the battery is stronger
    assert note.params["topic"] == "battery_service"
    assert note.request.startswith("Send")
    assert note.target_label in note.request
    assert note.label == f"Notify the owner of {note.target_label} about battery service"


def test_a_low_disk_device_without_battery_findings_is_notified_about_low_disk(
    real_conn, as_of, uncapped
):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, ["MT7PJB7N5LRE"])
    (note,) = of_type(out, "employee_notification")
    assert note.params["topic"] == "low_disk"


def test_memory_pressure_device_gets_a_memory_upgrade_and_a_replacement_flag(
    real_conn, as_of, uncapped, raw_latest
):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, MEMORY)
    (mem,) = [s for s in of_type(out, "upgrade_order") if s.params["component"] == "memory"]
    assert mem.device_id == "M4XVHUV1MEPZ"
    assert (mem.params["current"], mem.params["target"]) == ("16 GiB", "32 GiB")
    assert mem.label == f"Order a memory upgrade (16 GiB → 32 GiB) for {mem.target_label}"
    (flag,) = of_type(out, "replacement_flag")
    assert flag.device_id == "M4XVHUV1MEPZ"
    assert flag.label == f"Flag {flag.target_label} for replacement"
    assert flag.request == f"Flag {flag.target_label} for replacement"
    assert raw_latest["M4XVHUV1MEPZ"]["battery"]["condition"] == "Service Recommended"


def test_a_ticket_per_currently_failing_check_with_the_data_severity(
    real_conn, as_of, uncapped, raw_latest
):
    repo = repo_for(real_conn)
    ids = sorted(ACME["devices"])
    out = rec.recommend(repo, as_of, ids)
    failing = ACME["failing_latest"]["all"]
    for check_id, devices in failing.items():
        for device_id in ids:
            tickets = [
                s
                for s in of_type(out, "remediation_ticket", device_id)
                if s.params["check_id"] == check_id
            ]
            if device_id not in devices:
                assert tickets == []
                continue
            (ticket,) = tickets
            severity = next(
                c["severity"]
                for c in raw_latest[device_id]["compliance_results"]
                if c["check_id"] == check_id
            )
            assert ticket.params["severity"] == severity
            name = rec.check_name(check_id)
            assert check_id not in ticket.label and check_id not in ticket.request
            assert ticket.label.endswith(f"{name} ticket for {ticket.target_label}")
            assert ticket.request == (
                f"Open a remediation ticket for the {name} check on {ticket.target_label}"
            )


def test_a_device_with_nothing_eligible_yields_nothing():
    row = make_row(device="CALM", battery=None)
    repo = FleetRepository(make_conn([row]), TenantContext("acme-001", "recommend-test"))
    as_of = dataset_as_of(repo._conn)
    assert rec.recommend(repo, as_of, ["CALM"]) == []


def test_unknown_and_foreign_ids_yield_nothing(real_conn, as_of):
    globex = repo_for(real_conn, "globex-002")
    acme_only = [
        d for d in ACME["devices"] if d not in {x.device_id for x in globex.list_devices()}
    ]
    assert acme_only
    assert rec.recommend(globex, as_of, acme_only) == []
    assert rec.recommend(repo_for(real_conn), as_of, ["NOPE", "../x", ""]) == []
    assert rec.recommend(repo_for(real_conn), as_of, []) == []


def test_foreign_ids_do_not_leak_into_a_mixed_request(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, ["NOPE", "MT7PJB7N5LRE"])
    assert out and {s.device_id for s in out} == {"MT7PJB7N5LRE"}


def test_every_ref_resolves_in_the_tenant(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, ACME["devices"])
    assert out
    for s in out:
        assert 1 <= len(s.refs) <= policy.MAX_REFS_PER_FINDING
        for ref in s.refs:
            assert repo.resolve(EvidenceRef.parse(ref)).ref == ref
            assert EvidenceRef.parse(ref).device_id == s.device_id


def test_refs_and_params_come_from_eligibility(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    for s in rec.recommend(repo, as_of, LOW_DISK + MEMORY):
        kwargs = {}
        if s.action_type == "remediation_ticket":
            kwargs["check_id"] = s.params["check_id"]
        if s.action_type == "upgrade_order":
            kwargs["component"] = s.params["component"]
        if s.action_type == "employee_notification":
            kwargs["topic"] = s.params["topic"]
            kwargs["check_id"] = s.params.get("check_id")
        got = el.check(repo, as_of, s.action_type, s.device_id, **kwargs)
        assert isinstance(got, el.Eligible)
        assert s.refs == got.refs
        assert s.params == got.params
        assert s.target_label == got.target_label


def test_the_default_cap_is_respected(real_conn, as_of):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, ACME["devices"])
    assert len(out) == getattr(policy, "MAX_SUGGESTIONS", 6) == 6


def test_a_smaller_cap_keeps_the_highest_ranked(real_conn, as_of, monkeypatch):
    repo = repo_for(real_conn)
    monkeypatch.setattr(policy, "MAX_SUGGESTIONS", BIG, raising=False)
    full = rec.recommend(repo, as_of, ACME["devices"])
    monkeypatch.setattr(policy, "MAX_SUGGESTIONS", 3, raising=False)
    assert rec.recommend(repo, as_of, ACME["devices"]) == full[:3]


def test_order_is_battery_then_sustained_then_checks_then_notifications(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, ACME["devices"])
    tier = {
        "replacement_flag": 1,
        "upgrade_order": 2,
        "remediation_ticket": 3,
        "employee_notification": 4,
    }
    ranks = [tier[s.action_type] for s in out]
    assert ranks == sorted(ranks)
    assert {1, 2, 3, 4} <= set(ranks)


def test_the_order_is_deterministic_and_follows_the_given_device_order(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    ids = ["MT7PJB7N5LRE", "8NM23J95R5I6"]
    first = rec.recommend(repo, as_of, ids)
    assert first == rec.recommend(repo, as_of, ids)
    assert first == rec.recommend(repo, as_of, [*ids, *ids])  # duplicates ignored
    upgrades = [s.device_id for s in of_type(first, "upgrade_order")]
    assert upgrades == ids


def test_failing_high_severity_checks_come_first():
    rows = [
        make_row(
            device="D1",
            ts="2026-06-12T09:00:00Z",
            battery=None,
            compliance_results=[
                {"check_id": "screen_lock", "status": "fail", "severity": "medium"},
                {"check_id": "disk_encryption", "status": "fail", "severity": "high"},
            ],
        ),
    ]
    repo = FleetRepository(make_conn(rows), TenantContext("acme-001", "recommend-test"))
    out = rec.recommend(repo, dataset_as_of(repo._conn), ["D1"])
    assert [(s.action_type, s.params.get("check_id")) for s in out[:2]] == [
        ("remediation_ticket", "disk_encryption"),
        ("remediation_ticket", "screen_lock"),
    ]
    note = of_type(out, "employee_notification")[0]
    assert note.params["topic"] == "failing_check"
    assert note.params["message"].count("disk_encryption") == 1


def test_battery_service_ranks_below_failing_high_but_above_checks():
    rows = [
        make_row(
            device="D2",
            ts=f"2026-06-{day:02d}T09:00:00Z",
            battery={
                "battery_present": True,
                "condition": "Service Recommended",
                "cycle_count": 900,
                "full_charge_capacity": 4000,
                "percentage": 70,
            },
            compliance_results=[
                {"check_id": "disk_encryption", "status": "fail", "severity": "high"},
                {"check_id": "screen_lock", "status": "fail", "severity": "medium"},
            ],
        )
        for day in (8, 9, 10, 11, 12)
    ]
    repo = FleetRepository(make_conn(rows), TenantContext("acme-001", "recommend-test"))
    out = rec.recommend(repo, dataset_as_of(repo._conn), ["D2"])
    kinds = [(s.action_type, s.params.get("check_id")) for s in out]
    assert kinds[:3] == [
        ("remediation_ticket", "disk_encryption"),
        ("replacement_flag", None),
        ("remediation_ticket", "screen_lock"),
    ]


def test_chip_labels_are_human_and_distinct_for_each_check():
    assert rec.check_name("os_up_to_date") == "OS update"
    assert rec._a("OS update") == "an OS update"
    assert rec._a("screen lock") == "a screen lock"
    assert rec._a("disk encryption") == "a disk encryption"
    assert rec._a("failing OS update") == "a failing OS update"
    assert rec.check_name("new_thing") == "new thing"


def test_a_device_failing_two_checks_gets_two_different_ticket_labels(real_conn, as_of, uncapped):
    repo = repo_for(real_conn)
    out = rec.recommend(repo, as_of, sorted(ACME["devices"]))
    tickets = of_type(out, "remediation_ticket")
    by_device: dict[str, list[str]] = {}
    for t in tickets:
        by_device.setdefault(t.device_id, []).append(t.label)
    for labels in by_device.values():
        assert len(labels) == len(set(labels))
    assert all("(" not in t.label and "_" not in t.label for t in tickets)
