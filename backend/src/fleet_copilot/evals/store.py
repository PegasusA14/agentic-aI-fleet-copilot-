"""Stored evaluation runs (`evals.db`): one row per run and one per case instance.

The operator console reads these; nothing here holds a question's answer text, only the questions the
scenario asked, the checks with their readable details, the trace ids (the traces themselves live in the
trace store) and the effect ledger of action cases.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    started_at     TEXT NOT NULL,
    model_mode     TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    as_of          TEXT NOT NULL,
    policy_json    TEXT NOT NULL,
    totals_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    run_id         TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    instance_id    TEXT NOT NULL,
    suite          TEXT NOT NULL,
    case_id        TEXT NOT NULL,
    phrasing_index INTEGER NOT NULL,
    user           TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    passed         INTEGER NOT NULL,
    checks_json    TEXT NOT NULL,
    trace_ids_json TEXT NOT NULL,
    effects_json   TEXT NOT NULL,
    PRIMARY KEY (run_id, instance_id)
);
CREATE INDEX IF NOT EXISTS runs_started ON runs (started_at);
"""

_MAX_LIMIT = 500


def _run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "started_at": row["started_at"],
        "model_mode": row["model_mode"],
        "dataset_sha256": row["dataset_sha256"],
        "as_of": row["as_of"],
        "policy": json.loads(row["policy_json"]),
        "totals": json.loads(row["totals_json"]),
    }


def _case(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "instance_id": row["instance_id"],
        "suite": row["suite"],
        "case_id": row["case_id"],
        "phrasing_index": row["phrasing_index"],
        "user": row["user"],
        "questions": json.loads(row["questions_json"]),
        "passed": bool(row["passed"]),
        "checks": json.loads(row["checks_json"]),
        "trace_ids": json.loads(row["trace_ids_json"]),
        "effects": json.loads(row["effects_json"]),
    }


class EvalStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._tx() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:  # commit or roll back
                yield conn

    def start_run(
        self,
        *,
        run_id: str,
        started_at: str,
        model_mode: str,
        dataset_sha256: str,
        as_of: str,
        policy: dict[str, Any],
    ) -> None:
        """Open a run (its totals stay empty until `finish_run`, so a crashed run is recognisable)."""
        with self._tx() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.execute(
                "INSERT INTO runs (run_id, started_at, model_mode, dataset_sha256, as_of, policy_json,"
                " totals_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, started_at, model_mode, dataset_sha256, as_of, json.dumps(policy), "{}"),
            )

    def add_case(
        self,
        run_id: str,
        *,
        instance_id: str,
        suite: str,
        case_id: str,
        phrasing_index: int,
        user: str,
        questions: list[str],
        passed: bool,
        checks: list[dict[str, Any]],
        trace_ids: list[str],
        effects: list[dict[str, Any]],
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cases (run_id, instance_id, suite, case_id, phrasing_index, user,"
                " questions_json, passed, checks_json, trace_ids_json, effects_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    instance_id,
                    suite,
                    case_id,
                    phrasing_index,
                    user,
                    json.dumps(questions),
                    1 if passed else 0,
                    json.dumps(checks),
                    json.dumps(trace_ids),
                    json.dumps(effects),
                ),
            )

    def finish_run(self, run_id: str, totals: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET totals_json = ? WHERE run_id = ?", (json.dumps(totals), run_id)
            )

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Newest runs first, with their totals and no case rows."""
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("limit must be an integer")
        limit = max(0, min(limit, _MAX_LIMIT))
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_run(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """One run with all its cases (in the order they ran), or None."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            cases = conn.execute(
                "SELECT * FROM cases WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
        return {**_run(row), "cases": [_case(c) for c in cases]}
