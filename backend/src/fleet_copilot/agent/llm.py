from __future__ import annotations

import itertools
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, get_args

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from fleet_copilot import policy
from fleet_copilot.agent.memory import clean_note
from fleet_copilot.contracts import ActionType

RefusalReason = Literal[
    "cross_tenant",
    "other_company",
    "prompt_injection",
    "off_topic",
    "unsupported_action",
    "data_unavailable",
]
# Data the telemetry does not hold, so a question about it is answered with a fixed sentence (texts.py).
MissingTopic = Literal[
    "network", "location", "performance", "user_activity", "peripherals", "other"
]
ShortText = Annotated[str, Field(max_length=80)]
# The tools a plan may name: every read tool and the four proposal tools. `recommend_actions` is not here
# because code, not the model, makes that call on an advise turn.
ToolName = Literal[
    "low_disk_devices",
    "macos_version_check",
    "compliance_failures_now",
    "memory_pressure_now",
    "trend",
    "find_devices",
    "device_history",
    "software_inventory",
    "fleet_health",
    "create_upgrade_order",
    "open_remediation_ticket",
    "flag_device_for_replacement",
    "notify_employee",
]
MAX_PLANNED_TOOLS = 4

_MAX_SUMMARY = 300
_MAX_OPTIONS = 6
_MAX_OPTION_LENGTH = 80
_SECURITY_REASONS = ("cross_tenant", "other_company", "prompt_injection")
# Words a model puts in `refusal_reason` when it means "the telemetry has no such data": a topic name, or a
# synonym for "data unavailable" (which names no topic). Read as `missing_topic`, never as a bad reason.
_TOPIC_NAMES = frozenset(get_args(MissingTopic)) - {"other"}
_NO_DATA_SYNONYMS = frozenset(
    {"data_unavailable", "missing_data", "not_in_data", "missing_topic", "no_data", "other"}
)


class Reference(BaseModel):
    """How a question points back at an earlier answer ("the first one", "those", "MT7PJB7N5LRE")."""

    model_config = ConfigDict(extra="ignore")

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


def _word(value: object) -> str | None:
    """A model-written enum word, trimmed and lower-cased; blank or non-text reads as unset."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "_")
    return text or None


def _flag(value: object) -> bool:
    """A model-written yes/no: true, or the word "true" or "yes"; anything else is no."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes")
    return value is True


def _planned_tools(value: object) -> list[str]:
    """The known tool names of a model-written list, in order, once each and at most four.

    A single name is read as a list of one. Unknown or non-text entries are dropped, never an error.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    known = frozenset(get_args(ToolName))
    kept: list[str] = []
    for item in value:
        name = _word(item)
        if name in known and name not in kept:
            kept.append(name)
    return kept[:MAX_PLANNED_TOOLS]


class Plan(BaseModel):
    """What the planner decided. It never contains an answer, only what to do next."""

    model_config = ConfigDict(extra="ignore")

    intent: Literal["answer", "clarify", "refuse", "unsupported", "propose", "advise", "chat"]
    summary: str = Field(default="", max_length=_MAX_SUMMARY)
    window_days: int | None = Field(default=None, ge=1, le=policy.MAX_WINDOW_DAYS)
    reference: Reference | None = None
    # The action the user asked for, when they named one (propose and advise plans). Advice for several
    # devices is filtered to it; it never creates a proposal.
    action_type: ActionType | None = None
    clarification_question: str | None = Field(default=None, max_length=300)
    clarification_options: list[ShortText] = Field(default_factory=list, max_length=_MAX_OPTIONS)
    refusal_reason: RefusalReason | None = None
    # Set for a question about data the telemetry does not contain; code writes the reply for it.
    missing_topic: MissingTopic | None = None
    # The words the user used for a device that could be several ("macbook"), exactly as written. Code finds
    # the matching devices and writes the question and the options; the model never lists them.
    device_phrase: str | None = Field(default=None, max_length=40)
    # A company the question names as the owner of the devices, exactly as written (a misspelling of the
    # user's own company included). Code compares it with the Company line; the model never decides.
    named_company: str | None = Field(default=None, max_length=60)
    # The tools the planner expects to need (an `answer` or `propose` plan; empty otherwise). The act step
    # binds these, plus the two device lookups, instead of every tool.
    tools: list[ToolName] = Field(default_factory=list, max_length=MAX_PLANNED_TOOLS)
    # A preference or context statement the user just made ("I only care about laptops"), kept as a note for
    # the rest of the conversation. Plain text of at most 200 characters; code drops anything else.
    remember: str | None = Field(default=None, max_length=policy.MEMORY_NOTE_CHARS)
    # The user asked to forget the last note ("forget that").
    forget: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: object) -> object:
        """Make an inconsistent but meaningful plan one consistent plan, the same way every time.

        A refusal reason decides the intent, never the other way round, so a plan that names a reason is
        never read as an answer: security reasons are a `refuse`, `off_topic` is `unsupported` and a
        missing data topic is `unsupported` with reason `data_unavailable`. A refusal that names no reason
        gets the safe default for its intent; a clarify plan with a blank question keeps its intent and
        the question stays unset. Text that is too long is cut, null lists are empty lists, and the planned
        tools are the known tool names only (unknown ones dropped, at most four, none for a plan that
        answers nothing). Anything else is left for normal validation.
        """
        if not isinstance(data, dict):
            return data
        repaired = dict(data)
        intent = _word(repaired.get("intent"))
        reason = _word(repaired.get("refusal_reason"))
        topic = _word(repaired.get("missing_topic"))
        if topic is not None and topic not in get_args(MissingTopic):
            topic = "other"
        if reason in _TOPIC_NAMES or reason in _NO_DATA_SYNONYMS:
            # The reason field holds a topic name or a "data unavailable" synonym: that is a missing topic.
            topic = topic or (reason if reason in _TOPIC_NAMES else "other")
            reason = "data_unavailable"
        if intent in ("off_topic", "offtopic"):
            intent, reason = "unsupported", reason or "off_topic"
        if reason in _SECURITY_REASONS:
            intent, topic = "refuse", None
        elif reason == "off_topic":
            intent, topic = "unsupported", None
        elif topic is not None and reason in (None, "unsupported_action", "data_unavailable"):
            intent, reason = "unsupported", "data_unavailable"
        elif reason == "data_unavailable":
            intent, topic = "unsupported", "other"
        elif reason == "unsupported_action":
            intent, topic = "unsupported", None
        elif reason is None:
            topic = None
            if intent == "unsupported":
                reason = "unsupported_action"
            elif intent == "refuse":
                reason = "prompt_injection"
        if intent is not None:
            repaired["intent"] = intent
        repaired["refusal_reason"] = reason
        repaired["missing_topic"] = topic
        summary = repaired.get("summary")
        if summary is None:
            repaired["summary"] = ""
        elif isinstance(summary, str) and len(summary) > _MAX_SUMMARY:
            repaired["summary"] = summary[:_MAX_SUMMARY]
        options = repaired.get("clarification_options")
        if isinstance(options, list):
            kept = [
                o
                for o in options
                if isinstance(o, str) and o.strip() and len(o) <= _MAX_OPTION_LENGTH
            ]
            repaired["clarification_options"] = kept[:_MAX_OPTIONS]
        for name, limit in (("device_phrase", 40), ("named_company", 60)):
            text = repaired.get(name)
            # Blank, non-text or over-long values are not a usable phrase: read as unset, never an error.
            usable = isinstance(text, str) and text.strip() and len(text.strip()) <= limit
            repaired[name] = text.strip() if usable else None
        repaired["tools"] = (
            _planned_tools(repaired.get("tools"))
            if intent in ("answer", "propose") or intent is None
            else []
        )
        repaired["remember"] = clean_note(repaired.get("remember"))
        repaired["forget"] = _flag(repaired.get("forget"))
        question = repaired.get("clarification_question")
        if intent == "clarify" and (question is None or not str(question).strip()):
            repaired["clarification_question"] = None
        return repaired

    @field_validator("clarification_options", mode="before")
    @classmethod
    def _null_means_no_options(cls, value: object) -> object:
        # Live models send `null` for an empty optional list; only that one value is read as "none".
        return [] if value is None else value

    @model_validator(mode="after")
    def _consistent(self) -> Plan:
        if self.intent == "refuse" and self.refusal_reason not in _SECURITY_REASONS:
            raise ValueError(
                "a refuse plan needs refusal_reason cross_tenant, other_company or prompt_injection"
            )
        if self.intent == "unsupported" and self.refusal_reason not in (
            "off_topic",
            "unsupported_action",
            "data_unavailable",
        ):
            raise ValueError(
                "an unsupported plan needs a refusal_reason that is not a security one"
            )
        if (
            self.intent in ("answer", "clarify", "propose", "advise", "chat")
            and self.refusal_reason is not None
        ):
            raise ValueError("refusal_reason is only valid for refuse or unsupported plans")
        if (self.refusal_reason == "data_unavailable") != (self.missing_topic is not None):
            raise ValueError("missing_topic goes with refusal_reason data_unavailable")
        return self


class PlanRejected(Exception):
    """The model returned no usable plan (for example no structured output at all). Never carries model text."""


_SAFE_LOCATION = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def plan_problems(exc: BaseException) -> list[str] | None:
    """What was wrong with a rejected plan, as field names and error types only; None for any other failure.

    The result is shown to the planner on its one retry, so it must never carry model-written values.
    """
    if isinstance(exc, ValidationError):
        found: list[str] = []
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            parts = [
                str(p) for p in error["loc"] if _SAFE_LOCATION.match(str(p)) or isinstance(p, int)
            ]
            problem = f"{'.'.join(parts) or 'plan'}: {error['type']}"
            if problem not in found:
                found.append(problem)
        return found[:6] or ["plan: invalid"]
    if isinstance(exc, PlanRejected | OutputParserException):
        return ["plan: not a structured plan"]
    return None


class Explanation(BaseModel):
    """Model-written prose. It is verified against code-written facts before it is shown."""

    model_config = ConfigDict(extra="forbid")

    # Not used: the headline is always the one code writes. The field stays optional so recorded model
    # responses that carry one still load.
    headline: str | None = Field(default=None, max_length=300)
    explanation: str | None = Field(default=None, max_length=1500)


class ContentFiltered(Exception):
    """The model provider's own content filter refused the request (for example Azure Prompt Shields).

    `category` is "jailbreak" or "content". It never carries provider text.
    """

    def __init__(self, category: Literal["jailbreak", "content"]) -> None:
        super().__init__(category)
        self.category = category


class LLMGateway(Protocol):
    """The only way the graph talks to a language model. Implementations bind the tools themselves."""

    async def plan(self, messages: list[BaseMessage]) -> Plan: ...

    async def act(
        self, messages: list[BaseMessage], tools: Sequence[str] | None = None
    ) -> AIMessage:
        """One evidence-gathering step. `tools` names the only tools the model may call; None is all of them."""
        ...

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
    tools: tuple[str, ...] | None = (
        None  # the tool names an act call was limited to; None is every tool
    )

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

    def _next(
        self,
        kind: Literal["plan", "act", "explain"],
        messages: list[BaseMessage],
        tools: Sequence[str] | None = None,
    ) -> Any:
        self.calls.append(
            RecordedCall(kind, tuple(messages), None if tools is None else tuple(tools))
        )
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

    async def act(
        self, messages: list[BaseMessage], tools: Sequence[str] | None = None
    ) -> AIMessage:
        return self._next("act", messages, tools)

    async def explain(self, messages: list[BaseMessage]) -> Explanation:
        return self._next("explain", messages)
