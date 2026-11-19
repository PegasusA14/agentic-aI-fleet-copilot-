from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fleet_copilot.data.models import DiskVolume
from fleet_copilot.data.parse import ParsedRow, Quarantined, parse_ndjson
from fleet_copilot.timeutil import iso_z

SCHEMA = """
CREATE TABLE dataset_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE devices (
    company_id  TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    hostname    TEXT NOT NULL,
    os_name     TEXT NOT NULL,
    model_name  TEXT NOT NULL,
    form_factor TEXT NOT NULL CHECK (form_factor IN ('laptop', 'desktop')),
    PRIMARY KEY (company_id, device_id)
);

CREATE TABLE snapshots (
    snapshot_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id                   TEXT NOT NULL,
    device_id                    TEXT NOT NULL,
    employee_id                  TEXT NOT NULL,
    collected_at                 TEXT NOT NULL,
    hostname                     TEXT NOT NULL,
    os_name                      TEXT NOT NULL,
    os_version                   TEXT NOT NULL,
    model_name                   TEXT NOT NULL,
    disk_mount                   TEXT NOT NULL,
    disk_size_bytes              INTEGER NOT NULL,
    disk_available_bytes         INTEGER NOT NULL,
    disk_encrypted               INTEGER NOT NULL,
    mem_total_bytes              INTEGER NOT NULL,
    mem_used_bytes               INTEGER NOT NULL,
    battery_present              INTEGER,
    battery_condition            TEXT,
    battery_cycle_count          INTEGER,
    battery_full_charge_capacity INTEGER,
    battery_percentage           INTEGER,
    UNIQUE (company_id, device_id, collected_at)
);

CREATE TABLE compliance (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots (snapshot_id),
    check_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, check_id)
);
"""

_DEVICES_FROM_LATEST = """
INSERT INTO devices (company_id, device_id, employee_id, hostname, os_name, model_name, form_factor)
SELECT s.company_id, s.device_id, s.employee_id, s.hostname, s.os_name, s.model_name,
       CASE WHEN EXISTS (
                SELECT 1 FROM snapshots b
                WHERE b.company_id = s.company_id AND b.device_id = s.device_id
                  AND b.battery_present = 1
            ) THEN 'laptop' ELSE 'desktop' END
FROM snapshots s
WHERE s.collected_at = (
    SELECT MAX(m.collected_at) FROM snapshots m
    WHERE m.company_id = s.company_id AND m.device_id = s.device_id
)
"""

_ROOT_MOUNTS = ("/", "C:\\")


class DatasetMismatch(Exception):
    """The dataset differs from the pinned version, or contains nothing loadable."""


@dataclass(frozen=True)
class LoadReport:
    snapshots: int
    devices: int
    companies: int
    quarantined: list[Quarantined]
    sha256: str
    min_collected_at: str
    max_collected_at: str


def connect(db_path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the fleet database.

    Use one connection per thread or request: a SQLite connection is bound to the thread that
    opened it and raises `ProgrammingError` if another thread touches it.

    `read_only=True` opens an existing file in SQLite's read-only mode, so code on a read path
    cannot modify it even by mistake; it never creates a missing file and rejects `:memory:`.

    `load_dataset` swaps a rebuilt database into place with `os.replace`, so a reader that is
    already open keeps the OLD file until it reopens the connection.
    """
    if read_only:
        if str(db_path) == ":memory:":
            raise ValueError("an in-memory database cannot be opened read-only")
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _primary_volume(volumes: list[DiskVolume]) -> DiskVolume:
    for volume in volumes:
        if volume.mount_point in _ROOT_MOUNTS:
            return volume
    return volumes[0]


def insert_rows(conn: sqlite3.Connection, parsed: list[ParsedRow]) -> list[Quarantined]:
    """Insert validated rows. Returns rows rejected at insert time (duplicates)."""
    rejected: list[Quarantined] = []
    for item in parsed:
        row = item.row
        volume = _primary_volume(row.disk_volumes)
        battery = row.battery
        try:
            cursor = conn.execute(
                """
                INSERT INTO snapshots (
                    company_id, device_id, employee_id, collected_at, hostname, os_name, os_version,
                    model_name, disk_mount, disk_size_bytes, disk_available_bytes, disk_encrypted,
                    mem_total_bytes, mem_used_bytes, battery_present, battery_condition,
                    battery_cycle_count, battery_full_charge_capacity, battery_percentage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.company_id,
                    row.device_id,
                    row.employee_id,
                    iso_z(row.collected_at),
                    row.os.hostname,
                    row.os.product_name,
                    row.os.product_version,
                    row.device_identity.model_name,
                    volume.mount_point,
                    volume.size_bytes,
                    volume.available_bytes,
                    int(volume.encrypted),
                    row.memory.total_memory_bytes,
                    row.memory.used_memory_bytes,
                    None if battery is None else int(battery.battery_present),
                    None if battery is None else battery.condition,
                    None if battery is None else battery.cycle_count,
                    None if battery is None else battery.full_charge_capacity,
                    None if battery is None else battery.percentage,
                ),
            )
        except sqlite3.IntegrityError:
            rejected.append(
                Quarantined(item.line_no, "duplicate snapshot for (company, device, collected_at)")
            )
            continue
        conn.executemany(
            "INSERT INTO compliance (snapshot_id, check_id, status, severity) VALUES (?, ?, ?, ?)",
            [(cursor.lastrowid, c.check_id, c.status, c.severity) for c in row.compliance_results],
        )
    return rejected


def finalize(conn: sqlite3.Connection, sha256: str) -> None:
    conn.execute(_DEVICES_FROM_LATEST)
    count, lowest, highest = conn.execute(
        "SELECT COUNT(*), MIN(collected_at), MAX(collected_at) FROM snapshots"
    ).fetchone()
    conn.executemany(
        "INSERT INTO dataset_meta (key, value) VALUES (?, ?)",
        [
            ("dataset_sha256", sha256),
            ("snapshot_count", str(count)),
            ("min_collected_at", lowest),
            ("max_collected_at", highest),
        ],
    )
    conn.commit()


def _discard(path: Path) -> None:
    """Remove a SQLite file and its rollback journal, if present."""
    path.unlink(missing_ok=True)
    path.with_name(path.name + "-journal").unlink(missing_ok=True)


def load_dataset(
    data_path: str | Path, db_path: str | Path, *, expected_sha256: str | None = None
) -> LoadReport:
    """Rebuild the SQLite database from the NDJSON file. Deterministic and idempotent.

    The database is built in a temp file next to the target and moved into place as the last
    step, so a rejected or failed load never damages an existing database.
    """
    data_path = Path(data_path)
    db_path = Path(db_path)
    if db_path.resolve() == data_path.resolve():
        raise ValueError("db_path must differ from data_path")

    raw = data_path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and sha256 != expected_sha256:
        raise DatasetMismatch(f"dataset sha256 {sha256} does not match expected {expected_sha256}")

    parsed = parse_ndjson(raw.decode("utf-8").split("\n"))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    _discard(tmp)
    conn: sqlite3.Connection | None = None
    try:
        conn = connect(tmp)
        create_schema(conn)
        rejected = insert_rows(conn, parsed.rows)
        loaded = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        if loaded == 0:
            raise DatasetMismatch("dataset contains no valid snapshots")
        finalize(conn, sha256)
        meta = dict(conn.execute("SELECT key, value FROM dataset_meta").fetchall())
        report = LoadReport(
            snapshots=loaded,
            devices=conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
            companies=conn.execute("SELECT COUNT(DISTINCT company_id) FROM devices").fetchone()[0],
            quarantined=[*parsed.quarantined, *rejected],
            sha256=sha256,
            min_collected_at=meta["min_collected_at"],
            max_collected_at=meta["max_collected_at"],
        )
        conn.close()
        conn = None
        os.replace(tmp, db_path)
        return report
    except BaseException:
        if conn is not None:
            conn.close()
        _discard(tmp)
        raise
