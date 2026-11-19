import json

import pytest
from factories import make_row

from fleet_copilot.data.parse import parse_ndjson


def lines(*rows):
    return [json.dumps(r) for r in rows]


def test_valid_rows_parse_with_line_numbers():
    result = parse_ndjson(lines(make_row("A"), make_row("B")))
    assert [p.row.device_id for p in result.rows] == ["A", "B"]
    assert [p.line_no for p in result.rows] == [1, 2]
    assert result.quarantined == []


def test_blank_lines_are_skipped_but_still_counted():
    result = parse_ndjson([json.dumps(make_row("A")), "", json.dumps(make_row("B"))])
    assert [p.line_no for p in result.rows] == [1, 3]


def test_missing_battery_is_allowed():
    result = parse_ndjson(lines(make_row("A", battery=None)))
    assert result.rows[0].row.battery is None


def test_malformed_json_is_quarantined():
    result = parse_ndjson([json.dumps(make_row("A")), "{not json"])
    assert [p.line_no for p in result.rows] == [1]
    assert result.quarantined[0].line_no == 2
    assert "Invalid JSON" in result.quarantined[0].reason


def test_empty_disk_list_is_quarantined():
    result = parse_ndjson(lines(make_row("A", disk_volumes=[])))
    assert result.rows == []
    assert "disk_volumes" in result.quarantined[0].reason


def test_naive_timestamp_is_quarantined():
    result = parse_ndjson(lines(make_row("A", ts="2026-06-12T09:00:00")))
    assert "timezone" in result.quarantined[0].reason


def test_used_memory_above_total_is_quarantined():
    bad = {"total_memory_bytes": 10, "used_memory_bytes": 11}
    result = parse_ndjson(lines(make_row("A", memory=bad)))
    assert "used_memory_bytes" in result.quarantined[0].reason


def test_available_disk_above_size_is_quarantined():
    bad = [{"mount_point": "/", "size_bytes": 10, "available_bytes": 11, "encrypted": True}]
    result = parse_ndjson(lines(make_row("A", disk_volumes=bad)))
    assert "available_bytes" in result.quarantined[0].reason


def test_unknown_compliance_status_is_quarantined():
    bad = [{"check_id": "screen_lock", "status": "maybe", "severity": "medium"}]
    result = parse_ndjson(lines(make_row("A", compliance_results=bad)))
    assert "status" in result.quarantined[0].reason


def test_duplicate_check_ids_are_quarantined():
    dup = [{"check_id": "screen_lock", "status": "pass", "severity": "medium"}] * 2
    result = parse_ndjson(lines(make_row("A", compliance_results=dup)))
    assert "duplicate check_id" in result.quarantined[0].reason


def test_network_data_is_never_retained():
    row = make_row("A")
    row["network"] = [{"address": "10.0.0.1", "mac": "aa:bb:cc:dd:ee:ff"}]
    parsed = parse_ndjson(lines(row)).rows[0].row
    assert not hasattr(parsed, "network")


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"])
def test_out_of_range_timestamp_is_quarantined_and_following_rows_still_parse(ts):
    result = parse_ndjson(lines(make_row("A", ts=ts), make_row("B")))
    assert [p.row.device_id for p in result.rows] == ["B"]
    assert [p.line_no for p in result.rows] == [2]
    assert result.quarantined[0].line_no == 1
    assert "out of range" in result.quarantined[0].reason
