from __future__ import annotations

import itertools
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleet_copilot import policy

RefusalReason = Literal["cross_tenant", "prompt_injection", "off_topic", "unsupported_action"]
ShortText = Annotated[str, Field(max_length=80)]


class Reference(BaseModel):
    """How a question points back at an earlier answer ("the first one", "those", "MT7PJB7N5LRE")."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ordinal", "last", "all", "named"]
    index: int | None = Field(default=None, ge=1, le=100)  # 1-based, ordinal only
    list_hint: str | None = Field(default=None, max_length=80)  # words naming which list is meant
    device_ref: str | None = Field(default=None, max_length=80)  # device id or hostname, named only

    @model_validator(mode="after")
    def _shape(self) -> Reference:
        if self.kind == "ordinal" and self.index is None:
            raise ValueError("an ordinal reference needs an index")
        if self.kind != "ordinal" and self.index is not None:
            raise ValueError("index is only valid for an ordinal reference")
        if self.kind == "named" and not self.device_ref:
            raise ValueError("a named reference needs device_ref")
        if self.kind != "named" and self.device_ref is not None:
            raise ValueError("device_ref is only valid for a named reference")
        return self


class Plan(BaseModel):
    """What the planner decided. It never contains an answer, only what to do next."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["answer", "clarify", "refuse", "unsupported"]
    summary: str = Field(max_length=300)
    window_days: int | None = Field(default=None, ge=1, le=policy.MAX_WINDOW_DAYS)
    reference: Reference | None = None
    clarification_question: str | None = Field(default=None, max_length=300)
    clarification_options: list[ShortText] = Field(default_factory=list, max_length=6)
    refusal_reason: RefusalReason | None = None

    @field_validator("clarification_options", mode="before")
    @classmethod
    def _null_means_no_options(cls, value: object) -> object:
        # Live models send `null` for an empty optional list; only that one value is read as "none".
        return [] if value is None else value

    @model_validator(mode="after")
    def _consistent(self) -> Plan:
        if self.intent == "clarify" and not self.clarification_question:
            raise ValueError("a clarify plan needs a clarification_question")
        if self.intent == "refuse" and self.refusal_reason not in (
            "cross_tenant",
            "prompt_injection",
        ):
            raise ValueError("a refuse plan needs refusal_reason cross_tenant or prompt_injection")
        if self.intent == "unsupported" and self.refusal_reason not in (
            "off_topic",
            "unsupported_action",
        ):
            raise ValueError(
                "an unsupported plan needs refusal_reason off_topic or unsupported_action"
            )
        if self.intent in ("answer", "clarify") and self.refusal_reason is not None:
            raise ValueError("refusal_reason is only valid for refuse or unsupported plans")
        return self


class Explanation(BaseModel):
    """Model-written prose. It is verified against code-written facts before it is shown."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(max_length=300)
    explanation: str | None = Field(default=None, max_length=1500)


class LLMGateway(Protocol):
    """The only way the graph talks to a language model. Implementations bind the tools themselves."""

    async def plan(self, messages: list[BaseMessage]) -> Plan: ...

    async def act(self, messages: list[BaseMessage]) -> AIMessage: ...

    async def explain(self, messages: list[BaseMessage]) -> Explanation: ...


# ---- scripted gateway for tests and replay ---------------------------------------------------------

_call_ids = itertools.count(1)


def tool_calls_message(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    """An `AIMessage` asking for these tool calls, with fresh call ids."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": dict(args), "id": f"call-{next(_call_ids)}", "type": "tool_call"}
            for name, args in calls
        ],
    )


def final_message(text: str = "") -> AIMessage:
    """An `AIMessage` with no tool calls: the model has finished gathering evidence."""
    return AIMessage(content=text)


class ScriptExhausted(AssertionError):
    """A test asked the scripted model for a response it was not given."""


@dataclass(frozen=True)
class RecordedCall:
    kind: Literal["plan", "act", "explain"]
    messages: tuple[BaseMessage, ...]

    @property
    def text(self) -> str:
        """Every message's content joined, for substring assertions."""
        return "\n".join(str(m.content) for m in self.messages)


class ScriptedLLM:
    """Plays back queued responses and records exactly what it was asked.

    A queued `Exception` is raised instead of returned, which is how tests simulate a model outage.
    """

    def __init__(
        self,
        *,
        plans: Sequence[Plan | Exception] = (),
        acts: Sequence[AIMessage | Exception] = (),
        explains: Sequence[Explanation | Exception] = (),
    ) -> None:
        self._queues: dict[str, deque[Any]] = {
            "plan": deque(plans),
            "act": deque(acts),
            "explain": deque(explains),
        }
        self.calls: list[RecordedCall] = []

    @property
    def remaining(self) -> dict[str, int]:
        return {kind: len(queue) for kind, queue in self._queues.items()}

    def calls_of(self, kind: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == kind]

    def _next(self, kind: Literal["plan", "act", "explain"], messages: list[BaseMessage]) -> Any:
        self.calls.append(RecordedCall(kind, tuple(messages)))
        queue = self._queues[kind]
        if not queue:
            raise ScriptExhausted(
                f"no scripted {kind} response left (call {len(self.calls_of(kind))})"
            )
        item = queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def plan(self, messages: list[BaseMessage]) -> Plan:
        return self._next("plan", messages)

    async def act(self, messages: list[BaseMessage]) -> AIMessage:
        return self._next("act", messages)

    async def explain(self, messages: list[BaseMessage]) -> Explanation:
        return self._next("explain", messages)
