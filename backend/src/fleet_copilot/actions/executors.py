"""The simulated effects: one function per action type, each writing its own table row.

Nothing leaves the process. An executor writes only what the approver saw: the card's rationale for a ticket,
the code-derived parameters for the rest, and for a notification the exact message that is part of the payload
hash. Executors run inside the approval transaction; an exception rolls their row back.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fleet_copilot.actions.eligibility import Eligible
from fleet_copilot.actions.store import ActionStore, ProposalRow

Executor = Callable[[ActionStore, sqlite3.Connection, ProposalRow, Eligible], dict[str, Any]]


def _write(
    store: ActionStore, conn: sqlite3.Connection, row: ProposalRow, fields: dict[str, Any]
) -> dict[str, Any]:
    effect = {
        "proposal_id": row.proposal_id,
        "company_id": row.company_id,
        "device_id": row.target_device_id,
        **fields,
    }
    store.insert_effect(conn, row.action_type, effect)
    effect_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"effect_id": effect_id, "simulated": True, **fields}


def remediation_ticket(
    store: ActionStore, conn: sqlite3.Connection, row: ProposalRow, eligible: Eligible
) -> dict[str, Any]:
    fields = {"check_id": row.params["check_id"], "note": row.card["rationale"]}
    return _write(store, conn, row, fields)


def replacement_flag(
    store: ActionStore, conn: sqlite3.Connection, row: ProposalRow, eligible: Eligible
) -> dict[str, Any]:
    return _write(store, conn, row, {"reason": row.params["reason"]})


def upgrade_order(
    store: ActionStore, conn: sqlite3.Connection, row: ProposalRow, eligible: Eligible
) -> dict[str, Any]:
    fields = {
        "component": row.params["component"],
        "current_spec": row.params["current"],
        "target_spec": row.params["target"],
    }
    return _write(store, conn, row, fields)


def employee_notification(
    store: ActionStore, conn: sqlite3.Connection, row: ProposalRow, eligible: Eligible
) -> dict[str, Any]:
    if eligible.employee_id is None:
        raise ValueError("no employee is assigned to the device")
    fields = {
        "employee_id": eligible.employee_id,
        "topic": row.params["topic"],
        "message": row.params["message"],
    }
    return _write(store, conn, row, fields)


EXECUTORS: dict[str, Executor] = {
    "remediation_ticket": remediation_ticket,
    "replacement_flag": replacement_flag,
    "upgrade_order": upgrade_order,
    "employee_notification": employee_notification,
}


def execute(
    store: ActionStore, conn: sqlite3.Connection, row: ProposalRow, eligible: Eligible
) -> dict[str, Any]:
    return EXECUTORS[row.action_type](store, conn, row, eligible)
