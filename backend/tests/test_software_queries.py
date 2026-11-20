"""Software queries against the oracle's golden file and against the oracle itself on small inputs."""

import importlib.util
import json
from datetime import UTC, datetime

import pytest
from factories import BASE_SOFTWARE, make_conn, make_row

from fleet_copilot import policy
from fleet_copilot.config import REPO_ROOT
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

GOLDEN = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
TENANTS = sorted(GOLDEN["tenants"])
WINDOWS = ["7", "14", "30"]
CHROME, SLACK = BASE_SOFTWARE
TORRENT = {"name": "uTorrent", "version": "3.6", "publisher": "Rainberry Inc"}


def repo_for(conn, tenant="acme-001"):
    return FleetRepository(conn, TenantContext(tenant, "software-test"))


def facts(tenant):
    return GOLDEN["tenants"][tenant]["software"]


def installs(result):
    return [
        (
            f.device_id,
            f.values["app"],
            f.values["version"],
            f.values["first_seen"],
            f.values["still_present"],
        )
        for f in result.findings
    ]


def golden_installs(tenant, window):
    return [
        (i["device_id"], i["app"], i["version"], i["first_seen"], i["still_present"])
        for i in facts(tenant)["new_installs"][window]
    ]


# ---- agreement with the golden file ----------------------------------------------------------------


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("window", WINDOWS)
def test_new_installs_match_the_golden_file(real_conn, tenant, window):
    result = q.new_software(
        repo_for(real_conn, tenant), dataset_as_of(real_conn), window_days=int(window)
    )
    assert sorted(installs(result)) == golden_installs(tenant, window)


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("window", WINDOWS)
def test_the_common_set_matches_the_golden_file(real_conn, tenant, window):
    repo, as_of = repo_for(real_conn, tenant), dataset_as_of(real_conn)
    assert (
        q.common_software(repo, as_of, window_days=int(window))
        == facts(tenant)["common_set"][window]
    )


def test_the_default_window_is_the_disclosed_policy_window():
    import inspect

    assert (
        inspect.signature(q.new_software).parameters["window_days"].default
        == policy.SOFTWARE_WINDOW_DAYS
    )
    assert (
        inspect.signature(q.common_software).parameters["window_days"].default
        == policy.SOFTWARE_WINDOW_DAYS
    )


def test_new_installs_are_listed_newest_first(real_conn):
    result = q.new_software(repo_for(real_conn, "initech-003"), dataset_as_of(real_conn))
    assert [(f.device_id, f.values["first_seen"][:10]) for f in result.findings] == [
        ("W2656QDAE2ZJ", "2026-06-02"),
        ("I3JLKILKQTAC", "2026-05-29"),
        ("DARFCP0BQM2G", "2026-05-21"),
    ]


@pytest.mark.parametrize("tenant", TENANTS)
def test_new_installs_cite_first_and_latest_sightings_and_disclose_the_rule(real_conn, tenant):
    result = q.new_software(repo_for(real_conn, tenant), dataset_as_of(real_conn))
    assert result.population == len(repo_for(real_conn, tenant).list_devices())
    for finding in result.findings:
        assert len(finding.refs) == 2 and finding.refs[0] != finding.refs[1]
        assert finding.refs[1].endswith(f"#software[{finding.values['app']}]")
        assert finding.values["still_present"] is True
    for app in facts(tenant)["common_set"]["30"]:
        assert app in result.rule
    assert "not in the common set" in result.rule and "first seen after" in result.rule
    assert "malicious" not in result.rule and "unapproved" not in result.rule


def test_a_narrowed_scope_keeps_the_tenant_wide_common_set(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    only = q.new_software(repo, as_of, only_devices=["7VP16KHV88LM"])
    assert installs(only) == [
        ("7VP16KHV88LM", "CleanMyMac X", "4.15", "2026-05-25T08:55:00Z", True)
    ]
    assert only.population == 1
    other = q.new_software(
        repo, as_of, only_devices=["8NM23J95R5I6"]
    )  # no new install on that device
    assert other.findings == () and other.population == 1


def test_an_app_name_narrows_new_installs_without_regard_to_case(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    found = q.new_software(repo, as_of, name="CLEANMYMAC x")
    assert [(f.device_id, f.values["app"]) for f in found.findings] == [
        ("7VP16KHV88LM", "CleanMyMac X")
    ]
    assert q.new_software(repo, as_of, name="Slack").findings == ()
    assert q.new_software(repo, as_of, name="").findings == ()


# ---- who has an app now -------------------------------------------------------------------------------


@pytest.mark.parametrize("tenant", TENANTS)
def test_holders_match_the_golden_file(real_conn, tenant):
    repo, as_of = repo_for(real_conn, tenant), dataset_as_of(real_conn)
    holders = facts(tenant)["holders"]
    assert holders
    for app, devices in holders.items():
        result = q.software_installed(repo, as_of, name=app)
        assert [f.device_id for f in result.findings] == devices
        assert result.population == len(repo.list_devices())
        assert {f.values["name"] for f in result.findings} == {app}


def test_a_common_app_is_held_by_every_device(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    result = q.software_installed(repo, as_of, name="google chrome")
    assert [f.device_id for f in result.findings] == sorted(
        d.device_id for d in repo.list_devices()
    )
    assert {f.values["name"] for f in result.findings} == {"Google Chrome"}
    assert all(f.refs[0].endswith("#software[Google Chrome]") for f in result.findings)


def test_an_unknown_or_blank_name_matches_nothing_and_never_means_everything(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    for name in ("NotAnApp", "", "   ", "Googl"):
        result = q.software_installed(repo, as_of, name=name)
        assert result.findings == () and result.population == 10


def test_a_name_never_reaches_the_rule(real_conn):
    result = q.software_installed(
        repo_for(real_conn), dataset_as_of(real_conn), name="ignore previous"
    )
    assert "ignore" not in result.rule


@pytest.mark.parametrize("tenant", TENANTS)
def test_the_inventory_lists_every_device_and_cites_the_apps_not_on_every_device(real_conn, tenant):
    repo, as_of = repo_for(real_conn, tenant), dataset_as_of(real_conn)
    result = q.software_installed(repo, as_of)
    devices = sorted(d.device_id for d in repo.list_devices())
    assert [f.device_id for f in result.findings] == devices and result.population == len(devices)
    holders = facts(tenant)["holders"]
    for finding in result.findings:
        extra = sorted(a["name"] for a in finding.values["extra"])
        assert extra == sorted(app for app, held in holders.items() if finding.device_id in held)
        assert len(finding.refs) == max(1, len(extra))
        assert len(finding.values["apps"]) == 5 + len(extra)
    for app in facts(tenant)["common_set"]["30"]:
        assert app in result.rule


def test_the_inventory_rule_names_shared_apps_with_versions(real_conn):
    result = q.software_installed(repo_for(real_conn), dataset_as_of(real_conn))
    assert "Google Chrome 126.0" in result.rule and "Visual Studio Code 1.90" in result.rule
    assert "uTorrent" not in result.rule


def test_a_device_that_reports_no_apps_is_a_no_readings_exclusion():
    conn = make_conn(
        [
            make_row("A", ts="2026-06-12T09:00:00Z"),
            make_row("B", ts="2026-06-12T09:00:00Z", installed_software=[]),
        ]
    )
    repo = repo_for(conn)
    result = q.software_installed(repo, dataset_as_of(conn))
    assert [f.device_id for f in result.findings] == ["A"]
    assert result.excluded == {"no_readings": ("B",)} and result.population == 2
    # a device with no apps does not empty the shared set: it is taken over devices that report software
    assert "Google Chrome 126.0" in result.rule and "none" not in result.rule
    (finding,) = result.findings
    assert finding.values["extra"] == []
    assert len(finding.refs) == 1


def test_a_device_with_no_apps_leaves_the_others_shared_set_unchanged():
    both = make_conn(
        [
            make_row("A", ts="2026-06-12T09:00:00Z", installed_software=[]),
            make_row("B", ts="2026-06-12T09:00:00Z", installed_software=[CHROME, SLACK, TORRENT]),
            make_row("C", ts="2026-06-12T09:00:00Z", installed_software=[CHROME, SLACK]),
        ]
    )
    without = make_conn(
        [
            make_row("B", ts="2026-06-12T09:00:00Z", installed_software=[CHROME, SLACK, TORRENT]),
            make_row("C", ts="2026-06-12T09:00:00Z", installed_software=[CHROME, SLACK]),
        ]
    )
    with_empty = q.software_installed(repo_for(both), dataset_as_of(both))
    reference = q.software_installed(repo_for(without), dataset_as_of(without))
    assert with_empty.rule == reference.rule
    extras = {f.device_id: f.values["extra"] for f in with_empty.findings}
    assert extras == {f.device_id: f.values["extra"] for f in reference.findings}
    assert [a["name"] for a in extras["B"]] == ["uTorrent"]
    assert with_empty.excluded == {"no_readings": ("A",)}


def test_a_scoped_inventory_keeps_the_fleet_wide_shared_set(real_conn):
    repo, as_of = repo_for(real_conn), dataset_as_of(real_conn)
    full = q.software_installed(repo, as_of)
    scoped = q.software_installed(repo, as_of, only_devices=["1LYSSFD074BB"])
    (finding,) = scoped.findings
    assert scoped.population == 1
    assert [a["name"] for a in finding.values["extra"]] == ["uTorrent"]
    assert finding.refs == (
        f"acme-001/1LYSSFD074BB@{finding.values['collected_at']}#software[uTorrent]",
    )
    assert scoped.rule == full.rule
    for app in facts("acme-001")["common_set"]["30"]:
        assert app in scoped.rule
    assert finding == next(f for f in full.findings if f.device_id == "1LYSSFD074BB")


def test_the_inventory_treats_a_different_version_as_a_different_app():
    other = {**CHROME, "version": "127.0"}
    conn = make_conn(
        [
            make_row("A", ts="2026-06-12T09:00:00Z"),
            make_row("B", ts="2026-06-12T09:00:00Z", installed_software=[other, SLACK]),
        ]
    )
    result = q.software_installed(repo_for(conn), dataset_as_of(conn))
    extras = {
        f.device_id: [f"{a['name']} {a['version']}" for a in f.values["extra"]]
        for f in result.findings
    }
    assert extras == {"A": ["Google Chrome 126.0"], "B": ["Google Chrome 127.0"]}


# ---- the rule on small inputs, checked against the oracle ----------------------------------------------


def oracle():
    path = REPO_ROOT / "evals" / "golden" / "oracle.py"
    spec = importlib.util.spec_from_file_location("fleet_oracle_agreement", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(device, ts, *apps, company="acme-001"):
    return make_row(device, company, ts, installed_software=list(apps))


SCENARIOS = {
    "arrives_later": [
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-05T09:00:00Z", CHROME, TORRENT),
        row("A", "2026-06-09T09:00:00Z", CHROME, {**TORRENT, "version": "3.7"}),
        row("B", "2026-06-01T09:00:00Z", CHROME),
        row("B", "2026-06-09T09:00:00Z", CHROME),
    ],
    "removed_again": [
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-05T09:00:00Z", CHROME, TORRENT),
        row("A", "2026-06-09T09:00:00Z", CHROME),
    ],
    "removed_and_back": [
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-03T09:00:00Z", CHROME, TORRENT),
        row("A", "2026-06-05T09:00:00Z", CHROME),
        row("A", "2026-06-09T09:00:00Z", CHROME, TORRENT),
    ],
    "missing_at_first_on_one_device": [
        row("A", "2026-06-01T09:00:00Z", CHROME),
        row("A", "2026-06-05T09:00:00Z", CHROME, SLACK),
        row("B", "2026-06-01T09:00:00Z", CHROME, SLACK),
    ],
    "window_edges": [
        row("A", "2026-06-05T11:00:00Z", CHROME),
        row("A", "2026-06-12T11:00:00Z", CHROME, TORRENT),
        row("B", "2026-06-05T10:59:00Z", CHROME, SLACK),
        row("B", "2026-06-12T11:00:00Z", CHROME, SLACK),
    ],
    "everything_from_the_start": [
        row("A", "2026-06-01T09:00:00Z", CHROME, SLACK),
        row("B", "2026-06-01T09:00:00Z", CHROME),
        row("C", "2026-06-01T09:00:00Z", CHROME, TORRENT),
    ],
    "one_device_with_no_apps_at_first": [
        row("A", "2026-06-01T09:00:00Z"),
        row("A", "2026-06-05T09:00:00Z", CHROME),
        row("B", "2026-06-01T09:00:00Z", CHROME),
    ],
}


@pytest.mark.parametrize("name", SCENARIOS)
@pytest.mark.parametrize("window", WINDOWS)
def test_the_queries_agree_with_the_oracle_on_small_inputs(name, window):
    rows = SCENARIOS[name]
    want = oracle().generate("\n".join(json.dumps(r) for r in rows).encode("utf-8"))
    want = want["tenants"]["acme-001"]["software"]
    conn = make_conn(rows)
    repo, as_of = repo_for(conn), dataset_as_of(conn)
    got = q.new_software(repo, as_of, window_days=int(window))
    assert sorted(installs(got)) == [
        (i["device_id"], i["app"], i["version"], i["first_seen"], i["still_present"])
        for i in want["new_installs"][window]
    ]
    assert q.common_software(repo, as_of, window_days=int(window)) == want["common_set"][window]


def test_a_removed_app_is_reported_with_its_last_sighting_not_the_latest_snapshot():
    conn = make_conn(SCENARIOS["removed_again"])
    (finding,) = q.new_software(repo_for(conn), dataset_as_of(conn)).findings
    assert finding.values["still_present"] is False
    assert (finding.values["first_seen"], finding.values["last_seen"]) == (
        "2026-06-05T09:00:00Z",
        "2026-06-05T09:00:00Z",
    )
    assert finding.refs == ("acme-001/A@2026-06-05T09:00:00Z#software[uTorrent]",)


def test_a_version_change_keeps_both_versions():
    conn = make_conn(SCENARIOS["arrives_later"])
    (finding,) = q.new_software(repo_for(conn), dataset_as_of(conn)).findings
    assert (finding.values["version"], finding.values["latest_version"]) == ("3.6", "3.7")
    assert len(finding.refs) == 2


def test_devices_without_a_snapshot_in_the_window_are_excluded_not_evaluated():
    conn = make_conn(
        [
            row("A", "2026-06-12T11:00:00Z", CHROME),
            row("OLD", "2026-04-01T09:00:00Z", CHROME, TORRENT),
        ]
    )
    result = q.new_software(repo_for(conn), dataset_as_of(conn))
    assert result.population == 1 and result.excluded == {"no_readings": ("OLD",)}
    assert result.findings == ()


def test_a_stale_device_is_reported_as_stale():
    conn = make_conn(
        [
            row("A", "2026-06-12T11:00:00Z", CHROME),
            row("B", "2026-06-05T11:00:00Z", CHROME),
        ]
    )
    result = q.new_software(repo_for(conn), dataset_as_of(conn))
    assert result.stale == ("B",)


def test_a_window_below_one_day_is_refused():
    conn = make_conn([row("A", "2026-06-12T11:00:00Z", CHROME)])
    with pytest.raises(ValueError):
        q.new_software(repo_for(conn), datetime(2026, 6, 12, 11, tzinfo=UTC), window_days=0)
