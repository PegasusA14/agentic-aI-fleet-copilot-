import inspect
import json

import pytest

from fleet_copilot.config import REPO_ROOT
from fleet_copilot.domain.windows import STALE_AFTER
from fleet_copilot.fleet import health
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

GOLDEN = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
TENANTS = sorted(GOLDEN["tenants"])
CHECKS = ["disk_encryption", "screen_lock", "os_up_to_date"]


def repo_for(conn, tenant):
    return FleetRepository(conn, TenantContext(tenant, "golden-test"))


def ids(result):
    return sorted(f.device_id for f in result.findings)


@pytest.mark.parametrize("tenant", TENANTS)
def test_low_disk(real_conn, tenant):
    want = GOLDEN["tenants"][tenant]["low_disk_lt10"]
    got = q.low_disk(repo_for(real_conn, tenant), dataset_as_of(real_conn), 10.0)
    assert ids(got) == want["devices"]
    assert got.population == want["population"]


@pytest.mark.parametrize("tenant", TENANTS)
def test_macos_below_15(real_conn, tenant):
    want = GOLDEN["tenants"][tenant]["macos_below_15"]
    got = q.macos_below(repo_for(real_conn, tenant), dataset_as_of(real_conn), "15")
    assert ids(got) == want["devices"]
    assert got.population == want["population"]
    assert list(got.excluded.get("out_of_scope", ())) == want["out_of_scope"]
    assert list(got.excluded.get("unknown_version", ())) == want["unknown_version"]


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("form", ["all", "laptop"])
@pytest.mark.parametrize("check", CHECKS)
def test_failing_checks_at_the_latest_snapshot(real_conn, tenant, form, check):
    want = GOLDEN["tenants"][tenant]["failing_latest"][form][check]
    got = q.compliance_failures(
        repo_for(real_conn, tenant),
        dataset_as_of(real_conn),
        check_id=check,
        form_factor=None if form == "all" else form,
    )
    assert ids(got) == want


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("form", ["all", "laptop"])
def test_high_severity_failures(real_conn, tenant, form):
    want = GOLDEN["tenants"][tenant]["failing_high_severity"][form]
    got = q.compliance_failures(
        repo_for(real_conn, tenant),
        dataset_as_of(real_conn),
        severity="high",
        form_factor=None if form == "all" else form,
    )
    assert ids(got) == want


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("threshold", ["85", "90"])
@pytest.mark.parametrize("window", ["7", "30"])
def test_memory_trend(real_conn, tenant, threshold, window):
    want = GOLDEN["tenants"][tenant]["memory_trend"][threshold][window]
    got = q.memory_trend(
        repo_for(real_conn, tenant),
        dataset_as_of(real_conn),
        window_days=int(window),
        threshold_pct=float(threshold),
    )
    assert {f.device_id: f.values["trend"] for f in got.findings} == want


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("window", ["7", "30"])
def test_disk_trend(real_conn, tenant, window):
    want = GOLDEN["tenants"][tenant]["disk_trend_lt10"][window]
    got = q.disk_trend(
        repo_for(real_conn, tenant), dataset_as_of(real_conn), window_days=int(window)
    )
    assert {f.device_id: f.values["trend"] for f in got.findings} == want


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("check", ["screen_lock", "os_up_to_date"])
def test_compliance_trend(real_conn, tenant, check):
    want = GOLDEN["tenants"][tenant]["compliance_trend"][check]["30"]
    got = q.compliance_trend(
        repo_for(real_conn, tenant), dataset_as_of(real_conn), check_id=check, window_days=30
    )
    assert {
        f.device_id: {"kind": f.values["kind"], "change_at": f.values["change_at"]}
        for f in got.findings
    } == want


@pytest.mark.parametrize("tenant", TENANTS)
def test_battery(real_conn, tenant):
    want = GOLDEN["tenants"][tenant]["battery_30"]
    got = q.battery_overview(repo_for(real_conn, tenant), dataset_as_of(real_conn), 30)
    assert ids(got) == sorted(want["notable"])
    for finding in got.findings:
        expected = want["notable"][finding.device_id]
        assert finding.values["service_streak"] == expected["service_streak"]
        if expected["capacity_change_pct"] is None:
            assert finding.values["capacity_change_pct"] is None
        else:
            assert finding.values["capacity_change_pct"] == pytest.approx(
                expected["capacity_change_pct"], abs=1e-3
            )
    assert list(got.missing_latest) == want["missing_latest"]
    assert list(got.excluded.get("no_reading", ())) == want["no_reading"]


@pytest.mark.parametrize("tenant", TENANTS)
def test_devices_and_laptops_match_the_golden_file(real_conn, tenant):
    want = GOLDEN["tenants"][tenant]
    repo = repo_for(real_conn, tenant)
    devices = repo.list_devices()
    assert [d.device_id for d in devices] == want["devices"]
    assert [d.device_id for d in devices if d.form_factor == "laptop"] == want["laptops"]
    battery = q.battery_overview(repo, dataset_as_of(real_conn), 30)
    assert battery.population == len(want["laptops"])


def test_product_defaults_match_the_golden_policy():
    policy = GOLDEN["meta"]["policy"]

    def default(fn, name):
        return inspect.signature(fn).parameters[name].default

    assert default(q.low_disk, "threshold_pct") == policy["low_disk_free_pct_below"]
    assert default(q.disk_trend, "threshold_pct") == policy["low_disk_free_pct_below"]
    assert sorted(policy["memory_used_pct_at_least"]) == [85, 90]
    assert default(q.memory_trend, "threshold_pct") == policy["memory_used_pct_at_least"][0]
    assert STALE_AFTER.total_seconds() / 3600 == policy["stale_hours"]
    assert policy["windows_days"] == [7, 30]


def _by_device(result):
    return {f.device_id: f for f in result.findings}


@pytest.mark.parametrize("tenant", TENANTS)
def test_battery_insight_agrees_with_the_oracle(real_conn, tenant):
    want = GOLDEN["tenants"][tenant]["battery_insight"]
    got = _by_device(q.battery_overview(repo_for(real_conn, tenant), dataset_as_of(real_conn), 30))
    for device, expected in want["devices"].items():
        if device not in got:
            assert expected["severity"] == "ok", device
            continue
        values = got[device].values
        assert values["severity"] == expected["severity"], device
        assert values["service_streak"] == expected["service_streak"]
        assert values["fit"]["n"] == expected["readings"]
        assert values["rate_per_day"] == pytest.approx(expected["slope_per_day"], abs=1e-4)
        projection = values["projection"]
        assert projection["status"] == expected["status"], device
        for key in ("estimate", "earliest", "latest"):
            assert projection[key] == expected[key], (device, key)
    needs = sorted(d for d, f in got.items() if f.values["severity"] != "ok")
    assert needs == want["needs_attention"]
    assert (
        sorted(d for d, f in got.items() if f.values["severity"] == "act_soon") == want["act_soon"]
    )


@pytest.mark.parametrize("tenant", TENANTS)
def test_fleet_health_agrees_with_the_oracle(real_conn, tenant):
    want = GOLDEN["tenants"][tenant]["fleet_health"]
    result = health.fleet_health(repo_for(real_conn, tenant), dataset_as_of(real_conn), 30)
    worst = {}
    for f in result.findings:
        level = f.values["severity"]
        if worst.get(f.device_id) != "act_soon":
            worst[f.device_id] = level
    assert sorted(d for d, level in worst.items() if level == "act_soon") == want["act_soon"]
    assert sorted(d for d, level in worst.items() if level == "watch") == want["watch"]
    assert len(worst) == want["devices_needing_attention"]
