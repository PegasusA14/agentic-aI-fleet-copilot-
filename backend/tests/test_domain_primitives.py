from datetime import UTC, datetime, timedelta

import pytest

from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.domain.osversion import macos_older_than, parse_version
from fleet_copilot.domain.windows import STALE_AFTER, is_stale, window_start

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)


def test_free_and_used_percentages():
    assert free_pct(1000, 25) == 2.5
    assert used_pct(1000, 850) == 85.0


def test_percentages_are_exact_at_whole_number_thresholds():
    # A single rounding division keeps exact-threshold volumes exactly on the threshold.
    assert free_pct(100, 29) == 29.0
    assert free_pct(100, 7) == 7.0
    assert free_pct(20, 11) == 55.0
    assert not free_pct(100, 29) < 29.0
    assert used_pct(100, 85) == 85.0
    assert used_pct(25, 14) == 56.0


@pytest.mark.parametrize("fn", [free_pct, used_pct])
def test_zero_totals_are_rejected(fn):
    with pytest.raises(ValueError):
        fn(0, 0)


def test_window_start_counts_back_from_as_of():
    assert window_start(AS_OF, 7) == AS_OF - timedelta(days=7)
    with pytest.raises(ValueError):
        window_start(AS_OF, 0)


def test_staleness_uses_the_48_hour_limit():
    assert STALE_AFTER == timedelta(hours=48)
    assert not is_stale(AS_OF - timedelta(hours=48), AS_OF)
    assert is_stale(AS_OF - timedelta(hours=48, seconds=1), AS_OF)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("14.5", "older"),
        ("13.6", "older"),
        ("9.9", "older"),
        ("15.3", "not_older"),
        ("15.4", "not_older"),
        ("15.10", "not_older"),
        ("16.0", "not_older"),
    ],
)
def test_macos_older_than_15(version, expected):
    assert macos_older_than("macOS", version, "15") == expected


def test_version_comparison_is_numeric_not_lexicographic():
    assert macos_older_than("macOS", "15.10", "15.9") == "not_older"
    assert macos_older_than("macOS", "15.2", "15.10") == "older"


@pytest.mark.parametrize(
    ("version", "target", "expected"),
    [
        # equal to the target is not older
        ("15", "15", "not_older"),
        ("15.0", "15", "not_older"),
        ("15.4", "15.4", "not_older"),
        # just under the target is older
        ("14.9", "15", "older"),
        ("14.99", "15", "older"),
        # shorter than the target: missing parts count as zero
        ("14", "15.1", "older"),
        ("15", "15.0.1", "older"),
        ("15", "15.0", "not_older"),
    ],
)
def test_macos_version_boundaries_and_zero_padding(version, target, expected):
    assert macos_older_than("macOS", version, target) == expected


def test_only_macos_is_in_scope():
    assert macos_older_than("Windows", "10 22H2", "15") == "out_of_scope"
    assert macos_older_than("Windows", "11 23H2", "15") == "out_of_scope"


@pytest.mark.parametrize("version", ["", "Sonoma", "14.x", "14.5 beta"])
def test_unparseable_macos_versions_are_unknown(version):
    assert macos_older_than("macOS", version, "15") == "unknown"


def test_invalid_target_is_a_programming_error():
    with pytest.raises(ValueError):
        macos_older_than("macOS", "14.5", "fifteen")


def test_parse_version():
    assert parse_version("14.5") == (14, 5)
    assert parse_version(" 15 ") == (15,)
    assert parse_version("10 22H2") is None
