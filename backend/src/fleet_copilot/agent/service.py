"""The one entry point the API and the CLI use: one question in, trace events and one envelope out."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fleet_copilot import policy
from fleet_copilot.agent import outcomes
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.graph import RECURSION_LIMIT, build_graph
from fleet_copilot.agent.identity import Identity, thread_id
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.trace import to_event
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent
from fleet_copilot.data.loader import connect
from fleet_copilot.repository import dataset_as_of


class InvalidQuestion(ValueError):
    """The question is empty or too long. The API turns this into a 422, not an answer."""


@dataclass(frozen=True)
class TurnResult:
    envelope: AnswerEnvelope
    trace: list[TraceEvent]


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
    ) -> None:
        self._db_path = db_path
        self._graph = build_graph(gateway, checkpointer)
        self._new_trace_id = trace_id_factory
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    @asynccontextmanager
    async def open(
        cls, *, db_path: Path, gateway: LLMGateway, checkpoint_path: Path
    ) -> AsyncIterator[AgentService]:
        """A service whose conversations survive restarts (SQLite checkpoints)."""
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            yield cls(db_path=db_path, gateway=gateway, checkpointer=saver)

    def _as_of(self):
        conn = connect(self._db_path, read_only=True)
        try:
            return dataset_as_of(conn)
        finally:
            conn.close()

    async def stream_turn(
        self, identity: Identity, conversation_id: str, question: str
    ) -> AsyncIterator[TraceEvent | AnswerEnvelope]:
        """Yield trace events as they happen, then the final envelope (always exactly one)."""
        text = clean_question(question)
        thread = thread_id(identity, conversation_id)
        trace_id = self._new_trace_id()
        as_of = self._as_of()
        ctx = AgentContext(
            db_path=self._db_path,
            company_id=identity.company_id,
            actor_id=identity.actor_id,
            as_of=as_of,
            trace_id=trace_id,
        )
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread},
            "recursion_limit": RECURSION_LIMIT,
        }
        lock = self._locks.setdefault(thread, asyncio.Lock())
        seq = 0
        async with lock:  # one turn at a time per conversation
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
                        yield to_event(chunk, trace_id=trace_id, seq=seq)
                        seq += 1
                async with asyncio.timeout_at(deadline):
                    state = await self._graph.aget_state(config)
                raw = state.values.get("envelope")
                envelope = AnswerEnvelope.model_validate(raw) if raw else None
                if envelope is None or envelope.trace_id != trace_id:
                    raise RuntimeError("the turn finished without an answer")
            except Exception as exc:  # fail closed: a crash is a tool_error, never a guess
                yield to_event(
                    {
                        "kind": "error",
                        "summary": "The turn failed",
                        "data": {"error": type(exc).__name__},
                    },
                    trace_id=trace_id,
                    seq=seq,
                )
                envelope = outcomes.tool_failure(
                    company_id=identity.company_id, as_of=as_of, trace_id=trace_id
                )
            finally:
                # Also when the caller closes this stream mid-turn: stop the graph run it started.
                await stream.aclose()
        yield envelope

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
