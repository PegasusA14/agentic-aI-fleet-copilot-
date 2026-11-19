from datetime import UTC, datetime, timedelta, timezone

from fleet_copilot.timeutil import iso_z, parse_ts


def test_round_trip():
    assert iso_z(parse_ts("2026-06-12T11:00:00Z")) == "2026-06-12T11:00:00Z"


def test_parse_returns_aware_utc():
    assert parse_ts("2026-06-12T11:00:00Z").tzinfo == UTC


def test_iso_z_converts_other_offsets_to_utc():
    plus_two = timezone(timedelta(hours=2))
    assert iso_z(datetime(2026, 6, 12, 13, 0, tzinfo=plus_two)) == "2026-06-12T11:00:00Z"
