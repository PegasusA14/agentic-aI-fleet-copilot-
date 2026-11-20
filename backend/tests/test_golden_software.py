"""The oracle's software facts: the six new installs the design names, and the rule on tiny inputs.

The rule tests feed the oracle hand-made rows whose answers are worked out here, not by the product.
"""

import importlib.util
import json

import pytest
from factories import make_row

from fleet_copilot.config import REPO_ROOT

ORACLE = REPO_ROOT / "evals" / "golden" / "oracle.py"
G = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
T = G["tenants"]

CHROME = {"name": "Google Chrome", "version": "126.0", "publisher": "Google LLC"}
SLACK = {"name": "Slack", "version": "4.39", "publisher": "Slack Technologies"}
TORRENT = {"name": "uTorrent", "version": "3.6", "publisher": "Rainberry Inc"}
AS_OF = "2026-06-12T11:00:00Z"


def oracle():
    spec = importlib.util.spec_from_file_location("fleet_oracle_software", ORACLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def facts_for(*rows, tenant="acme-001"):
    raw = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    return oracle().generate(raw)["tenants"][tenant]["software"]


def row(device, ts, *apps):
    return make_row(device, "acme-001", ts, installed_software=list(apps))


# ---- the committed golden file ---------------------------------------------------------------------


def pairs(tenant, days="30"):
    return [
        (i["device_id"], i["app"], i["version"], i["first_seen"][:10])
        for i in T[tenant]["software"]["new_installs"][days]
    ]


def test_the_six_new_installs_the_design_names_are_in_the_golden_file():
    assert pairs("acme-001") == [
        ("1LYSSFD074BB", "uTorrent", "3.6", "2026-05-31"),
        ("7VP16KHV88LM", "CleanMyMac X", "4.15", "2026-05-25"),
    ]
    assert pairs("globex-002") == [("Q1AFVLH3YV33", "uTorrent", "3.6", "2026-05-20")]
    assert pairs("initech-003") == [
        ("DARFCP0BQM2G", "TeamViewer", "15.5", "2026-05-21"),
        ("I3JLKILKQTAC", "uTorrent", "3.6", "2026-05-29"),
        ("W2656QDAE2ZJ", "uTorrent", "3.6", "2026-06-02"),
    ]
    assert all(i["still_present"] for t in T.values() for i in t["software"]["new_installs"]["30"])


def test_the_common_set_is_the_five_apps_on_every_device_for_every_tenant_and_window():
    five = ["1Password", "Google Chrome", "Slack", "Visual Studio Code", "Zoom"]
    for tenant in T.values():
        assert tenant["software"]["common_set"] == {"7": five, "14": five, "30": five}


def test_holders_of_each_app_outside_the_common_set_at_the_as_of_time():
    assert T["acme-001"]["software"]["holders"] == {
        "CleanMyMac X": ["7VP16KHV88LM"],
        "uTorrent": ["1LYSSFD074BB"],
    }
    assert T["globex-002"]["software"]["holders"] == {"uTorrent": ["Q1AFVLH3YV33"]}
    assert T["initech-003"]["software"]["holders"] == {
        "TeamViewer": ["DARFCP0BQM2G"],
        "uTorrent": ["I3JLKILKQTAC", "W2656QDAE2ZJ"],
    }


def test_a_shorter_window_only_sees_installs_after_its_own_first_snapshot():
    assert all(not t["software"]["new_installs"]["7"] for t in T.values())
    assert [i[1] for i in pairs("acme-001", "14")] == [
        "uTorrent"
    ]  # CleanMyMac X came before day 14
    assert pairs("initech-003", "14") == [("W2656QDAE2ZJ", "uTorrent", "3.6", "2026-06-02")]


# ---- the rule, on tiny hand-made inputs ----------------------------------------------------------------


def test_the_common_set_is_what_every_device_had_at_its_first_snapshot():
    facts = facts_for(
        row("A", "2026-06-01T09:00:00Z", CHROME, SLACK),
        row("B", "2026-06-01T09:00:00Z", CHROME),
        row("C", "2026-06-01T09:00:00Z", CHROME, TORRENT),
    )
    assert facts["common_set"]["30"] == ["Google Chrome"]
    assert facts["new_installs"]["30"] == []  # everything was there from the start
    assert facts["holders"] == {"Slack": ["A"], "uTorrent": ["C"]}


def test_an_app_that_arrives_later_is_new_and_its_first_sighting_is_kept():
    facts = facts_for(
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-05T09:00:00Z", CHROME, TORRENT),
        row("A", "2026-06-09T09:00:00Z", CHROME, {**TORRENT, "version": "3.7"}),
        row("B", "2026-06-01T09:00:00Z", CHROME),
        row("B", "2026-06-09T09:00:00Z", CHROME),
    )
    assert facts["new_installs"]["30"] == [
        {
            "device_id": "A",
            "app": "uTorrent",
            "version": "3.6",
            "first_seen": "2026-06-05T09:00:00Z",
            "still_present": True,
        }
    ]


def test_an_app_that_was_removed_again_is_still_reported_but_not_present():
    facts = facts_for(
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-05T09:00:00Z", CHROME, TORRENT),
        row("A", "2026-06-09T09:00:00Z", CHROME),
    )
    (found,) = facts["new_installs"]["30"]
    assert (found["app"], found["still_present"]) == ("uTorrent", False)
    assert facts["holders"] == {}


def test_an_app_missing_from_one_devices_first_snapshot_leaves_the_common_set_and_can_be_new():
    facts = facts_for(
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-05T09:00:00Z", CHROME, SLACK),
        row("B", "2026-06-01T09:00:00Z", CHROME, SLACK),
    )
    assert facts["common_set"]["30"] == ["Google Chrome"]
    assert [(i["device_id"], i["app"]) for i in facts["new_installs"]["30"]] == [("A", "Slack")]


def test_the_window_start_is_inclusive_and_a_device_with_no_snapshot_in_it_is_ignored():
    # as-of is the latest collected_at of all rows, 2026-06-12T11:00:00Z; 7 days back is 2026-06-05T11:00:00Z
    facts = facts_for(
        row("A", "2026-06-05T11:00:00Z", CHROME),  # exactly on the start: inside
        row("A", "2026-06-12T11:00:00Z", CHROME, TORRENT),
        row(
            "B", "2026-06-05T10:59:00Z", CHROME, SLACK
        ),  # one minute before: outside, B has nothing inside
    )
    assert facts["common_set"]["7"] == ["Google Chrome"]
    assert [(i["device_id"], i["app"]) for i in facts["new_installs"]["7"]] == [("A", "uTorrent")]
    assert facts["common_set"]["30"] == ["Google Chrome"]  # both devices count in the longer window


@pytest.mark.parametrize("days", ["7", "14", "30"])
def test_every_window_is_present_for_every_tenant(days):
    for tenant in T.values():
        assert (
            days in tenant["software"]["common_set"] and days in tenant["software"]["new_installs"]
        )
