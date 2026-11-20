"""Graph state. Values are plain JSON-friendly types (dicts, strings) so any checkpointer can store them.

`history` and `focus` persist across the turns of one conversation. Everything else is reset by the
`scope` node at the start of each turn.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from fleet_copilot import policy
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
    records: list[dict[str, Any]]
    draft: dict[str, Any] | None
    prose: dict[str, Any] | None
    envelope: dict[str, Any] | None


def history_entry(question: str, envelope: AnswerEnvelope) -> dict[str, str]:
    return {
        "question": " ".join(question.split())[:_MAX_HISTORY_QUESTION],
        "outcome": envelope.outcome,
        "headline": envelope.headline,
    }


def next_history(
    history: list[dict[str, str]] | None, question: str, envelope: AnswerEnvelope
) -> list[dict[str, str]]:
    return [*(history or []), history_entry(question, envelope)][-policy.MAX_HISTORY_TURNS :]


def next_focus(
    focus: list[dict[str, Any]] | None, envelope: AnswerEnvelope
) -> list[dict[str, Any]]:
    if envelope.outcome not in FOCUS_OUTCOMES:
        return list(focus or [])
    return [entity_list.model_dump(mode="json") for entity_list in envelope.entity_lists]
