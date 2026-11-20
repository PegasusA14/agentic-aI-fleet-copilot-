"""Every evidence reference a query emits must resolve in its own tenant, and the value the
finding states must agree with the observation the reference points at.

The cross-checks recompute from the RESOLVED evidence, never from the finding, so a wrong
path, a wrong snapshot or a wrong number cannot pass by agreeing with itself.
"""

import re

import pytest
from test_isolation import TENANTS, every_query, repo_for

from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import dataset_as_of

QUERY_NAMES = [
    "low_disk",
    "macos_below",
    "compliance_failures",
    "compliance_failures_laptops",
    "memory_trend",
    "disk_trend",
    "screen_lock_trend",
    "battery",
    "os_up_to_date_trend",
    "software_installed",
    "software_holders",
    "new_software",
]
LOW_DISK_PCT = 10.0
MEMORY_PCT = 85.0
COMPLIANCE_PATH = re.compile(r"compliance\[([^\]]+)\]")


@pytest.fixture(scope="module")
def evaluated(real_conn):
    """{tenant: (repository, {query name: QueryResult})} over the real dataset."""
    as_of = dataset_as_of(real_conn)
    out = {}
    for tenant in TENANTS:
        repo = repo_for(real_conn, tenant)
        results = every_query(repo, as_of)
        results["os_up_to_date_trend"] = q.compliance_trend(repo, as_of, check_id="os_up_to_date")
        out[tenant] = (repo, results)
    return out


def resolve(repo, ref):
    return repo.resolve(EvidenceRef.parse(ref))


def findings_of(evaluated, *names):
    """Yield (tenant, repo, query name, finding) for the named queries across all tenants."""
    for tenant, (repo, results) in evaluated.items():
        for name in names:
            for finding in results[name].findings:
                yield tenant, repo, name, finding


def used_pct(value):
    return value["used_bytes"] * 100.0 / value["total_bytes"]


def free_pct(value):
    return value["available_bytes"] * 100.0 / value["size_bytes"]


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("name", QUERY_NAMES)
def test_every_emitted_ref_resolves_in_its_tenant(evaluated, tenant, name):
    repo, results = evaluated[tenant]
    for finding in results[name].findings:
        assert finding.refs, f"{name}: finding for {finding.device_id} cites nothing"
        assert len(set(finding.refs)) == len(finding.refs), f"{name}: duplicate refs"
        for ref in finding.refs:
            parsed = EvidenceRef.parse(ref)
            assert parsed.company_id == tenant
            assert parsed.device_id == finding.device_id
            evidence = repo.resolve(parsed)  # NotFound fails the test
            assert evidence.ref == ref
            assert evidence.value


@pytest.mark.parametrize("name", QUERY_NAMES)
def test_each_query_cites_something_somewhere(evaluated, name):
    cited = sum(len(f.refs) for _, results in evaluated.values() for f in results[name].findings)
    assert cited > 0, f"{name} produced no findings in any tenant, so the ref check is vacuous"


def test_every_query_name_is_covered(evaluated):
    for _, results in evaluated.values():
        assert set(results) == set(QUERY_NAMES)


def test_low_disk_values_agree_with_resolved_evidence(evaluated):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, "low_disk"):
        (ref,) = finding.refs
        assert EvidenceRef.parse(ref).path == "disk"
        recomputed = free_pct(resolve(repo, ref).value)
        assert recomputed == pytest.approx(finding.values["free_pct"], rel=1e-9), (
            tenant,
            finding.device_id,
        )
        assert recomputed < LOW_DISK_PCT
        checked += 1
    assert checked > 0


def test_macos_below_values_agree_with_resolved_evidence(evaluated):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, "macos_below"):
        (ref,) = finding.refs
        assert EvidenceRef.parse(ref).path == "os"
        value = resolve(repo, ref).value
        assert value["os_name"] == "macOS"
        assert value["os_version"] == finding.values["os_version"], (tenant, finding.device_id)
        assert int(re.match(r"\d+", value["os_version"]).group()) < 15
        checked += 1
    assert checked > 0


@pytest.mark.parametrize("name", ["compliance_failures", "compliance_failures_laptops"])
def test_compliance_failure_refs_resolve_to_failures(evaluated, name):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, name):
        severities = {}
        for ref in finding.refs:
            match = COMPLIANCE_PATH.fullmatch(EvidenceRef.parse(ref).path)
            assert match, ref
            value = resolve(repo, ref).value
            assert value["status"] == "fail", (tenant, ref)
            assert value["check_id"] == match.group(1)
            severities[value["check_id"]] = value["severity"]
            checked += 1
        stated = {item["check_id"]: item["severity"] for item in finding.values["failing"]}
        assert severities == stated, (tenant, finding.device_id)
    assert checked > 0


def breached_findings_agree(evaluated, name, path, pct, breached):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, name):
        evidence = [resolve(repo, ref) for ref in finding.refs]
        assert {EvidenceRef.parse(ref).path for ref in finding.refs} == {path}
        newest = max(evidence, key=lambda e: e.collected_at)  # the always-cited latest snapshot
        for item in evidence:
            if item is not newest:
                assert breached(pct(item.value)), (tenant, item.ref)
        assert pct(newest.value) == pytest.approx(finding.values["latest_value"], rel=1e-9)
        assert sum(breached(pct(e.value)) for e in evidence) == finding.values["breached"]
        checked += 1
    assert checked > 0


def test_memory_trend_refs_are_breached_snapshots_plus_the_latest(evaluated):
    breached_findings_agree(
        evaluated, "memory_trend", "memory", used_pct, lambda pct: pct >= MEMORY_PCT
    )


def test_disk_trend_refs_are_breached_snapshots_plus_the_latest(evaluated):
    breached_findings_agree(
        evaluated, "disk_trend", "disk", free_pct, lambda pct: pct < LOW_DISK_PCT
    )


@pytest.mark.parametrize(
    ("name", "check_id"),
    [("screen_lock_trend", "screen_lock"), ("os_up_to_date_trend", "os_up_to_date")],
)
def test_compliance_trend_refs_show_the_stated_change(evaluated, name, check_id):
    # (state before the change, state from the change on) for each kind of finding
    shape = {
        "transition": ("pass", "fail"),
        "recovered": ("fail", "pass"),
        "persistent_failure": ("fail", "fail"),
    }
    kinds = set()
    for tenant, repo, _, finding in findings_of(evaluated, name):
        kind = finding.values["kind"]
        kinds.add(kind)
        before, after = shape[kind]
        evidence = [resolve(repo, ref) for ref in finding.refs]
        assert all(e.value["check_id"] == check_id for e in evidence)
        statuses = [e.value["status"] for e in evidence]
        assert statuses[0] == before, (tenant, finding.device_id, statuses)
        assert statuses[1] == after, (tenant, finding.device_id, statuses)
        assert set(statuses[1:]) == {after}
        if kind == "persistent_failure":
            assert finding.values["change_at"] is None
        else:
            assert finding.values["change_at"] == evidence[1].collected_at
    assert kinds, f"{name} had no findings to check"


def test_battery_values_agree_with_resolved_evidence(evaluated):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, "battery"):
        evidence = sorted(
            (resolve(repo, ref) for ref in finding.refs), key=lambda e: e.collected_at
        )
        assert {EvidenceRef.parse(ref).path for ref in finding.refs} == {"battery"}
        latest = evidence[-1]
        assert latest.collected_at == finding.values["latest_reading_at"]
        assert latest.value["cycle_count"] == finding.values["cycles_latest"]
        assert latest.value["condition"] == finding.values["latest_condition"]
        capacities = [
            e.value["full_charge_capacity"]
            for e in evidence
            if e.value["full_charge_capacity"] is not None
        ]
        if finding.values["capacity_change_pct"] is not None:
            change = (capacities[-1] - capacities[0]) / capacities[0] * 100.0
            assert change == pytest.approx(finding.values["capacity_change_pct"], rel=1e-9), (
                tenant,
                finding.device_id,
            )
        if finding.values["service_streak"] > 0:
            started = [e for e in evidence if e.collected_at == finding.values["first_service_at"]]
            assert started and started[0].value["condition"] == "Service Recommended"
        checked += 1
    assert checked > 0


def test_software_holder_values_agree_with_resolved_evidence(evaluated):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, "software_holders"):
        (ref,) = finding.refs
        evidence = resolve(repo, ref)
        assert EvidenceRef.parse(ref).path == "software[uTorrent]"
        assert evidence.collected_at == finding.values["collected_at"]
        assert evidence.value == {
            "name": finding.values["name"],
            "version": finding.values["version"],
            "publisher": finding.values["publisher"],
        }, tenant
        checked += 1
    assert checked > 0


def test_new_software_refs_show_the_first_and_the_last_sighting(evaluated):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, "new_software"):
        values = finding.values
        first, last = (resolve(repo, ref) for ref in (finding.refs[0], finding.refs[-1]))
        assert first.collected_at == values["first_seen"], (tenant, finding.device_id)
        assert last.collected_at == values["last_seen"], (tenant, finding.device_id)
        assert first.value["name"] == last.value["name"] == values["app"]
        assert first.value["version"] == values["version"]
        assert last.value["version"] == values["latest_version"]
        assert values["first_seen"] <= values["last_seen"]
        checked += 1
    assert checked > 0


def test_software_inventory_refs_name_apps_the_finding_lists(evaluated):
    checked = 0
    for tenant, repo, _, finding in findings_of(evaluated, "software_installed"):
        listed = {(a["name"], a["version"]) for a in finding.values["apps"]}
        extra = {(a["name"], a["version"]) for a in finding.values["extra"]}
        cited = {(v["name"], v["version"]) for v in (resolve(repo, r).value for r in finding.refs)}
        assert cited <= listed, (tenant, finding.device_id)
        assert cited == (extra or cited)  # extras are all cited; a device with none cites one app
        assert extra <= listed
        checked += 1
    assert checked > 0
