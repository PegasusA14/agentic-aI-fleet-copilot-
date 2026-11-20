"""The one entry point the API and the CLI use: one question in, trace events and one envelope out."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.graph import RECURSION_LIMIT, build_graph
from fleet_copilot.agent.identity import Identity, thread_id
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.trace import to_event
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent
from fleet_copilot.data.loader import connect
from fleet_copilot.repository import dataset_as_of


@dataclass(frozen=True)
class TurnResult:
    envelope: AnswerEnvelope
    trace: list[TraceEvent]


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

    def _as_of(self):
        conn = connect(self._db_path, read_only=True)
        try:
            return dataset_as_of(conn)
        finally:
            conn.close()

    async def stream_turn(
        self, identity: Identity, conversation_id: str, question: str
    ) -> AsyncIterator[TraceEvent | AnswerEnvelope]:
        """Yield trace events as they happen, then the final envelope."""
        trace_id = self._new_trace_id()
        ctx = AgentContext(
            db_path=self._db_path,
            company_id=identity.company_id,
            actor_id=identity.actor_id,
            as_of=self._as_of(),
            trace_id=trace_id,
        )
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id(identity, conversation_id)},
            "recursion_limit": RECURSION_LIMIT,
        }
        seq = 0
        async for chunk in self._graph.astream(
            {"question": question.strip()}, config, context=ctx, stream_mode="custom"
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "trace":
                yield to_event(chunk, trace_id=trace_id, seq=seq)
                seq += 1
        state = await self._graph.aget_state(config)
        yield AnswerEnvelope.model_validate(state.values["envelope"])

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
