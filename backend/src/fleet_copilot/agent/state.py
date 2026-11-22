"""Graph state. Values are plain JSON-friendly types (dicts, strings) so any checkpointer can store them.

`history` and `focus` persist across the turns of one conversation. Everything else is reset by the
`scope` node at the start of each turn.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from fleet_copilot import policy
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.contracts import AnswerEnvelope

# Outcomes that replace the list a follow-up can point at. Clarifications and refusals keep it.
FOCUS_OUTCOMES = frozenset({"answer", "no_match", "insufficient_data"})
_MAX_HISTORY_QUESTION = 200


class TurnState(TypedDict, total=False):
    # persistent
    history: list[dict[str, str]]
    focus: list[dict[str, Any]]
    # per turn
    question: str
    turn_messages: Annotated[list[AnyMessage], add_messages]
    plan: dict[str, Any] | None
    focus_ids: list[str] | None
    pending_calls: bool
    rounds: int
    limit_hit: bool
    # Set once when the planned tools gathered nothing and every tool was offered for the rest of the turn.
    widened: bool
    # Set when part of what the model asked for never ran: calls dropped over the per-round cap, or
    # (`unfixed_calls`, tool names only) calls the tool rejected and the model never repeated correctly.
    incomplete: bool
    unfixed_calls: list[str]
    records: list[dict[str, Any]]
    draft: dict[str, Any] | None
    # Suggested actions (and the evidence they cite) for a list answer. Held apart from the draft so the
    # model never sees them: they are merged into the final envelope after the prose is verified.
    extras: dict[str, Any] | None
    # What this turn answered, hashed: a later turn with the same value repeats it.
    fingerprint: str | None
    prose: dict[str, Any] | None
    envelope: dict[str, Any] | None


def turn_fingerprint(plan_intent: str, records: Sequence[ToolRecord], as_of: datetime) -> str:
    """A hash of what a turn answered: the plan's intent, each tool call, the devices found and the as-of.

    Two turns of one conversation with the same fingerprint gave the same answer. The question's wording
    is left out on purpose: a rephrased question that makes the same calls is a repeat too.
    """
    calls = sorted(
        (record.tool, json.dumps(record.args, sort_keys=True, default=str)) for record in records
    )
    devices = sorted({device_id for record in records for device_id in record.device_ids})
    payload = json.dumps(
        [plan_intent, calls, devices, as_of.isoformat()], sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def earlier_turn(history: Sequence[dict[str, str]] | None, fingerprint: str) -> str | None:
    """The trace id of the turn that first gave this answer, within the remembered turns."""
    for entry in reversed(history or []):
        if entry.get("fingerprint") == fingerprint and entry.get("trace_id"):
            return entry["trace_id"]
    return None


def history_entry(
    question: str, envelope: AnswerEnvelope, fingerprint: str | None = None
) -> dict[str, str]:
    """What the planner sees of a turn is `question`, `outcome` and `headline`. The fingerprint and trace
    id are extra keys for repeat detection that no prompt renders."""
    entry = {
        "question": " ".join(question.split())[:_MAX_HISTORY_QUESTION],
        "outcome": envelope.outcome,
        "headline": envelope.headline,
    }
    if fingerprint:
        entry["fingerprint"] = fingerprint
        # A repeat points at the answer it repeated, so a third ask still finds the original.
        entry["trace_id"] = envelope.repeat_of or envelope.trace_id
    return entry


def next_history(
    history: list[dict[str, str]] | None,
    question: str,
    envelope: AnswerEnvelope,
    fingerprint: str | None = None,
) -> list[dict[str, str]]:
    entry = history_entry(question, envelope, fingerprint)
    return [*(history or []), entry][-policy.MAX_HISTORY_TURNS :]


def next_focus(
    focus: list[dict[str, Any]] | None, envelope: AnswerEnvelope
) -> list[dict[str, Any]]:
    if envelope.outcome not in FOCUS_OUTCOMES:
        return list(focus or [])
    return [entity_list.model_dump(mode="json") for entity_list in envelope.entity_lists]
