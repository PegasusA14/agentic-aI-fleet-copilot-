import pytest

from fleet_copilot.agent.format import fmt_bytes, fmt_day, fmt_pct, fmt_signed_pct, fmt_ts, noun


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KiB"),
        (1099511627776, "1.0 TiB"),
        (549755813888, "512.0 GiB"),
        (11 * 10**9, "10.2 GiB"),
    ],
)
def test_binary_byte_units(value, expected):
    assert fmt_bytes(value) == expected


def test_percentages_and_timestamps():
    assert fmt_pct(2.0009) == "2.0%"
    assert fmt_pct(100) == "100.0%"
    assert fmt_signed_pct(-4.2236) == "-4.2%"
    assert fmt_signed_pct(0.0) == "+0.0%"
    assert fmt_ts("2026-06-12T09:02:00Z") == "2026-06-12 09:02Z"
    assert fmt_day("2026-05-22T09:41:00Z") == "2026-05-22"


def test_noun_agreement():
    assert noun(1, "device") == "device"
    assert noun(0, "device") == "devices"
    assert noun(2, "device") == "devices"
