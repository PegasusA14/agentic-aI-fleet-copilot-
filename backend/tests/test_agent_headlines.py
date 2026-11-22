"""The code-written headline of every answer: one short, plain sentence that ends with a full stop.

Each case fixes the exact words, the numbers the headline must keep (the verify gate asks a model's
headline to restate them), and checks the properties every headline shares.
"""

import re

import pytest
from agent_support import ACME, GLOBEX, INITECH, repo_for, run

from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.verify import number_tokens
from fleet_copilot.repository import dataset_as_of

NUMBER = re.compile(r"\d+(?:\.\d+)?")
LONG = 90  # a history line names the device and four readings

# (id, tenant, tool call, the headline, the numbers it keeps, the longest it may be)
CASES = [
    ("low_disk", ACME, ("low_disk_devices", {}), "2 of your 10 devices are under 10% free disk space.", ("2", "10", "10"), 70),
    ("low_disk_empty", ACME, ("low_disk_devices", {"threshold_pct": 1}), "None of your 10 devices are under 1% free disk space.", ("10", "1"), 70),
    ("macos", INITECH, ("macos_version_check", {"older_than": "15"}), "4 of your 5 Macs are behind macOS 15.", ("4", "5", "15"), 70),
    ("macos_empty", ACME, ("macos_version_check", {"older_than": "10"}), "None of your 7 Macs are behind macOS 10.", ("7", "10"), 70),
    ("compliance_one_check", ACME, ("compliance_failures_now", {"check_id": "screen_lock"}), "5 of your 10 devices fail the screen lock check.", ("5", "10"), 70),
    ("compliance_many_checks", ACME, ("compliance_failures_now", {}), "10 of your 10 devices fail at least one check.", ("10", "10"), 70),
    ("compliance_laptops", GLOBEX, ("compliance_failures_now", {"form_factor": "laptop"}), "5 of your 8 laptops fail at least one check.", ("5", "8"), 70),
    ("compliance_severity_empty", ACME, ("compliance_failures_now", {"severity": "high", "form_factor": "laptop"}), "None of your 9 laptops fail a high-severity check.", ("9",), 70),
    ("memory", ACME, ("memory_pressure_now", {}), "1 of your 10 devices is using 85% or more memory.", ("1", "10", "85"), 70),
    ("memory_empty", ACME, ("memory_pressure_now", {"threshold_pct": 99}), "None of your 10 devices are using 99% or more memory.", ("10", "99"), 70),
    ("trend_disk", ACME, ("trend", {"metric": "disk"}), "2 of your 10 devices dipped under 10% free disk in the last 7 days.", ("2", "10", "10", "7"), 70),
    ("trend_memory", ACME, ("trend", {"metric": "memory"}), "1 of your 10 devices hit 85% or more memory use in the last 7 days.", ("1", "10", "85", "7"), 70),
    ("trend_compliance", ACME, ("trend", {"metric": "compliance", "check_id": "screen_lock"}), "5 of your 10 devices had screen lock failures in the last 30 days.", ("5", "10", "30"), 70),
    ("trend_battery", ACME, ("trend", {"metric": "battery"}), "3 of your 9 laptops show signs of battery wear in the last 30 days.", ("3", "9", "30"), 70),
    ("trend_battery_empty", INITECH, ("trend", {"metric": "battery"}), "None of your 5 laptops show signs of battery wear in the last 30 days.", ("5", "30"), 70),
    ("trend_one_day", ACME, ("trend", {"metric": "battery", "window_days": 1}), "None of your 9 laptops show signs of battery wear in the last 1 day.", ("9", "1"), 70),
    ("not_enough_history", ACME, ("trend", {"metric": "disk", "window_days": 1}), "Not enough snapshots in that window to show a trend for your 10 devices.", ("10",), 75),
    ("find", ACME, ("find_devices", {"os_name": "macOS"}), "7 of your 10 devices match.", ("7", "10"), 70),
    ("find_grouped_all", ACME, ("find_devices", {"group_by": "os"}), "Your 10 devices, grouped by OS.", ("10",), 70),
    ("find_grouped_some", ACME, ("find_devices", {"form_factor": "laptop", "group_by": "model"}), "9 of your 10 devices, grouped by model.", ("9", "10"), 70),
    ("software_holders", ACME, ("software_inventory", {"view": "current", "app_name": "utorrent"}), "1 of your 10 devices has uTorrent installed.", ("1", "10"), 70),
    ("software_new_installs", ACME, ("software_inventory", {"view": "new_installs"}), "2 of your 10 devices got 2 new installs in the last 30 days.", ("2", "10", "2", "30"), 70),
    ("software_new_install_singular", GLOBEX, ("software_inventory", {"view": "new_installs"}), "1 of your 8 devices got 1 new install in the last 30 days.", ("1", "8", "1", "30"), 70),
    ("software_new_installs_empty", ACME, ("software_inventory", {"view": "new_installs", "window_days": 7}), "None of your 10 devices got new installs in the last 7 days.", ("10", "7"), 70),
    ("software_inventory", ACME, ("software_inventory", {"view": "current"}), "10 of your 10 devices reported their installed software.", ("10", "10"), 70),
    ("history_disk", ACME, ("device_history", {"device_id": "MT7PJB7N5LRE", "metric": "disk_free_pct"}), "Free disk space on acme-macbook-4: started at 2.3%, now 2.0% (low 2.0%, high 2.3%).", ("2.3", "2.0", "2.0", "2.3"), LONG),
    ("history_memory", ACME, ("device_history", {"device_id": "MT7PJB7N5LRE", "metric": "memory_used_pct"}), "Memory use on acme-macbook-4: started at 45.6%, now 47.6% (low 45.6%, high 54.4%).", ("45.6", "47.6", "45.6", "54.4"), LONG),
    ("history_battery", ACME, ("device_history", {"device_id": "MT7PJB7N5LRE", "metric": "battery_capacity"}), "Battery capacity on acme-macbook-4: started at 5679, now 5679 (low 5679, high 5679).", ("5679",), LONG),
    ("history_compliance", ACME, ("device_history", {"device_id": "MT7PJB7N5LRE", "metric": "compliance", "check_id": "screen_lock"}), "Screen lock on acme-macbook-4: passed in 0 and failed in 7 snapshots, now failing.", ("0", "7"), LONG),
]  # fmt: skip

IDS = [case[0] for case in CASES]


def headline_of(real_db_path, real_conn, tenant, call):
    name, args = call
    comp = compose(
        [run(real_db_path, real_conn, name, args, tenant)],
        resolve=repo_for(real_conn, tenant).resolve,
    )
    return comp, comp.fallback_headline


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_tool_writes_its_headline_in_plain_words(real_db_path, real_conn, case):
    _, tenant, call, expected, _, _ = case
    comp, headline = headline_of(real_db_path, real_conn, tenant, call)
    assert headline == expected
    assert comp.table is None or comp.table.title


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_headline_is_one_sentence_with_its_numbers_and_no_serial(
    real_db_path, real_conn, case
):
    _, tenant, call, expected, numbers, longest = case
    comp, headline = headline_of(real_db_path, real_conn, tenant, call)
    assert headline.endswith(".") and not headline.endswith("..")
    assert headline.count(". ") == 0, "one sentence"
    assert len(headline) <= longest
    # the numbers the old wording carried are all still there, so a model's headline is still held to them
    assert set(numbers) <= set(NUMBER.findall(headline))
    assert number_tokens(" ".join(numbers)) <= number_tokens(headline)
    serials = {d for claim in comp.claims for d in claim.device_ids}
    assert not any(serial in headline for serial in serials)
    assert "compliance" not in headline.lower() and "_" not in headline


def test_the_count_opens_the_headline_in_the_shape_the_screen_ticks(real_db_path, real_conn):
    for _, _, call, expected, _, _ in CASES:
        if call[0] != "device_history":
            assert re.match(
                r"(\d+ of your \d+ \w|None of your \d+ |Not enough snapshots|Your \d+ )", expected
            )
    _, headline = headline_of(real_db_path, real_conn, ACME, ("low_disk_devices", {}))
    assert re.match(r"\d+ of your \d+ ", headline)


def test_fleet_health_names_the_company_and_counts_each_device_once(real_db_path, real_conn):
    comp, headline = headline_of(real_db_path, real_conn, ACME, ("fleet_health", {}))
    assert headline == "7 devices need attention at {company}: 4 act soon, 3 to watch."
    envelope = comp.to_envelope(
        company_id=ACME,
        as_of=dataset_as_of(real_conn),
        trace_id="t-1",
        company_name="Acme",
    )
    assert envelope.headline == "7 devices need attention at Acme: 4 act soon, 3 to watch."
    nameless = comp.to_envelope(company_id=ACME, as_of=dataset_as_of(real_conn), trace_id="t-1")
    assert nameless.headline == "7 devices need attention: 4 act soon, 3 to watch."


def test_fleet_health_with_nothing_to_flag_says_so(real_db_path, real_conn):
    records = run(real_db_path, real_conn, "fleet_health", {}, ACME)
    quiet = records.model_copy(update={"findings": []})
    comp = compose([quiet], resolve=repo_for(real_conn, ACME).resolve)
    assert (comp.outcome, comp.fallback_headline) == (
        "no_match",
        "Nothing needs attention across your 10 devices.",
    )


def test_a_turn_with_two_checks_joins_two_sentences_with_a_space(real_db_path, real_conn):
    comp = compose(
        [
            run(real_db_path, real_conn, "low_disk_devices", {}, ACME),
            run(real_db_path, real_conn, "macos_version_check", {"older_than": "15"}, ACME),
        ],
        resolve=repo_for(real_conn, ACME).resolve,
    )
    assert comp.fallback_headline == (
        "2 of your 10 devices are under 10% free disk space. 4 of your 7 Macs are behind macOS 15."
    )


def test_a_device_whose_hostname_is_not_plain_is_called_that_device(real_db_path, real_conn):
    record = run(
        real_db_path,
        real_conn,
        "device_history",
        {"device_id": "MT7PJB7N5LRE", "metric": "disk_free_pct"},
        ACME,
    )
    odd = record.model_copy(
        update={
            "findings": [
                record.findings[0].model_copy(update={"hostname": "Bob's <MacBook> (work)"})
            ]
        }
    )
    comp = compose([odd], resolve=repo_for(real_conn, ACME).resolve)
    assert comp.fallback_headline.startswith("Free disk space on that device: started at")
    assert "MT7PJB7N5LRE" not in comp.fallback_headline


def test_no_devices_at_all_is_plain_too():
    comp = compose([], resolve=lambda ref: None)
    assert comp.fallback_headline == "There are no devices to check."


@pytest.mark.parametrize(
    ("call", "title"),
    [
        (("low_disk_devices", {}), "Low on disk (under 10% free)"),
        (("memory_pressure_now", {}), "High memory use (85% or more)"),
        (("macos_version_check", {"older_than": "15"}), "Macs behind macOS 15"),
        (("compliance_failures_now", {}), "Devices failing a check"),
        (("compliance_failures_now", {"check_id": "screen_lock"}), "Screen lock failures"),
        (("trend", {"metric": "disk"}), "Free disk, last 7 days"),
        (("trend", {"metric": "memory"}), "Memory use, last 7 days"),
        (
            ("trend", {"metric": "compliance", "check_id": "screen_lock"}),
            "Screen lock, last 30 days",
        ),
        (("trend", {"metric": "battery"}), "Battery wear, last 30 days"),
        (("software_inventory", {"view": "new_installs"}), "New software, last 30 days"),
    ],
    ids=lambda p: p if isinstance(p, str) else p[0],
)
def test_table_titles_are_plain_words(real_db_path, real_conn, call, title):
    comp, _ = headline_of(real_db_path, real_conn, ACME, call)
    assert comp.table.title == title


def test_the_check_column_names_the_check_in_words_and_keeps_its_id_as_the_value(
    real_db_path, real_conn
):
    comp, _ = headline_of(real_db_path, real_conn, ACME, ("compliance_failures_now", {}))
    cells = [row.cells["check"] for row in comp.table.rows]
    assert {c.display for c in cells} == {"OS update", "Screen lock"}
    assert {c.value for c in cells} == {"os_up_to_date", "screen_lock"}


def test_a_check_is_named_in_words_and_keeps_its_capitals():
    from fleet_copilot.agent.present import check_title, check_words

    assert check_words("disk_encryption") == "disk encryption"
    assert check_title("disk_encryption") == "Disk encryption"
    assert check_title("os_up_to_date") == "OS update"
    assert check_title("firewall_on") == "Firewall on"
