"""List answers carry a typed table, one supporting sentence, scope counts, excluded devices and next questions."""

import re

import pytest
from agent_support import ACME, INITECH, repo_for, run

from fleet_copilot import policy
from fleet_copilot.agent.compose import Composition, compose
from fleet_copilot.agent.records import FindingRecord, ToolRecord
from fleet_copilot.repository import dataset_as_of

SERIAL = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,16}\b")


def composed(real_db_path, real_conn, calls, tenant=ACME) -> Composition:
    records = [run(real_db_path, real_conn, name, args, tenant) for name, args in calls]
    return compose(records, resolve=repo_for(real_conn, tenant).resolve)


def envelope(comp: Composition, real_conn, tenant=ACME, **kw):
    return comp.to_envelope(company_id=tenant, as_of=dataset_as_of(real_conn), trace_id="t-1", **kw)


def column_keys(table):
    return [c.key for c in table.columns]


def display(row, key):
    return row.cells[key].display


# ---- macOS: the Initech mockup ----------------------------------------------------------------------


@pytest.fixture
def initech_macos(real_db_path, real_conn):
    comp = composed(
        real_db_path, real_conn, [("macos_version_check", {"older_than": "15"})], INITECH
    )
    return envelope(comp, real_conn, INITECH, company_name="Initech")


def test_the_macos_table_lists_the_four_older_macs_oldest_first(initech_macos):
    table = initech_macos.table
    assert table.title == "macOS older than 15"
    assert column_keys(table) == ["device", "macos", "behind"]
    assert [r.device_id for r in table.rows] == [
        "SPKRRDF0L786",
        "XZYK11QTPWEC",
        "DARFCP0BQM2G",
        "RLD7WV7J82SH",
    ]
    assert [r.label for r in table.rows] == [
        "initech-macbook-2",
        "initech-macbook-7",
        "initech-macbook-6",
        "initech-mac-4",
    ]
    assert [display(r, "macos") for r in table.rows] == ["13.6", "13.6", "14.5", "14.5"]
    assert [r.cells["behind"].value for r in table.rows] == [2, 2, 1, 1]
    assert [display(r, "behind") for r in table.rows] == [
        "2 major behind",
        "2 major behind",
        "1 major behind",
        "1 major behind",
    ]
    assert [r.cells["behind"].tone for r in table.rows] == [
        "attention",
        "attention",
        "neutral",
        "neutral",
    ]
    assert [c.label for c in table.columns][-1] == "Behind 15"
    assert table.default_sort.key == "macos" and table.default_sort.direction == "asc"
    assert table.total_rows == 4
    # the device cell is the hostname, and every row cites readings of its own device
    assert all(r.cells["device"].display == r.label for r in table.rows)
    assert all(ref.split("/")[1].startswith(r.device_id) for r in table.rows for ref in r.refs)


def test_the_macos_scope_line_counts_checked_matched_ok_and_not_checked(initech_macos):
    line = initech_macos.scope_line
    assert (line.checked, line.matched, line.ok, line.ok_label, line.not_checked) == (
        5,
        4,
        1,
        "up to date",
        2,
    )
    assert line.company_name == "Initech"
    assert line.basis == "latest snapshot per device"
    assert line.as_of == initech_macos.scope.as_of


def test_the_macos_excluded_devices_say_why_in_the_devices_own_words(initech_macos):
    excluded = initech_macos.coverage.excluded_devices
    assert {e.label for e in excluded} == {"initech-dell-5", "initech-dell-1"}
    assert {e.reason for e in excluded} == {"out_of_scope"}
    assert all(e.reason_text.startswith("Windows 1") for e in excluded)
    assert all(e.reason_text.endswith(", not a Mac") for e in excluded)
    assert "Windows 11, not a Mac" in {e.reason_text for e in excluded}
    # the existing map is untouched
    assert sorted(initech_macos.coverage.excluded["out_of_scope"]) == sorted(
        e.device_id for e in excluded
    )


def test_the_macos_summary_groups_by_version_and_never_lists_serials(initech_macos):
    assert initech_macos.summary == "Two are on 13.6 and two on 14.5."
    assert not SERIAL.search(initech_macos.summary)
    assert len(initech_macos.summary) <= 200


def test_the_macos_follow_ups_offer_the_unchecked_devices_and_advice(initech_macos):
    ups = initech_macos.follow_ups
    assert [u.label for u in ups] == [
        "Look at the 2 devices not checked",
        "What should we do about these?",
    ]
    assert ups[0].request == "Which devices were not checked, and why?"
    assert len(ups) <= 4


def test_the_old_claim_entity_list_and_evidence_are_unchanged(initech_macos):
    (claim,) = initech_macos.claims
    assert claim.device_ids == ["DARFCP0BQM2G", "RLD7WV7J82SH", "SPKRRDF0L786", "XZYK11QTPWEC"]
    assert "SPKRRDF0L786 (initech-macbook-2; macOS 13.6)" in claim.text
    (entity_list,) = initech_macos.entity_lists
    assert [i.device_id for i in entity_list.items] == claim.device_ids
    assert {e.ref for e in initech_macos.evidence} == set(claim.refs)


def test_every_row_ref_is_in_the_evidence_of_a_valid_envelope(initech_macos):
    known = {e.ref for e in initech_macos.evidence}
    assert all(set(r.refs) <= known for r in initech_macos.table.rows)


def test_unknown_versions_leave_the_up_to_date_count_unknown():
    rec = ToolRecord(
        tool="macos_version_check",
        args={"older_than": "15"},
        findings=[
            FindingRecord(
                device_id="D1",
                hostname="mac-1",
                values={"os_version": "14.5", "collected_at": "2026-06-12T10:00:00Z"},
                refs=["initech-003/D1@2026-06-12T10:00:00Z#os"],
            )
        ],
        population=3,
        excluded={"unknown_version": ["D2"], "out_of_scope": ["D3"]},
    )
    from fleet_copilot.agent.present import present

    line = present(rec).scope
    assert (line.checked, line.matched, line.ok, line.ok_label, line.not_checked) == (
        3,
        1,
        None,
        "",
        1,
    )


def test_an_older_record_without_per_device_text_still_names_a_reason(real_db_path, real_conn):
    rec = run(real_db_path, real_conn, "macos_version_check", {"older_than": "15"}, INITECH)
    old = rec.model_copy(update={"excluded_detail": {}})
    comp = compose([old], resolve=repo_for(real_conn, INITECH).resolve)
    excluded = envelope(comp, real_conn, INITECH).coverage.excluded_devices
    assert len(excluded) == 2
    assert {e.reason_text for e in excluded} == {"Out of scope for this check"}


# ---- other tools ------------------------------------------------------------------------------------


def test_the_low_disk_table_puts_the_least_free_device_first(real_db_path, real_conn):
    env = envelope(composed(real_db_path, real_conn, [("low_disk_devices", {})]), real_conn)
    table = env.table
    assert [r.device_id for r in table.rows] == ["MT7PJB7N5LRE", "8NM23J95R5I6"]
    assert [r.label for r in table.rows] == ["acme-macbook-4", "acme-thinkpad-8"]
    assert column_keys(table) == ["device", "free", "space"]
    assert display(table.rows[0], "free") == "2.0%"
    assert display(table.rows[0], "space") == "10.2 GiB of 512.0 GiB"
    assert table.default_sort.key == "free" and table.default_sort.direction == "asc"
    assert env.summary == "Both are under 10% free, the lowest at 2.0%."
    line = env.scope_line
    assert (line.checked, line.matched, line.ok, line.ok_label, line.not_checked) == (
        10,
        2,
        8,
        "healthy",
        0,
    )
    assert env.coverage.excluded_devices == []
    assert [u.label for u in env.follow_ups] == [
        f"Show their {policy.TREND_WINDOW_DAYS}-day trend",
        "What should we do about these?",
    ]
    assert "free disk space" in env.follow_ups[0].request
    assert not any(
        SERIAL.search(text) for text in [env.summary, *(u.request for u in env.follow_ups)]
    )


def test_the_memory_table_puts_the_highest_use_first(real_db_path, real_conn):
    env = envelope(composed(real_db_path, real_conn, [("memory_pressure_now", {})]), real_conn)
    table = env.table
    used = [r.cells["used"].value for r in table.rows]
    assert used == sorted(used, reverse=True) and len(used) >= 1
    assert column_keys(table) == ["device", "used", "memory"]
    assert table.default_sort.direction == "desc"
    assert env.scope_line.ok_label == "healthy"


def test_the_compliance_table_has_one_row_per_device_and_failing_check(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("compliance_failures_now", {})])
    env = envelope(comp, real_conn)
    table = env.table
    assert column_keys(table) == ["device", "check", "severity", "seen"]
    pairs = [(r.device_id, display(r, "check")) for r in table.rows]
    assert len(pairs) == len(set(pairs)) >= 1
    severities = [r.cells["severity"].value for r in table.rows]
    assert severities == sorted(severities, reverse=True)  # most severe first
    assert all(len(r.refs) == 1 for r in table.rows)
    assert table.default_sort.key == "severity"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", display(table.rows[0], "seen"))
    assert env.scope_line.ok_label == "passing"
    assert {c.kind for c in table.columns} == {"device", "text", "pill", "date"}


def test_a_trend_table_shows_pattern_breaches_and_the_latest_value(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("trend", {"metric": "memory"})])
    env = envelope(comp, real_conn)
    table = env.table
    assert column_keys(table) == ["device", "pattern", "breached", "latest"]
    assert env.scope_line.basis == f"last {policy.TREND_WINDOW_DAYS} days of snapshots"
    for row in table.rows:
        assert re.fullmatch(r"\d+ of \d+ snapshots", display(row, "breached"))
        assert display(row, "pattern") in {
            "Sustained",
            "Onset",
            "Intermittent",
            "Recovering",
            "Isolated",
        }
    ranks = [r.cells["pattern"].value for r in table.rows]
    assert ranks == sorted(ranks, reverse=True)
    assert all(len(r.refs) >= 1 for r in table.rows)
    assert env.coverage.excluded_devices == [] or all(
        e.reason == "insufficient_history" and e.reason_text.startswith(("Only ", "No snapshots"))
        for e in env.coverage.excluded_devices
    )


def test_a_disk_trend_follow_up_is_not_another_trend_request(real_db_path, real_conn):
    env = envelope(composed(real_db_path, real_conn, [("trend", {"metric": "disk"})]), real_conn)
    assert "What should we do about these?" in [u.label for u in env.follow_ups]
    assert not any("trend" in u.label for u in env.follow_ups)


def test_the_battery_trend_and_its_excluded_devices(real_db_path, real_conn):
    env = envelope(composed(real_db_path, real_conn, [("trend", {"metric": "battery"})]), real_conn)
    assert env.table is not None
    assert column_keys(env.table) == ["device", "capacity", "cycles", "condition"]
    for e in env.coverage.excluded_devices:
        assert (e.reason, e.reason_text) == ("no_reading", "No battery reading")


def test_the_software_inventory_table_lists_each_devices_apps(real_db_path, real_conn):
    env = envelope(
        composed(
            real_db_path,
            real_conn,
            [("software_inventory", {"view": "current", "device_ids": ["MT7PJB7N5LRE"]})],
        ),
        real_conn,
    )
    table = env.table
    assert column_keys(table) == ["device", "apps", "extra"]
    (row,) = table.rows
    assert row.device_id == "MT7PJB7N5LRE" and row.label == "acme-macbook-4"
    assert re.fullmatch(r"\d+ apps?", display(row, "apps"))
    assert env.scope_line.ok is None
    assert [u.label for u in env.follow_ups] == []


def test_new_installs_table_has_one_row_per_device_and_app(real_db_path, real_conn):
    env = envelope(
        composed(real_db_path, real_conn, [("software_inventory", {"view": "new_installs"})]),
        real_conn,
    )
    table = env.table
    assert column_keys(table) == ["device", "app", "version", "first_seen", "status"]
    firsts = [r.cells["first_seen"].value for r in table.rows]
    assert firsts == sorted(firsts, reverse=True)
    assert env.scope_line.basis.startswith("last ")


def test_a_find_devices_inventory_has_a_table_but_no_advice(real_db_path, real_conn):
    env = envelope(
        composed(real_db_path, real_conn, [("find_devices", {"os_name": "macOS"})]), real_conn
    )
    assert column_keys(env.table) == ["device", "os", "model"]
    labels = [r.label for r in env.table.rows]
    assert labels == sorted(labels)
    assert all(not u.label.startswith("What should") for u in env.follow_ups)


def test_a_single_device_history_has_no_table(real_db_path, real_conn):
    env = envelope(
        composed(
            real_db_path,
            real_conn,
            [("device_history", {"device_id": "MT7PJB7N5LRE", "metric": "disk_free_pct"})],
        ),
        real_conn,
    )
    assert env.table is None and env.summary == "" and env.scope_line is None


def test_two_list_tools_in_one_turn_get_no_table(real_db_path, real_conn):
    comp = composed(
        real_db_path, real_conn, [("low_disk_devices", {}), ("memory_pressure_now", {})]
    )
    env = envelope(comp, real_conn)
    assert env.table is None and env.scope_line is None and env.summary == ""
    assert len(env.claims) == 2


def test_a_check_that_finds_nothing_still_reports_what_was_checked(real_db_path, real_conn):
    comp = composed(
        real_db_path, real_conn, [("macos_version_check", {"older_than": "10"})], INITECH
    )
    env = envelope(comp, real_conn, INITECH, company_name="Initech")
    assert env.outcome == "no_match" and env.table is None
    assert (env.scope_line.checked, env.scope_line.matched, env.scope_line.ok) == (5, 0, 5)
    assert env.scope_line.not_checked == 2
    assert [u.label for u in env.follow_ups] == ["Look at the 2 devices not checked"]


def test_follow_ups_are_capped_at_four_and_fit_the_alternative_limits(real_db_path, real_conn):
    for tool, args in [
        ("low_disk_devices", {}),
        ("memory_pressure_now", {}),
        ("compliance_failures_now", {}),
        ("trend", {"metric": "disk"}),
    ]:
        env = envelope(composed(real_db_path, real_conn, [(tool, args)]), real_conn)
        assert len(env.follow_ups) <= 4
        assert all(len(u.label) <= 60 and len(u.request) <= 160 for u in env.follow_ups)


def test_summaries_stay_short_and_serial_free_for_every_list_tool(real_db_path, real_conn):
    calls = [
        ("low_disk_devices", {}),
        ("memory_pressure_now", {}),
        ("compliance_failures_now", {}),
        ("trend", {"metric": "memory"}),
        ("trend", {"metric": "disk"}),
        ("trend", {"metric": "battery"}),
        ("find_devices", {}),
        ("software_inventory", {"view": "current"}),
        ("software_inventory", {"view": "new_installs"}),
    ]
    for tool, args in calls:
        env = envelope(composed(real_db_path, real_conn, [(tool, args)]), real_conn)
        assert 0 < len(env.summary) <= 200, (tool, args, env.summary)
        assert not SERIAL.search(env.summary), (tool, env.summary)
