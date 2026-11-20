"""Persisted traces for the operator console and the per-answer Trace tab.

Event data passes through `redact_data` before it is stored. That bounds the SHAPE of what is kept (key
names, scalar types, length, single line, depth); it does not recognise identifiers. The CONTENT guarantee,
that no question, model prose, hostname, device id or employee id is persisted, comes from the emitters
(`agent/graph.py` and `agent/trace.py`), which send counts and fixed codes. A test drives the risky paths and
scans every stored row for every identifier in the dataset, so a leaking emitter fails there.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_copilot.agent.service import TurnRecord
from fleet_copilot.contracts import TraceEvent
from fleet_copilot.timeutil import iso_z

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    trace_id        TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    elapsed_ms      INTEGER NOT NULL,
    outcome         TEXT,
    reason_code     TEXT,
    model_mode      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trace_events (
    trace_id   TEXT NOT NULL REFERENCES turns (trace_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    tool       TEXT,
    summary    TEXT NOT NULL,
    data       TEXT NOT NULL,
    latency_ms INTEGER,
    PRIMARY KEY (trace_id, seq)
);
CREATE INDEX IF NOT EXISTS turns_started ON turns (started_at);
"""

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_MAX_TEXT = 80
_MAX_ITEMS = 20


def _scalar(value: Any) -> Any:
    """A value that is safe to keep: bool, None, small number, or a short single-line string."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 10**9 else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT and "\n" not in value else "redacted"
    return None


def redact_data(data: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
    """Bound the shape of trace data: snake_case keys, scalars, short single-line strings, one level of nesting.

    This is a shape filter, not a content filter: a short string that happens to be an identifier is kept.
    Keeping identifiers out is the emitters' job (see the module docstring).
    """
    kept: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not _KEY.match(key):
            continue
        if isinstance(value, dict):
            if _depth == 0:
                kept[key] = redact_data(value, 1)
        elif isinstance(value, list | tuple):
            items = [_scalar(item) for item in list(value)[:_MAX_ITEMS]]
            kept[key] = items
        else:
            safe = _scalar(value)
            if safe is not None or value is None:
                kept[key] = safe
    return kept


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TraceStore:
    def __init__(
        self, path: Path, *, model_mode: str, now: Callable[[], datetime] = _utc_now
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._model_mode = model_mode
        self._now = now
        with self._tx() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:  # commit or roll back
                yield conn

    def record(self, turn: TurnRecord) -> None:
        envelope = turn.envelope
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO turns (trace_id, company_id, actor_id, conversation_id, started_at,"
                " elapsed_ms, outcome, reason_code, model_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn.trace_id,
                    turn.identity.company_id,
                    turn.identity.actor_id,
                    turn.conversation_id,
                    iso_z(self._now()),
                    turn.elapsed_ms,
                    envelope.outcome if envelope else None,
                    envelope.reason_code if envelope else None,
                    self._model_mode,
                ),
            )
            conn.execute("DELETE FROM trace_events WHERE trace_id = ?", (turn.trace_id,))
            conn.executemany(
                "INSERT INTO trace_events (trace_id, seq, kind, tool, summary, data, latency_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.trace_id,
                        e.seq,
                        e.kind,
                        e.tool,
                        e.summary,
                        json.dumps(redact_data(e.data)),
                        e.latency_ms,
                    )
                    for e in turn.events
                ],
            )

    @staticmethod
    def _events(rows: list[sqlite3.Row]) -> list[TraceEvent]:
        return [
            TraceEvent(
                trace_id=r["trace_id"],
                seq=r["seq"],
                kind=r["kind"],
                tool=r["tool"],
                summary=r["summary"],
                data=json.loads(r["data"]),
                latency_ms=r["latency_ms"],
            )
            for r in rows
        ]

    def get_trace(self, company_id: str, trace_id: str) -> list[TraceEvent] | None:
        """One turn's events, only if the turn belongs to this company."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT e.* FROM trace_events e JOIN turns t ON t.trace_id = e.trace_id"
                " WHERE t.company_id = ? AND e.trace_id = ? ORDER BY e.seq",
                (company_id, trace_id),
            ).fetchall()
        return self._events(rows) or None

    def get_trace_any(self, trace_id: str) -> list[TraceEvent] | None:
        """Operator read: any tenant's turn."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM trace_events WHERE trace_id = ? ORDER BY seq", (trace_id,)
            ).fetchall()
        return self._events(rows) or None

    def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        limit = max(0, min(limit, 500))
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT trace_id, company_id, actor_id, conversation_id, started_at, elapsed_ms, outcome,"
                " reason_code, model_mode FROM turns ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
