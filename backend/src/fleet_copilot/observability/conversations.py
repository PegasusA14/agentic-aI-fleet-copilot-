"""Server-side chat history: one row per conversation and one per answered turn, for the sidebar and reopen.

Unlike `TraceStore` (which keeps no question text), this store keeps what the admin typed and the full answer
envelope, because the admin must be able to read their own conversation again. Every read and delete is scoped
by `(company_id, actor_id)` from the signed session, so another tenant or another admin sees what an unknown
id gets: nothing. A turn with no envelope (the reader left before the answer) is not stored.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_copilot.agent.service import TurnRecord
from fleet_copilot.timeutil import iso_z

TITLE_MAX_CHARS = 60
MAX_LIST = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    company_id      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    title           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (company_id, actor_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS turns (
    company_id      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    question        TEXT NOT NULL,
    envelope_json   TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (company_id, actor_id, conversation_id, seq)
);
CREATE INDEX IF NOT EXISTS conversations_recent
    ON conversations (company_id, actor_id, updated_at);
"""


def make_title(question: str) -> str:
    """The first question on one line, at most 60 characters, cut at a word boundary."""
    text = " ".join(question.split())
    if len(text) <= TITLE_MAX_CHARS:
        return text
    head = text[:TITLE_MAX_CHARS]
    if text[TITLE_MAX_CHARS] == " ":  # the 60th character ends a word: keep it whole
        return head
    cut = head.rfind(" ")
    return head[:cut] if cut > 0 else head


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ConversationStore:
    def __init__(self, path: Path, *, now: Callable[[], datetime] = _utc_now) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._now = now
        with closing(sqlite3.connect(path, timeout=10)) as conn:
            conn.executescript(SCHEMA)  # runs its own commits, so it stays outside `_tx`

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: we open the transaction ourselves, so a write can take the lock up front
        with closing(sqlite3.connect(self._path, timeout=10, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def record(self, turn: TurnRecord) -> None:
        if turn.envelope is None:
            return
        who = (turn.identity.company_id, turn.identity.actor_id, turn.conversation_id)
        stamp = iso_z(self._now())
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM turns"
                " WHERE company_id = ? AND actor_id = ? AND conversation_id = ?",
                who,
            ).fetchone()
            conn.execute(
                "INSERT INTO conversations (company_id, actor_id, conversation_id, title, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (company_id, actor_id, conversation_id) DO UPDATE SET"
                " updated_at = excluded.updated_at",
                (*who, make_title(turn.question), stamp, stamp),
            )
            conn.execute(
                "INSERT INTO turns (company_id, actor_id, conversation_id, seq, question, envelope_json,"
                " trace_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *who,
                    row["next"],
                    turn.question,
                    turn.envelope.model_dump_json(),
                    turn.trace_id,
                    stamp,
                ),
            )

    def list(self, company_id: str, actor_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """This admin's conversations, most recently active first."""
        if not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        limit = max(0, min(limit, MAX_LIST))
        with self._tx() as conn:
            # Timestamps have one-second resolution, so ties are broken by the newest turn's row id.
            rows = conn.execute(
                "SELECT conversation_id, title, updated_at, turns FROM ("
                " SELECT c.conversation_id, c.title, c.updated_at, COUNT(t.seq) AS turns,"
                " COALESCE(MAX(t.rowid), 0) AS latest"
                " FROM conversations c LEFT JOIN turns t ON t.company_id = c.company_id"
                " AND t.actor_id = c.actor_id AND t.conversation_id = c.conversation_id"
                " WHERE c.company_id = ? AND c.actor_id = ?"
                " GROUP BY c.conversation_id, c.title, c.updated_at)"
                " ORDER BY updated_at DESC, latest DESC LIMIT ?",
                (company_id, actor_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, company_id: str, actor_id: str, conversation_id: str) -> dict[str, Any] | None:
        """One conversation with its turns, or None when it is unknown or not this admin's."""
        who = (company_id, actor_id, conversation_id)
        with self._tx() as conn:
            head = conn.execute(
                "SELECT conversation_id, title FROM conversations"
                " WHERE company_id = ? AND actor_id = ? AND conversation_id = ?",
                who,
            ).fetchone()
            if head is None:
                return None
            rows = conn.execute(
                "SELECT seq, question, envelope_json, trace_id, created_at FROM turns"
                " WHERE company_id = ? AND actor_id = ? AND conversation_id = ? ORDER BY seq",
                who,
            ).fetchall()
        return {
            "conversation_id": head["conversation_id"],
            "title": head["title"],
            "turns": [
                {
                    "seq": r["seq"],
                    "question": r["question"],
                    "envelope": json.loads(r["envelope_json"]),
                    "trace_id": r["trace_id"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }

    def delete(self, company_id: str, actor_id: str, conversation_id: str) -> bool:
        """Forget one conversation and its turns. True when something was removed."""
        who = (company_id, actor_id, conversation_id)
        where = "WHERE company_id = ? AND actor_id = ? AND conversation_id = ?"
        with self._tx() as conn:
            conn.execute(f"DELETE FROM turns {where}", who)
            removed = conn.execute(f"DELETE FROM conversations {where}", who).rowcount
        return removed > 0
