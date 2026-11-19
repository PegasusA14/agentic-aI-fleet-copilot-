from __future__ import annotations

import json
import sqlite3
from typing import Any

from fleet_copilot.data.loader import connect, create_schema, finalize, insert_rows
from fleet_copilot.data.parse import parse_ndjson

_CHECKS = [
    {"check_id": "disk_encryption", "status": "pass", "severity": "high"},
    {"check_id": "screen_lock", "status": "pass", "severity": "medium"},
    {"check_id": "os_up_to_date", "status": "pass", "severity": "medium"},
]


def make_row(
    device: str = "DEV1",
    company: str = "acme-001",
    ts: str = "2026-06-12T09:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """A valid raw telemetry row. Passing `key=None` removes that key from the row."""
    row: dict[str, Any] = {
        "device_id": device,
        "company_id": company,
        "employee_id": f"emp-{company.split('-')[0]}-1000",
        "collected_at": ts,
        "agent_version": "42.0.0",
        "os": {"product_name": "macOS", "product_version": "14.5", "hostname": f"{company[:4]}-host-{device}"},
        "device_identity": {"model_name": "MacBook Pro"},
        "memory": {"total_memory_bytes": 17179869184, "used_memory_bytes": 8589934592},
        "disk_volumes": [
            {"mount_point": "/", "size_bytes": 1000, "available_bytes": 500, "encrypted": True}
        ],
        "battery": {
            "battery_present": True,
            "condition": "Normal",
            "cycle_count": 100,
            "full_charge_capacity": 5000,
            "percentage": 80,
        },
        "compliance_results": [dict(c) for c in _CHECKS],
    }
    for key, value in overrides.items():
        if value is None:
            row.pop(key, None)
        else:
            row[key] = value
    return row


def make_conn(raw_rows: list[dict[str, Any]]) -> sqlite3.Connection:
    """In-memory database built from raw rows; fails loudly if any row is invalid."""
    conn = connect(":memory:")
    create_schema(conn)
    parsed = parse_ndjson(json.dumps(r) for r in raw_rows)
    assert not parsed.quarantined, parsed.quarantined
    assert not insert_rows(conn, parsed.rows)
    finalize(conn, "test-sha")
    return conn
