import os
import time
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from fleet_copilot.timeutil import iso_z, parse_ts


def test_round_trip():
    assert iso_z(parse_ts("2026-06-12T11:00:00Z")) == "2026-06-12T11:00:00Z"


def test_parse_returns_aware_utc():
    assert parse_ts("2026-06-12T11:00:00Z").tzinfo == UTC


def test_iso_z_converts_other_offsets_to_utc():
    plus_two = timezone(timedelta(hours=2))
    assert iso_z(datetime(2026, 6, 12, 13, 0, tzinfo=plus_two)) == "2026-06-12T11:00:00Z"


def test_iso_z_converts_negative_offsets_too():
    minus_seven = timezone(timedelta(hours=-7))
    assert iso_z(datetime(2026, 6, 12, 4, 0, tzinfo=minus_seven)) == "2026-06-12T11:00:00Z"


def test_iso_z_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone-aware"):
        iso_z(datetime(2026, 6, 12, 11, 0))


def test_iso_z_rejects_a_tzinfo_that_gives_no_offset():
    class NoOffset(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

        def tzname(self, dt):
            return None

    with pytest.raises(ValueError, match="timezone-aware"):
        iso_z(datetime(2026, 6, 12, 11, 0, tzinfo=NoOffset()))


@pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="needs time.tzset to change the machine zone"
)
def test_iso_z_does_not_depend_on_the_machine_time_zone():
    aware = datetime(2026, 6, 12, 13, 0, tzinfo=timezone(timedelta(hours=2)))
    naive = datetime(2026, 6, 12, 11, 0)
    previous = os.environ.get("TZ")
    seen = {}
    try:
        for zone in ("Asia/Kolkata", "America/Los_Angeles"):
            os.environ["TZ"] = zone
            time.tzset()
            with pytest.raises(ValueError, match="timezone-aware"):
                iso_z(naive)  # never guessed from the machine's zone
            seen[zone] = iso_z(aware)
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
    assert seen == {
        "Asia/Kolkata": "2026-06-12T11:00:00Z",
        "America/Los_Angeles": "2026-06-12T11:00:00Z",
    }
