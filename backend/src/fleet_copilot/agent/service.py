"""The one entry point the API and the CLI use: one question in, trace events and one envelope out."""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fleet_copilot import policy
from fleet_copilot.agent import memory as memory_module
from fleet_copilot.agent import outcomes
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.graph import RECURSION_LIMIT, build_graph
from fleet_copilot.agent.identity import Identity, thread_id
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.trace import to_event
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent
from fleet_copilot.data.loader import SCHEMA_VERSION, connect, stored_schema_version
from fleet_copilot.repository import dataset_as_of

log = logging.getLogger(__name__)


class InvalidQuestion(ValueError):
    """The question is empty or too long. The API turns this into a 422, not an answer."""


class _StaleDataset(Exception):
    """Internal: the database was built with another schema version."""


class DatasetUnavailable(RuntimeError):
    """The fleet database is missing or unreadable. The API turns this into a 503."""


@dataclass(frozen=True)
class TurnResult:
    envelope: AnswerEnvelope
    trace: list[TraceEvent]


@dataclass(frozen=True)
class TurnRecord:
    """Everything worth keeping about one turn, handed to the recorder when the turn ends."""

    identity: Identity
    conversation_id: str
    question: str  # the cleaned question the admin asked; only the conversation store keeps it
    trace_id: str
    events: tuple[TraceEvent, ...]
    envelope: AnswerEnvelope | None  # None when the reader left before the answer
    elapsed_ms: int


def clean_question(question: str) -> str:
    text = question.strip()
    if not text:
        raise InvalidQuestion("Ask a question about your devices.")
    if len(text) > policy.MAX_QUESTION_CHARS:
        raise InvalidQuestion(f"Questions are limited to {policy.MAX_QUESTION_CHARS} characters.")
    return text


def _new_trace_id() -> str:
    return secrets.token_hex(6)


class AgentService:
    def __init__(
        self,
        *,
        db_path: Path,
        gateway: LLMGateway,
        checkpointer: Any,
        trace_id_factory: Callable[[], str] = _new_trace_id,
        recorder: Callable[[TurnRecord], None] | None = None,
        actions_db_path: Path | None = None,
    ) -> None:
        self._db_path = db_path
        self._actions_db_path = actions_db_path
        self._checkpointer = checkpointer
        self._graph = build_graph(gateway, checkpointer)
        self._new_trace_id = trace_id_factory
        self._recorder = recorder
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        *,
        db_path: Path,
        gateway: LLMGateway,
        checkpoint_path: Path,
        recorder: Callable[[TurnRecord], None] | None = None,
        actions_db_path: Path | None = None,
    ) -> AsyncIterator[AgentService]:
        """A service whose conversations survive restarts (SQLite checkpoints)."""
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            yield cls(
                db_path=db_path,
                gateway=gateway,
                checkpointer=saver,
                recorder=recorder,
                actions_db_path=actions_db_path,
            )

    def _as_of(self):
        conn = connect(self._db_path, read_only=True)
        try:
            if stored_schema_version(conn) != SCHEMA_VERSION:
                raise _StaleDataset
            return dataset_as_of(conn)
        finally:
            conn.close()

    def check_ready(self) -> datetime:
        """The pinned as-of, or `DatasetUnavailable` (fixed text) when the database cannot be read or
        was built by an older loader (it would be missing tables, so every lookup would fail)."""
        try:
            return self._as_of()
        except _StaleDataset:
            raise DatasetUnavailable("The dataset must be rebuilt with make load-data.") from None
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            raise DatasetUnavailable("The dataset is not available.") from None

    @asynccontextmanager
    async def _conversation_lock(self, thread: str) -> AsyncIterator[None]:
        """One turn at a time per conversation, and no lock left behind once nobody uses it."""
        self._lock_users[thread] = self._lock_users.get(thread, 0) + 1
        lock = self._locks.setdefault(thread, asyncio.Lock())
        try:
            async with lock:
                yield
        finally:
            remaining = self._lock_users[thread] - 1
            if remaining:
                self._lock_users[thread] = remaining
            else:
                del self._lock_users[thread]
                self._locks.pop(thread, None)

    async def delete_conversation(self, identity: Identity, conversation_id: str) -> None:
        """Forget one conversation's saved state. The thread key includes company and actor."""
        thread = thread_id(identity, conversation_id)
        async with self._conversation_lock(thread):
            await self._checkpointer.adelete_thread(thread)

    async def notes(self, identity: Identity, conversation_id: str) -> list[dict[str, Any]]:
        """What this conversation was asked to remember, oldest first: `index` (to forget it), `text`, `turn`.

        Read from the conversation's checkpoint, so only the signed-in admin's own conversation answers.
        """
        thread = thread_id(identity, conversation_id)
        async with self._conversation_lock(thread):
            state = await self._graph.aget_state({"configurable": {"thread_id": thread}})
        held = memory_module.load(state.values.get("memory"))
        return [
            {"index": index, "text": note.text, "turn": note.turn}
            for index, note in enumerate(held.notes)
        ]

    async def forget_note(self, identity: Identity, conversation_id: str, index: int) -> bool:
        """Remove one note (by the `index` `notes` lists). False when there is no such note."""
        thread = thread_id(identity, conversation_id)
        config: dict[str, Any] = {"configurable": {"thread_id": thread}}
        async with self._conversation_lock(thread):
            state = await self._graph.aget_state(config)
            held = memory_module.load(state.values.get("memory"))
            try:
                kept = memory_module.forget_note(held, index)
            except IndexError:
                return False
            await self._graph.aupdate_state(
                config, {"memory": memory_module.dump(kept)}, as_node="respond"
            )
        return True

    def _record(
        self,
        identity: Identity,
        conversation_id: str,
        question: str,
        trace_id: str,
        events: list[TraceEvent],
        envelope: AnswerEnvelope | None,
        started: float,
    ) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder(
                TurnRecord(
                    identity=identity,
                    conversation_id=conversation_id,
                    question=question,
                    trace_id=trace_id,
                    events=tuple(events),
                    envelope=envelope,
                    elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                )
            )
        except Exception as exc:  # observability must never break an answer
            log.warning("could not record a turn: %s", type(exc).__name__)

    async def stream_turn(
        self, identity: Identity, conversation_id: str, question: str
    ) -> AsyncIterator[TraceEvent | AnswerEnvelope]:
        """Yield trace events as they happen, then the final envelope (always exactly one)."""
        text = clean_question(question)
        thread = thread_id(identity, conversation_id)
        trace_id = self._new_trace_id()
        as_of = self.check_ready()
        ctx = AgentContext(
            db_path=self._db_path,
            company_id=identity.company_id,
            actor_id=identity.actor_id,
            as_of=as_of,
            trace_id=trace_id,
            company_name=identity.company_name,
            actions_db_path=self._actions_db_path,
        )
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread},
            "recursion_limit": RECURSION_LIMIT,
        }
        events: list[TraceEvent] = []
        envelope: AnswerEnvelope | None = None
        started = time.perf_counter()
        seq = 0
        try:
            async with self._conversation_lock(thread):
                stream = self._graph.astream(
                    {"question": text}, config, context=ctx, stream_mode="custom"
                )
                # One deadline for the whole turn, so a hung gateway cannot hold the lock and block every
                # later turn of this conversation. Only the awaits are inside the timeout, never a `yield`:
                # a slow reader of the stream must not be cancelled by it.
                deadline = asyncio.get_running_loop().time() + policy.TURN_TIMEOUT_S
                try:
                    while True:
                        try:
                            async with asyncio.timeout_at(deadline):
                                chunk = await anext(stream)
                        except StopAsyncIteration:
                            break
                        if isinstance(chunk, dict) and chunk.get("type") == "trace":
                            event = to_event(chunk, trace_id=trace_id, seq=seq)
                            seq += 1
                            events.append(event)
                            yield event
                    async with asyncio.timeout_at(deadline):
                        state = await self._graph.aget_state(config)
                    raw = state.values.get("envelope")
                    candidate = AnswerEnvelope.model_validate(raw) if raw else None
                    if candidate is None or candidate.trace_id != trace_id:
                        raise RuntimeError("the turn finished without an answer")
                    envelope = candidate  # only this turn's envelope ever reaches the recorder
                except Exception as exc:  # fail closed: a crash is a tool_error, never a guess
                    event = to_event(
                        {
                            "kind": "error",
                            "summary": "The turn failed",
                            "data": {"error": type(exc).__name__},
                        },
                        trace_id=trace_id,
                        seq=seq,
                    )
                    seq += 1
                    events.append(event)
                    yield event
                    envelope = outcomes.tool_failure(
                        company_id=identity.company_id, as_of=as_of, trace_id=trace_id
                    )
                finally:
                    # Also when the caller closes this stream mid-turn: stop the graph run it started.
                    await stream.aclose()
            yield envelope
        finally:
            self._record(identity, conversation_id, text, trace_id, events, envelope, started)

    async def run_turn(self, identity: Identity, conversation_id: str, question: str) -> TurnResult:
        events: list[TraceEvent] = []
        final: AnswerEnvelope | None = None
        async for item in self.stream_turn(identity, conversation_id, question):
            if isinstance(item, TraceEvent):
                events.append(item)
            else:
                final = item
        assert final is not None
        return TurnResult(final, events)
