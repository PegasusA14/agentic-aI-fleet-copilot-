"""Persisted proposals, simulated effects and the append-only audit log (`actions.db`).

Every method takes the connection of one `ActionStore.transaction()`, so a decision, its effect row and its
audit entries commit together or not at all. The effect tables carry `UNIQUE (proposal_id)`: a second effect for
the same proposal is refused by the database itself, whatever the calling code does. SQLite triggers refuse any
UPDATE or DELETE on `audit_log`.

Tenant scoping is in the SQL (`company_id = ?`) of every read except the operator audit read (`company_id=None`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fleet_copilot.contracts import ProposalStatus
from fleet_copilot.timeutil import iso_z

AuditEvent = Literal[
    "proposal_created",
    "proposal_refused",
    "proposal_approved",
    "proposal_rejected",
    "proposal_executed",
    "proposal_invalidated",
    "proposal_failed",
    "approval_refused",
    "duplicate_approval",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id      TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    conversation_id  TEXT,
    trace_id         TEXT,
    action_type      TEXT NOT NULL,
    target_device_id TEXT NOT NULL,
    params           TEXT NOT NULL,
    payload_hash     TEXT NOT NULL,
    evidence_refs    TEXT NOT NULL,
    card             TEXT NOT NULL,
    status           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    decided_by       TEXT,
    decided_at       TEXT,
    detail           TEXT
);
CREATE INDEX IF NOT EXISTS proposals_tenant ON proposals (company_id, status, payload_hash);
CREATE TABLE IF NOT EXISTS tickets (
    effect_id   INTEGER PRIMARY KEY,
    proposal_id TEXT UNIQUE NOT NULL,
    company_id  TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    check_id    TEXT NOT NULL,
    note        TEXT NOT NULL,
    simulated   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replacement_flags (
    effect_id   INTEGER PRIMARY KEY,
    proposal_id TEXT UNIQUE NOT NULL,
    company_id  TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    simulated   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upgrade_orders (
    effect_id    INTEGER PRIMARY KEY,
    proposal_id  TEXT UNIQUE NOT NULL,
    company_id   TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    component    TEXT NOT NULL,
    current_spec TEXT NOT NULL,
    target_spec  TEXT NOT NULL,
    simulated    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employee_notifications (
    effect_id   INTEGER PRIMARY KEY,
    proposal_id TEXT UNIQUE NOT NULL,
    company_id  TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    topic       TEXT NOT NULL,
    message     TEXT NOT NULL,
    simulated   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    at               TEXT NOT NULL,
    company_id       TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    event            TEXT NOT NULL,
    proposal_id      TEXT,
    trace_id         TEXT,
    action_type      TEXT,
    target_device_id TEXT,
    payload_hash     TEXT,
    refs             TEXT NOT NULL DEFAULT '[]',
    detail           TEXT NOT NULL DEFAULT '{}'
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""

# The effect table and its writable columns, per action type. Column names are never taken from the caller.
_EFFECTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "remediation_ticket": ("tickets", ("device_id", "check_id", "note")),
    "replacement_flag": ("replacement_flags", ("device_id", "reason")),
    "upgrade_order": (
        "upgrade_orders",
        ("device_id", "component", "current_spec", "target_spec"),
    ),
    "employee_notification": (
        "employee_notifications",
        ("device_id", "employee_id", "topic", "message"),
    ),
}
_EFFECT_COMMON = ("proposal_id", "company_id")
_EFFECT_OPTIONAL = ("simulated", "created_at")
_MAX_LIMIT = 500


def utc_now() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def payload_hash(
    company_id: str,
    action_type: str,
    target_device_id: str,
    params: dict[str, str],
    *,
    employee_id: str | None = None,
    rationale: str = "",
) -> str:
    """The hash the approver signs, independent of key order.

    It covers the tenant, action, target and every parameter, plus what the effect or the card carries beyond
    the parameters: the notification recipient (`employee_id`) and the card's rationale (a ticket's note).
    """
    canonical = json.dumps(
        {
            "company_id": company_id,
            "action_type": action_type,
            "target_device_id": target_device_id,
            "params": params,
            "employee_id": employee_id,
            "rationale": rationale,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProposalRow:
    proposal_id: str
    company_id: str
    actor_id: str
    action_type: str
    target_device_id: str
    params: dict[str, str]
    payload_hash: str
    evidence_refs: list[str]
    card: dict[str, Any]
    conversation_id: str | None = None
    trace_id: str | None = None
    status: ProposalStatus = "pending"
    created_at: str | None = None  # filled from the store clock on insert
    decided_by: str | None = None
    decided_at: str | None = None
    detail: str | None = None


def _proposal(r: sqlite3.Row) -> ProposalRow:
    return ProposalRow(
        proposal_id=r["proposal_id"],
        company_id=r["company_id"],
        actor_id=r["actor_id"],
        conversation_id=r["conversation_id"],
        trace_id=r["trace_id"],
        action_type=r["action_type"],
        target_device_id=r["target_device_id"],
        params=json.loads(r["params"]),
        payload_hash=r["payload_hash"],
        evidence_refs=json.loads(r["evidence_refs"]),
        card=json.loads(r["card"]),
        status=r["status"],
        created_at=r["created_at"],
        decided_by=r["decided_by"],
        decided_at=r["decided_at"],
        detail=r["detail"],
    )


def _limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    return max(0, min(limit, _MAX_LIMIT))


class ActionStore:
    def __init__(self, path: Path, *, now: Callable[[], datetime] = utc_now) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._now = now
        with self.transaction() as conn:
            for statement in _statements(SCHEMA):
                conn.execute(statement)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One connection, `BEGIN IMMEDIATE` (writers queue), commit on success, roll back on any exception."""
        with closing(sqlite3.connect(self._path, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def insert_proposal(self, conn: sqlite3.Connection, row: ProposalRow) -> None:
        conn.execute(
            "INSERT INTO proposals (proposal_id, company_id, actor_id, conversation_id, trace_id, action_type,"
            " target_device_id, params, payload_hash, evidence_refs, card, status, created_at, decided_by,"
            " decided_at, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.proposal_id,
                row.company_id,
                row.actor_id,
                row.conversation_id,
                row.trace_id,
                row.action_type,
                row.target_device_id,
                _json(row.params),
                row.payload_hash,
                _json(row.evidence_refs),
                _json(row.card),
                row.status,
                row.created_at or iso_z(self._now()),
                row.decided_by,
                row.decided_at,
                row.detail,
            ),
        )

    def find_pending(
        self, conn: sqlite3.Connection, company_id: str, payload_hash: str
    ) -> ProposalRow | None:
        found = conn.execute(
            "SELECT * FROM proposals WHERE company_id = ? AND payload_hash = ? AND status = 'pending'"
            " ORDER BY rowid LIMIT 1",
            (company_id, payload_hash),
        ).fetchone()
        return _proposal(found) if found else None

    def find_executed(
        self,
        conn: sqlite3.Connection,
        company_id: str,
        action_type: str,
        target_device_id: str,
        params: dict[str, str],
        since: datetime,
    ) -> ProposalRow | None:
        """The latest EXECUTED proposal of this exact action (type, device, parameters) created at or after `since`."""
        found = conn.execute(
            "SELECT * FROM proposals WHERE company_id = ? AND action_type = ? AND target_device_id = ?"
            " AND params = ? AND status = 'executed' AND created_at >= ? ORDER BY rowid DESC LIMIT 1",
            (company_id, action_type, target_device_id, _json(params), iso_z(since)),
        ).fetchone()
        return _proposal(found) if found else None

    def count_pending(self, conn: sqlite3.Connection, company_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM proposals WHERE company_id = ? AND status = 'pending'",
            (company_id,),
        ).fetchone()
        return int(row["n"])

    def get(
        self, conn: sqlite3.Connection, company_id: str, proposal_id: str
    ) -> ProposalRow | None:
        found = conn.execute(
            "SELECT * FROM proposals WHERE company_id = ? AND proposal_id = ?",
            (company_id, proposal_id),
        ).fetchone()
        return _proposal(found) if found else None

    def list(
        self,
        conn: sqlite3.Connection,
        company_id: str,
        status: ProposalStatus | None = None,
        limit: int = 50,
    ) -> list[ProposalRow]:
        """This company's proposals, newest first."""
        sql = "SELECT * FROM proposals WHERE company_id = ?"
        args: list[Any] = [company_id]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        rows = conn.execute(sql + " ORDER BY rowid DESC LIMIT ?", (*args, _limit(limit))).fetchall()
        return [_proposal(r) for r in rows]

    def set_status(
        self,
        conn: sqlite3.Connection,
        company_id: str,
        proposal_id: str,
        status: ProposalStatus,
        decided_by: str | None,
        detail: str | None,
    ) -> bool:
        """Record a decision. True if this company's proposal exists and was updated."""
        done = conn.execute(
            "UPDATE proposals SET status = ?, decided_by = ?, decided_at = ?, detail = ?"
            " WHERE company_id = ? AND proposal_id = ?",
            (status, decided_by, iso_z(self._now()), detail, company_id, proposal_id),
        )
        return done.rowcount == 1

    def insert_effect(
        self, conn: sqlite3.Connection, action_type: str, row: dict[str, Any]
    ) -> None:
        """Write the simulated effect row. `UNIQUE (proposal_id)` raises `IntegrityError` on a second one."""
        if action_type not in _EFFECTS:
            raise ValueError(f"unknown action type: {action_type!r}")
        table, own = _EFFECTS[action_type]
        allowed = {*_EFFECT_COMMON, *own, *_EFFECT_OPTIONAL}
        unknown = set(row) - allowed
        missing = {*_EFFECT_COMMON, *own} - set(row)
        if unknown or missing:
            raise ValueError(
                f"effect row mismatch: unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        values = {"simulated": 1, "created_at": iso_z(self._now()), **row}
        columns = [*_EFFECT_COMMON, *own, *_EFFECT_OPTIONAL]
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(values[c] for c in columns),
        )

    def append_audit(
        self,
        conn: sqlite3.Connection,
        event: AuditEvent,
        *,
        company_id: str,
        actor_id: str,
        proposal_id: str | None = None,
        trace_id: str | None = None,
        action_type: str | None = None,
        target_device_id: str | None = None,
        payload_hash: str | None = None,
        refs: Sequence[str] = (),
        detail: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log (at, company_id, actor_id, event, proposal_id, trace_id, action_type,"
            " target_device_id, payload_hash, refs, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                iso_z(self._now()),
                company_id,
                actor_id,
                event,
                proposal_id,
                trace_id,
                action_type,
                target_device_id,
                payload_hash,
                _json(list(refs)),
                _json(detail or {}),
            ),
        )

    def audit(
        self,
        conn: sqlite3.Connection,
        company_id: str | None,
        *,
        proposal_id: str | None = None,
        target_device_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Audit entries, newest first. `company_id=None` is the operator read across all tenants."""
        clauses: list[str] = []
        args: list[Any] = []
        for column, value in (
            ("company_id", company_id),
            ("proposal_id", proposal_id),
            ("target_device_id", target_device_id),
            ("actor_id", actor_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM audit_log{where} ORDER BY seq DESC LIMIT ?", (*args, _limit(limit))
        ).fetchall()
        return [
            {**dict(r), "refs": json.loads(r["refs"]), "detail": json.loads(r["detail"])}
            for r in rows
        ]

    def effect_counts(self, conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
        """Rows per effect table per company (operator view)."""
        counts: dict[str, dict[str, int]] = {}
        for table, _ in _EFFECTS.values():
            rows = conn.execute(
                f"SELECT company_id, COUNT(*) AS n FROM {table} GROUP BY company_id"
            ).fetchall()
            counts[table] = {r["company_id"]: r["n"] for r in rows}
        return counts


def _statements(script: str) -> Iterator[str]:
    """Split the schema into statements. executescript would COMMIT the open transaction, so it is not used.

    Triggers contain `;` inside BEGIN ... END, so they are kept whole.
    """
    buffer = ""
    for line in script.splitlines():
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
