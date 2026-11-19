import json

import pytest
from factories import make_row

from fleet_copilot.config import EXPECTED_DATASET_SHA256, PINNED_AS_OF, Settings
from fleet_copilot.data.loader import DatasetMismatch, connect, load_dataset


def ndjson(tmp_path, *items):
    path = tmp_path / "data.ndjson"
    text = "\n".join(i if isinstance(i, str) else json.dumps(i) for i in items) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_rows_and_quarantines_bad_ones(tmp_path):
    data = ndjson(tmp_path, make_row("A"), "{broken", make_row("B", company="globex-002"))
    report = load_dataset(data, tmp_path / "db.sqlite")
    assert (report.snapshots, report.devices, report.companies) == (2, 2, 2)
    assert [q.line_no for q in report.quarantined] == [2]


def test_duplicate_snapshot_is_quarantined(tmp_path):
    data = ndjson(tmp_path, make_row("A"), make_row("A"))
    report = load_dataset(data, tmp_path / "db.sqlite")
    assert report.snapshots == 1
    assert report.quarantined[0].line_no == 2
    assert "duplicate" in report.quarantined[0].reason


def test_form_factor_is_derived_from_battery_presence(tmp_path):
    data = ndjson(tmp_path, make_row("LAP"), make_row("MINI", battery=None))
    load_dataset(data, tmp_path / "db.sqlite")
    conn = connect(tmp_path / "db.sqlite")
    got = {r["device_id"]: r["form_factor"] for r in conn.execute("SELECT * FROM devices")}
    assert got == {"LAP": "laptop", "MINI": "desktop"}


def test_primary_volume_prefers_the_root_mount(tmp_path):
    volumes = [
        {"mount_point": "/Volumes/Data", "size_bytes": 10, "available_bytes": 9, "encrypted": True},
        {"mount_point": "/", "size_bytes": 1000, "available_bytes": 500, "encrypted": True},
    ]
    load_dataset(ndjson(tmp_path, make_row("A", disk_volumes=volumes)), tmp_path / "db.sqlite")
    row = connect(tmp_path / "db.sqlite").execute("SELECT disk_mount, disk_size_bytes FROM snapshots").fetchone()
    assert (row["disk_mount"], row["disk_size_bytes"]) == ("/", 1000)


def test_devices_use_the_latest_snapshot_identity(tmp_path):
    older = make_row("A", ts="2026-06-11T09:00:00Z", os={"product_name": "macOS", "product_version": "14.5", "hostname": "old-name"})
    newer = make_row("A", ts="2026-06-12T09:00:00Z", os={"product_name": "macOS", "product_version": "15.4", "hostname": "new-name"})
    load_dataset(ndjson(tmp_path, older, newer), tmp_path / "db.sqlite")
    row = connect(tmp_path / "db.sqlite").execute("SELECT hostname FROM devices").fetchone()
    assert row["hostname"] == "new-name"


def test_sha_mismatch_is_rejected(tmp_path):
    data = ndjson(tmp_path, make_row("A"))
    with pytest.raises(DatasetMismatch, match="sha256"):
        load_dataset(data, tmp_path / "db.sqlite", expected_sha256="0" * 64)


def test_dataset_without_valid_rows_is_rejected(tmp_path):
    with pytest.raises(DatasetMismatch, match="no valid snapshots"):
        load_dataset(ndjson(tmp_path, "{broken"), tmp_path / "db.sqlite")


def test_reload_rebuilds_instead_of_duplicating(tmp_path):
    data = ndjson(tmp_path, make_row("A"), make_row("A", ts="2026-06-11T09:00:00Z"))
    first = load_dataset(data, tmp_path / "db.sqlite")
    second = load_dataset(data, tmp_path / "db.sqlite")
    assert first.snapshots == second.snapshots == 2


def test_real_dataset_matches_the_pinned_facts(tmp_path):
    report = load_dataset(
        Settings.from_env().data_path, tmp_path / "db.sqlite", expected_sha256=EXPECTED_DATASET_SHA256
    )
    assert (report.snapshots, report.devices, report.companies) == (750, 25, 3)
    assert report.quarantined == []
    assert report.min_collected_at == "2026-05-14T08:40:00Z"
    assert report.max_collected_at == PINNED_AS_OF
