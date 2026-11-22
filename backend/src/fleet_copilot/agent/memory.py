"""What one conversation remembers: the turns so far (code-written facts) and the user's own notes.

Everything here is a pure function over `ConversationMemory`; the graph keeps the value in its checkpointed
state, so it is per conversation and survives a reopened conversation. Two kinds of content live side by
side and are never mixed:

  * turns: written by code from the final envelope (headline, summary, table cells, insight findings,
    proposals and their status). These are facts the system produced.
  * notes: what the user said about their fleet ("I only care about laptops"). They are the user's
    statements, never telemetry: they are labelled "You said" when rendered, never cited as evidence, never
    part of `memory_facts`, and never used as a tool argument.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fleet_copilot import policy
from fleet_copilot.agent.verify import bad_chars, has_markup, stated_month_days
from fleet_copilot.contracts import AnswerEnvelope

MAX_FACT_ROWS = 10  # devices kept per turn
MAX_FACT_CELLS = 4  # cells (or insight values) kept per device
MAX_FACT_REFS = 6
MAX_PROPOSALS_PER_TURN = 4
MAX_REPLY_KEPT = 400  # characters of a turn's reply kept in memory
GONE = "unknown"  # status of a remembered proposal the action service no longer knows
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class KeyFact(_Model):
    """One device an answer showed, with its main values, as the answer displayed them."""

    device_id: str
    label: str
    facts: dict[str, str] = Field(default_factory=dict)
    refs: list[str] = Field(default_factory=list)


class RememberedProposal(_Model):
    proposal_id: str
    action_type: str
    device_id: str
    label: str
    status: str


class TurnMemory(_Model):
    turn: int
    question: str
    outcome: str
    reason_code: str | None = None
    headline: str = ""
    summary: str = ""
    # What the assistant said under the headline (the checked reply), cut to `MAX_REPLY_KEPT` characters.
    reply: str = ""
    key_facts: list[KeyFact] = Field(default_factory=list)
    proposals: list[RememberedProposal] = Field(default_factory=list)
    trace_id: str = ""


class Note(_Model):
    """A statement the user made. `turn` is the turn it was made in."""

    text: str
    turn: int


class ConversationMemory(_Model):
    turns: list[TurnMemory] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)


def load(value: object) -> ConversationMemory:
    """The memory held in graph state (a plain dict, or nothing yet)."""
    if isinstance(value, ConversationMemory):
        return value
    if isinstance(value, dict):
        return ConversationMemory.model_validate(value)
    return ConversationMemory()


def dump(memory: ConversationMemory) -> dict[str, Any]:
    return memory.model_dump(mode="json")


def next_turn(memory: ConversationMemory) -> int:
    return memory.turns[-1].turn + 1 if memory.turns else 1


def _one_line(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


# ---- writing --------------------------------------------------------------------------------------


def _row_facts(envelope: AnswerEnvelope) -> list[KeyFact]:
    table = envelope.table
    if table is None:
        return []
    shown = [column for column in table.columns if column.kind != "device"]
    facts: list[KeyFact] = []
    for row in table.rows[:MAX_FACT_ROWS]:
        cells = {
            column.label: row.cells[column.key].display
            for column in shown
            if column.key in row.cells and row.cells[column.key].display
        }
        facts.append(
            KeyFact(
                device_id=row.device_id,
                label=row.label,
                facts=dict(list(cells.items())[:MAX_FACT_CELLS]),
                refs=row.refs[:MAX_FACT_REFS],
            )
        )
    return facts


def _insight_facts(envelope: AnswerEnvelope) -> list[KeyFact]:
    facts: list[KeyFact] = []
    for insight in envelope.insights[:MAX_FACT_ROWS]:
        values = {"Finding": insight.finding}
        for pair in insight.evidence_kv[:2]:
            values[pair.label] = pair.value
        facts.append(
            KeyFact(
                device_id=insight.device_id,
                label=insight.label,
                facts=values,
                refs=insight.refs[:MAX_FACT_REFS],
            )
        )
    return facts


def key_facts_of(envelope: AnswerEnvelope) -> list[KeyFact]:
    """The devices an answer showed: its table rows, or its insights when it has no table."""
    return _row_facts(envelope) or _insight_facts(envelope)


def remember_turn(
    memory: ConversationMemory, envelope: AnswerEnvelope, question: str, trace_id: str
) -> ConversationMemory:
    """The memory with this turn appended (the oldest turns fall away beyond `MEMORY_MAX_TURNS`)."""
    proposals = []
    if envelope.proposal is not None:
        card = envelope.proposal
        proposals.append(
            RememberedProposal(
                proposal_id=card.proposal_id,
                action_type=card.action_type,
                device_id=card.target_device_id,
                label=card.target_label,
                status=card.status,
            )
        )
    entry = TurnMemory(
        turn=next_turn(memory),
        question=_one_line(question, policy.MEMORY_NOTE_CHARS),
        outcome=envelope.outcome,
        reason_code=envelope.reason_code,
        headline=envelope.headline,
        summary=envelope.summary,
        reply=_one_line(envelope.reply, MAX_REPLY_KEPT),
        key_facts=key_facts_of(envelope),
        proposals=proposals[:MAX_PROPOSALS_PER_TURN],
        trace_id=trace_id,
    )
    turns = [*memory.turns, entry][-policy.MEMORY_MAX_TURNS :]
    return memory.model_copy(update={"turns": turns})


def clean_note(text: object) -> str | None:
    """A note as it may be stored, or None when it must be dropped.

    A note is plain text: whitespace is collapsed, an empty one or one with a control, invisible or markup
    character is dropped (never repaired), and a long one is cut to `MEMORY_NOTE_CHARS`.
    """
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split())
    if not cleaned or bad_chars(cleaned) or has_markup(cleaned):
        return None
    return cleaned[: policy.MEMORY_NOTE_CHARS].rstrip()


def add_note(
    memory: ConversationMemory, text: object, *, turn: int | None = None
) -> ConversationMemory:
    """The memory with this user statement added, or unchanged when the note is unusable or already held."""
    cleaned = clean_note(text)
    if cleaned is None or any(note.text == cleaned for note in memory.notes):
        return memory
    note = Note(text=cleaned, turn=turn if turn is not None else next_turn(memory))
    return memory.model_copy(update={"notes": [*memory.notes, note][-policy.MEMORY_MAX_NOTES :]})


def forget_last_note(memory: ConversationMemory) -> ConversationMemory:
    return memory.model_copy(update={"notes": memory.notes[:-1]})


def forget_note(memory: ConversationMemory, index: int) -> ConversationMemory:
    """The memory without note `index` (0-based, in the order the notes are listed). IndexError if none."""
    if not 0 <= index < len(memory.notes):
        raise IndexError(index)
    notes = [note for position, note in enumerate(memory.notes) if position != index]
    return memory.model_copy(update={"notes": notes})


def refresh_proposals(
    memory: ConversationMemory, lookup: Callable[[str], str | None]
) -> ConversationMemory:
    """The memory with each remembered proposal's status read again. `lookup` gives None for one that is gone."""
    turns = []
    for turn in memory.turns:
        if not turn.proposals:
            turns.append(turn)
            continue
        refreshed = []
        for proposal in turn.proposals:
            status = lookup(proposal.proposal_id)
            refreshed.append(proposal.model_copy(update={"status": status or GONE}))
        turns.append(turn.model_copy(update={"proposals": refreshed}))
    return memory.model_copy(update={"turns": turns})


# ---- reading --------------------------------------------------------------------------------------


def _facts_line(fact: KeyFact) -> str:
    values = "; ".join(f"{label}: {value}" for label, value in fact.facts.items())
    return f"{fact.label} ({values})" if values else fact.label


def _detail(turn: TurnMemory) -> list[str]:
    lines = [f"Turn {turn.turn} | {turn.outcome} | asked: {turn.question}"]
    if turn.headline:
        lines.append(f"  Answer: {turn.headline}")
    if turn.summary:
        lines.append(f"  Summary: {turn.summary}")
    if turn.reply:
        lines.append(f"  Assistant reply: {turn.reply}")
    if turn.key_facts:
        lines.append("  Devices: " + ", ".join(_facts_line(fact) for fact in turn.key_facts))
    for proposal in turn.proposals:
        lines.append(
            f"  Proposal {proposal.proposal_id}: {proposal.action_type} for {proposal.label}"
            f" ({proposal.status})"
        )
    return lines


def _one_liner(turn: TurnMemory) -> str:
    return (
        f"Turn {turn.turn} | {turn.outcome} | asked: {_one_line(turn.question, 80)}"
        f" | answer: {_one_line(turn.headline, 120)}"
    )


def render(
    memory: ConversationMemory,
    *,
    detail_turns: int = 8,
    max_chars: int = policy.MEMORY_MAX_CHARS,
) -> str:
    """The memory as text for a prompt: notes first ("You said"), then older turns one line each, then the
    newest turns in detail. Deterministic, and never longer than `max_chars`.

    When it does not fit, the oldest one-line turns go first, then the oldest detailed turns become one
    line, and the notes and the newest turns stay. Callers fence the result as data.
    """
    notes = [f"You said: {note.text} (turn {note.turn})" for note in memory.notes]
    turns = memory.turns

    def build(detailed: int, skipped: int) -> str:
        older = turns[skipped : len(turns) - detailed]
        recent = turns[len(turns) - detailed :]
        lines = list(notes)
        if skipped:
            lines.append(f"({skipped} earlier turns are not shown)")
        lines.extend(_one_liner(turn) for turn in older)
        for turn in recent:
            lines.extend(_detail(turn))
        return "\n".join(lines)

    for detailed in range(min(detail_turns, len(turns)), -1, -1):
        for skipped in range(len(turns) - detailed + 1):
            text = build(detailed, skipped)
            if len(text) <= max_chars:
                return text or "(none)"
    return build(0, len(turns))[:max_chars] or "(none)"


def memory_facts(memory: ConversationMemory) -> dict[str, frozenset[str]]:
    """What the remembered turns state, for checking later prose against: `numbers`, `labels` (hostnames),
    `ids` (device and proposal ids), `refs` and `dates` (calendar days as "jun 12"). Notes are the user's
    words, not facts, so they add nothing."""
    numbers: set[str] = set()
    labels: set[str] = set()
    ids: set[str] = set()
    refs: set[str] = set()
    dates: set[str] = set()
    for turn in memory.turns:
        texts = [turn.headline, turn.summary, turn.reply]
        for fact in turn.key_facts:
            labels.add(fact.label)
            ids.add(fact.device_id)
            refs.update(fact.refs)
            texts.extend(fact.facts.values())
        for proposal in turn.proposals:
            labels.add(proposal.label)
            ids.update((proposal.proposal_id, proposal.device_id))
        for text in texts:
            numbers.update(_NUMBER.findall(text))
            dates.update(f"{month} {day}" for month, day in stated_month_days(text))
    return {
        "numbers": frozenset(numbers),
        "labels": frozenset(labels),
        "ids": frozenset(ids),
        "refs": frozenset(refs),
        "dates": frozenset(dates),
    }
