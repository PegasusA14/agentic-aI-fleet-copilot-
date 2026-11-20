import json
from contextlib import closing

import pytest
from factories import BASE_SOFTWARE, make_row

from fleet_copilot.config import EXPECTED_DATASET_SHA256, Settings
from fleet_copilot.data.loader import connect, load_dataset
from fleet_copilot.data.models import InstalledSoftware
from fleet_copilot.data.parse import parse_ndjson

UTORRENT = {"name": "uTorrent", "version": "3.6", "publisher": "Rainberry Inc"}


def lines(*rows):
    return [json.dumps(r) for r in rows]


def write(tmp_path, *rows):
    path = tmp_path / "data.ndjson"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# ---- parsing -------------------------------------------------------------------------------------


def test_installed_software_is_parsed_into_typed_items():
    row = make_row("A", installed_software=[*BASE_SOFTWARE, UTORRENT])
    parsed = parse_ndjson(lines(row)).rows[0].row
    assert [s.name for s in parsed.installed_software] == ["Google Chrome", "Slack", "uTorrent"]
    assert parsed.installed_software[2] == InstalledSoftware(**UTORRENT)


def test_an_empty_software_list_is_valid_but_a_missing_one_is_not():
    assert parse_ndjson(lines(make_row("A", installed_software=[]))).quarantined == []
    missing = make_row("A")
    del missing["installed_software"]
    result = parse_ndjson(lines(missing))
    assert result.rows == [] and "installed_software" in result.quarantined[0].reason


@pytest.mark.parametrize(
    "bad_item",
    [
        {"name": "", "version": "1.0", "publisher": "P"},
        {"name": " lead", "version": "1.0", "publisher": "P"},
        {"name": "trail ", "version": "1.0", "publisher": "P"},
        {"name": "x" * 61, "version": "1.0", "publisher": "P"},
        {"name": "Evil<script>", "version": "1.0", "publisher": "P"},
        {"name": "a[b]", "version": "1.0", "publisher": "P"},
        {"name": "a#b", "version": "1.0", "publisher": "P"},
        {"name": "a/b", "version": "1.0", "publisher": "P"},
        {"name": "App", "version": "", "publisher": "P"},
        {"name": "App", "version": "1 2", "publisher": "P"},
        {"name": "App", "version": "v" * 41, "publisher": "P"},
        {"name": "App", "version": "1.0", "publisher": ""},
        {"name": "App", "version": "1.0", "publisher": "<b>P</b>"},
        {"name": "App", "version": "1.0"},
        {"name": "App", "publisher": "P"},
        {"version": "1.0", "publisher": "P"},
        "App 1.0",
    ],
)
def test_a_bad_software_item_quarantines_the_row_without_echoing_it(bad_item):
    result = parse_ndjson(lines(make_row("A", installed_software=[bad_item]), make_row("B")))
    assert [p.row.device_id for p in result.rows] == ["B"]
    assert result.quarantined[0].line_no == 1
    assert result.quarantined[0].reason.startswith("installed_software.0")
    assert "script" not in result.quarantined[0].reason


def test_the_allowed_name_characters_are_letters_digits_space_and_dot_underscore_plus_minus():
    for name in ["1Password", "CleanMyMac X", "Node.js", "C++ Tools", "a_b-c", "x" * 60, "A"]:
        item = {"name": name, "version": "1.0", "publisher": "P"}
        assert parse_ndjson(lines(make_row("A", installed_software=[item]))).quarantined == [], name


def test_duplicate_names_in_one_row_are_quarantined_case_insensitively():
    dup = [
        {"name": "Slack", "version": "1", "publisher": "P"},
        {"name": "slack", "version": "2", "publisher": "P"},
    ]
    result = parse_ndjson(lines(make_row("A", installed_software=dup)))
    assert "duplicate name" in result.quarantined[0].reason


def test_too_many_software_items_are_quarantined():
    many = [{"name": f"App{i}", "version": "1", "publisher": "P"} for i in range(201)]
    assert parse_ndjson(lines(make_row("A", installed_software=many))).rows == []


def test_network_is_still_dropped_and_the_model_has_no_network_field():
    row = make_row("A")
    row["network"] = [{"address": "10.0.0.1", "mac": "aa:bb:cc:dd:ee:ff"}]
    parsed = parse_ndjson(lines(row)).rows[0].row
    assert not hasattr(parsed, "network")


# ---- loading -------------------------------------------------------------------------------------


def test_software_rows_are_stored_per_snapshot(tmp_path):
    rows = [
        make_row("A", ts="2026-06-11T09:00:00Z"),
        make_row("A", ts="2026-06-12T09:00:00Z", installed_software=[*BASE_SOFTWARE, UTORRENT]),
    ]
    load_dataset(write(tmp_path, *rows), tmp_path / "db.sqlite")
    with closing(connect(tmp_path / "db.sqlite")) as conn:
        found = conn.execute(
            "SELECT s.collected_at, w.name, w.version, w.publisher FROM software w "
            "JOIN snapshots s ON s.snapshot_id = w.snapshot_id ORDER BY s.collected_at, w.name"
        ).fetchall()
    assert [(r["collected_at"][:10], r["name"]) for r in found] == [
        ("2026-06-11", "Google Chrome"),
        ("2026-06-11", "Slack"),
        ("2026-06-12", "Google Chrome"),
        ("2026-06-12", "Slack"),
        ("2026-06-12", "uTorrent"),
    ]
    assert found[-1]["version"] == "3.6" and found[-1]["publisher"] == "Rainberry Inc"


def test_the_software_table_has_the_documented_shape(tmp_path):
    load_dataset(write(tmp_path, make_row("A")), tmp_path / "db.sqlite")
    with closing(connect(tmp_path / "db.sqlite")) as conn:
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(software)")]
        keys = [r["name"] for r in conn.execute("PRAGMA table_info(software)") if r["pk"]]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert columns == ["snapshot_id", "name", "version", "publisher"]
    assert keys == ["snapshot_id", "name"]
    assert "network" not in tables


def test_a_row_with_bad_software_is_quarantined_and_the_rest_still_load(tmp_path):
    bad = make_row("A", installed_software=[{"name": "a;b", "version": "1", "publisher": "P"}])
    report = load_dataset(write(tmp_path, bad, make_row("B")), tmp_path / "db.sqlite")
    assert (report.snapshots, [q.line_no for q in report.quarantined]) == (1, [1])
    with closing(connect(tmp_path / "db.sqlite")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM software").fetchone()[0] == 2


def test_the_real_dataset_loads_every_software_row(tmp_path):
    report = load_dataset(
        Settings.from_env().data_path,
        tmp_path / "db.sqlite",
        expected_sha256=EXPECTED_DATASET_SHA256,
    )
    assert report.quarantined == []
    with closing(connect(tmp_path / "db.sqlite", read_only=True)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM software").fetchone()[0]
        per_snapshot = conn.execute(
            "SELECT n, COUNT(*) AS snapshots FROM "
            "(SELECT COUNT(*) AS n FROM software GROUP BY snapshot_id) GROUP BY n ORDER BY n"
        ).fetchall()
        names = [r[0] for r in conn.execute("SELECT DISTINCT name FROM software ORDER BY name")]
    assert total == 645 * 5 + 105 * 6
    assert [(r["n"], r["snapshots"]) for r in per_snapshot] == [(5, 645), (6, 105)]
    assert names == [
        "1Password",
        "CleanMyMac X",
        "Google Chrome",
        "Slack",
        "TeamViewer",
        "Visual Studio Code",
        "Zoom",
        "uTorrent",
    ]
