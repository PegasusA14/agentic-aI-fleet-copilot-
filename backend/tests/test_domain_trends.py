import pytest

from fleet_copilot.domain.trends import ComplianceTrend, classify_breach_trend, classify_compliance

T, F = True, False


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], "insufficient"),
        ([T, T], "insufficient"),
        ([F] * 30, "none"),
        ([T] * 7, "sustained"),
        ([T] * 24 + [F] * 6, "recovering"),
        ([F] * 6 + [T] * 24, "sustained"),            # exactly 80% and latest breached
        ([F] * 15 + [T] * 15, "onset"),                 # crossed once and stayed there
        ([F] * 25 + [T] * 5, "onset"),
        ([F] * 28 + [T] * 2, "isolated"),               # run of 2 is below the onset minimum
        ([T] * 3 + [F] * 27, "isolated"),               # old spike, latest clear
        ([F] * 10 + [T] + [F] * 19, "isolated"),        # one-off spike
        ([T, F] * 15, "intermittent"),
    ],
)
def test_breach_trend_classes(flags, expected):
    assert classify_breach_trend(flags) == expected


def test_scattered_breaches_are_intermittent_but_two_spikes_are_isolated():
    scattered = ([T, F, F] * 10)[:30]
    assert classify_breach_trend(scattered) == "intermittent"
    two_spikes = [F] * 30
    two_spikes[8] = two_spikes[10] = T
    assert classify_breach_trend(two_spikes) == "isolated"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], ComplianceTrend("insufficient", None)),
        (["fail"], ComplianceTrend("insufficient", None)),
        (["fail", "fail"], ComplianceTrend("insufficient", None)),
        (["fail"] * 30, ComplianceTrend("persistent_failure", None)),
        (["pass"] * 30, ComplianceTrend("passing", None)),
        (["pass"] * 8 + ["fail"] * 22, ComplianceTrend("transition", 8)),
        (["fail"] * 5 + ["pass"] * 3, ComplianceTrend("recovered", 5)),
        (["pass", "fail"], ComplianceTrend("transition", 1)),
        (["fail", "pass", "fail"], ComplianceTrend("transition", 2)),
    ],
)
def test_compliance_trend(statuses, expected):
    assert classify_compliance(statuses) == expected
