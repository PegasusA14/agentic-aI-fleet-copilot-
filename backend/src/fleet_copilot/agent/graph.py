"""The agent as an explicit LangGraph: scope -> plan -> (reference) -> act <-> tools -> compose -> talk -> verify -> respond.

Rules the graph enforces, whatever the model says:
  * tenant and actor come from `AgentContext`; no node or tool accepts them from the model;
  * follow-up references are resolved by code against the lists last shown, and the resolved device
    ids are injected into the tool calls, replacing any ids the model supplied;
  * tool rounds, calls per round and retries are bounded;
  * claims, evidence and numbers come from the composer; model prose is shown only if it verifies;
  * the talk step (the model's conversational reply) never runs for a security refusal, and a chat turn
    reaches it from the planner with no tools.
Nodes are small and take what they need from state; `llm` is the only model dependency.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from typing import Any

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime

from fleet_copilot import policy
from fleet_copilot.actions.eligibility import display_hostname
from fleet_copilot.actions.service import ActionService, ProposalNotFound
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent import memory as memory_module
from fleet_copilot.agent import outcomes, texts
from fleet_copilot.agent.cassette import CassetteMiss
from fleet_copilot.agent.compose import (
    Resolver,
    compose,
    extra_suggestions,
    suggestion_evidence,
    talk_facts,
)
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.disambiguation import (
    company_typo_clarification,
    device_clarification,
    is_company_typo,
    is_own_company,
    names_specific_identifier,
)
from fleet_copilot.agent.filters import grounded_args, ungrounded_filters
from fleet_copilot.agent.focus import (
    NeedsClarification,
    NotFoundRef,
    Resolved,
    focus_summary,
    resolve_reference,
)
from fleet_copilot.agent.identity import Identity
from fleet_copilot.agent.llm import (
    MAX_PLANNED_TOOLS,
    ContentFiltered,
    LLMGateway,
    Plan,
    Reference,
    plan_problems,
)
from fleet_copilot.agent.present import ADVISABLE_TOOLS, relevant_to
from fleet_copilot.agent.prompts import (
    act_messages,
    plan_messages,
    plan_retry_message,
    talk_messages,
)
from fleet_copilot.agent.records import ToolRecord, record_from_message, redact_tool_calls
from fleet_copilot.agent.state import (
    TurnState,
    earlier_turn,
    next_focus,
    next_history,
    turn_fingerprint,
)
from fleet_copilot.agent.tools import (
    DEVICE_FILTER_TOOLS,
    INVALID_INPUT_PREFIX,
    LOOKUP_TOOLS,
    PROPOSAL_TOOLS,
    SINGLE_DEVICE_TOOLS,
    TOOLS,
    execute_recommend,
    tool_error_text,
)
from fleet_copilot.agent.trace import emit, trace_args
from fleet_copilot.agent.verify import (
    composition_of,
    dropped_count,
    verify_clarification,
    verify_evidence,
    verify_prose,
)
from fleet_copilot.contracts import (
    REPLY_REASONS,
    AnswerEnvelope,
    EntityList,
    EvidenceItem,
    SuggestedAction,
)
from fleet_copilot.data.loader import connect
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext

# A turn is at most MAX_TOOL_ROUNDS x (act, tools, collect) plus the fixed nodes; leave headroom.
RECURSION_LIMIT = 4 * policy.MAX_TOOL_ROUNDS + 20

ToolCall = dict[str, Any]

# Words that name a specific action (order/upgrade, ticket, flag/replace, notify/tell). A word is a prefix
# match, so "upgrading", "tickets" and "notified" count. Without one, advice is not narrowed to an action.
_ACTION_WORDS = re.compile(
    r"\b(?:order|upgrad|ticket|flag|replac|notif|tell|messag|email|remind)", re.IGNORECASE
)


# The stricter set used before an `unsupported_action` plan is believed: any word that asks for something to
# be done (approve, execute, delete, reboot ...) plus the specific-action words above. Whole words, so the
# data words "installed", "enabled" and "locked" do not count; the check name "screen lock" is blanked out
# first because it is a compliance check, not the lock action.
_CHECK_NAMES = re.compile(
    r"\bscreen[\s_-]*lock\w*|\bos[\s_-]*up[\s_-]*to[\s_-]*date\b|\bdisk[\s_-]*encrypt\w*",
    re.IGNORECASE,
)
_STRICT_ACTION_WORDS = re.compile(
    r"\b(?:approv|execut|delet|remov|reboot|restart|deploy|push|proceed|confirm|accept|submit|apply"
    r"|order|upgrad|ticket|flag|replac|notif|tell|messag|email|remind|send|create)"
    r"|\b(?:wipe|wiping|install|uninstall\w*|lock|disable|enable)\b"
    r"|\brun\s+(?:it|them|that|this)\b|\bgo\s+ahead\b|\bdo\s+(?:it|that|this)\b",
    re.IGNORECASE,
)


_WORD_TOKEN = re.compile(r"[a-z]+", re.IGNORECASE)
# The two words a typo must not hide: approving a proposal and executing it are never something this agent does.
_APPROVAL_WORDS = ("approve", "execute")


def _within_one_edit(a: str, b: str) -> bool:
    """True when `a` is `b` with one letter added, dropped, changed or swapped with its neighbour."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        wrong = [i for i in range(len(a)) if a[i] != b[i]]
        if len(wrong) == 1:
            return True
        return (
            len(wrong) == 2
            and wrong[1] == wrong[0] + 1
            and a[wrong[0]] == b[wrong[1]]
            and a[wrong[1]] == b[wrong[0]]
        )
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long[i]:
        i += 1
    return short[i:] == long[i + 1 :]


def typoed_approval(question: str) -> bool:
    """True when a word of the question is "approve" or "execute" misspelled by one edit ("aprove", "exectue").

    A correctly spelled word is not a typo: the action words above already catch it. Words under five letters
    are skipped so short everyday words never match.
    """
    for token in _WORD_TOKEN.findall(question.lower()):
        if len(token) < 5:
            continue
        for word in _APPROVAL_WORDS:
            forms = (word, word + "d", word + "s", word[:-1] + "ing")
            if token not in forms and any(_within_one_edit(token, form) for form in forms):
                return True
    return False


# Asking the people who own the devices to do something about them: "ask the users", "tell the owners",
# "get them to", and the housekeeping that asking is about ("clean up", "free up space", "tidy", "delete some
# files"). Both are the notify-the-employee action in other words.
_OWNER_ASK = re.compile(
    r"\b(?:ask|tell|get|let|remind|message|email|notify|nudge)\w*\s+"
    r"(?:(?:the|those|these|all|our|each|every)\s+)?"
    r"(?:users?|owners?|employees?|people|staff|assignees?|them|him|her)\b",
    re.IGNORECASE,
)
_CLEAN_UP = re.compile(
    r"\bclean(?:ing)?[\s-]*up\b|\bfree(?:ing)?[\s-]*up\b|\btidy\w*"
    r"|\bdelet\w*\s+(?:some|old|unused|their|files?)|\bclear(?:ing)?\s+(?:out\s+)?(?:some\s+)?(?:space|files?)",
    re.IGNORECASE,
)


def names_an_action(question: str, *, strict: bool = False) -> bool:
    """True when the user's question names a specific action the advice could be limited to.

    `strict` widens the set to every word that asks for something to be done; it decides whether a plan
    that calls the question an unsupported action is believed. A misspelled "approve" or "execute" counts.
    Without `strict`, asking the owners to clean up ("can't we ask the users to free up space?") also counts.
    """
    if strict:
        folded = _CHECK_NAMES.sub(" ", question)
        return _STRICT_ACTION_WORDS.search(folded) is not None or typoed_approval(question)
    return (
        _ACTION_WORDS.search(question) is not None
        or _OWNER_ASK.search(question) is not None
        or _CLEAN_UP.search(question) is not None
    )


# "every one of those", "all of them", "those three", "these": the user points at the whole list shown last.
_BULK_REFERENCE = re.compile(r"\b(?:those|these|them)\b", re.IGNORECASE)
# The action words a bulk follow-up carries, mapped to the action the advice is limited to.
_ACTION_BY_WORD = (
    (re.compile(r"replac|flag", re.IGNORECASE), "replacement_flag"),
    (re.compile(r"ticket", re.IGNORECASE), "remediation_ticket"),
    (re.compile(r"upgrad|order", re.IGNORECASE), "upgrade_order"),
    (re.compile(r"notif|tell|messag|email|remind", re.IGNORECASE), "employee_notification"),
)
_MACOS_VERSION = re.compile(r"\bmac\s*os\w*\s*(?:x\s*)?\d", re.IGNORECASE)
_COMPLIANCE_WORDS = re.compile(
    r"complian|screen[\s_-]*lock|encrypt|up[\s_-]*to[\s_-]*date", re.IGNORECASE
)
_SUMMARY_WORDS = re.compile(
    r"fleet[\s_-]*health|overall|summar|needs?\s+attention|across|everything", re.IGNORECASE
)
_BATTERY_WORDS = re.compile(r"\bbat\w{0,3}r(?:y|ies)\b", re.IGNORECASE)


# A message that puts forward a course of action for the devices already discussed: "can't we ask the users",
# "why not just tell the owners", "what about asking them", "shouldn't we replace it", "instead".
_COURSE_CUE = re.compile(
    r"\b(?:(?:can|could|should|shall|couldn|shouldn)['\u2019]?(?:t|not)?\s+we"
    r"|why\s+not|what\s+about|how\s+about|why\s+(?:do\s+we\s+)?need\w*|why\s+needed"
    r"|instead|rather\s+than|let['\u2019]?s)\b",
    re.IGNORECASE,
)
# "tell me", "remind me": a request to the assistant, not an action on a device.
_ASSISTANT_ASK = re.compile(r"\b(?:tell|show|remind|message)\s+(?:me|us)\b", re.IGNORECASE)
# A question about what already happened to an earlier proposal, not a new course of action.
_PAST_OR_STATUS = re.compile(
    r"\b(?:did\s+(?:we|you|it)|have\s+we|has\s+it|status|approved|executed|happened)\b",
    re.IGNORECASE,
)


def suggests_course_of_action(question: str) -> bool:
    """True when the message puts forward an action for devices already discussed.

    It needs both a cue that proposes or questions a course ("can't we", "why not", "what about", "instead")
    and a named action (the non-strict `names_an_action` set, which includes asking the owners to clean up).
    A greeting, thanks, a capability question and a question about what already happened name no action, or
    carry no cue, and are not this.
    """
    if _PAST_OR_STATUS.search(question):
        return False
    folded = _ASSISTANT_ASK.sub(" ", question)
    return _COURSE_CUE.search(folded) is not None and names_an_action(folded)


def bulk_action_type(question: str) -> str | None:
    """The action a "do it for all of those" follow-up names, or None when it names none.

    Asking the owners to clean up is the notification even when the message also names the action it would
    replace ("why needed upgrade, can't we ask the users to clean up?").
    """
    if _OWNER_ASK.search(question) or _CLEAN_UP.search(question):
        return "employee_notification"
    for pattern, action in _ACTION_BY_WORD:
        if pattern.search(question):
            return action
    return None


def adjust_tools(plan: Plan, question: str) -> Plan:
    """The plan with the tool list corrected where the question leaves no doubt which tool is meant.

    A question that names a macOS version needs `macos_version_check`, and not the compliance check unless it
    asks about compliance too; a battery question that asks for no summary uses `trend`, not `fleet_health`.
    """
    if plan.intent != "answer":
        return plan
    tools = list(plan.tools)
    if _MACOS_VERSION.search(question):
        if "macos_version_check" not in tools:
            tools.insert(0, "macos_version_check")
        if not _COMPLIANCE_WORDS.search(question):
            tools = [t for t in tools if t != "compliance_failures_now"]
    if (
        _BATTERY_WORDS.search(question)
        and not _SUMMARY_WORDS.search(question)
        and "fleet_health" in tools
    ):
        tools = ["trend" if t == "fleet_health" else t for t in tools]
        tools = list(dict.fromkeys(tools))
    tools = tools[:MAX_PLANNED_TOOLS]
    return plan if tools == plan.tools else plan.model_copy(update={"tools": tools})


_TOOL_NAMES = frozenset(t.name for t in TOOLS)
# What a trace shows for a tool name the model made up: never the model's own text.
UNKNOWN_TOOL = "unknown_tool"


@contextmanager
def _repo(ctx: AgentContext):
    """A read-only repository for this tenant. The graph never writes to the dataset."""
    conn = connect(ctx.db_path, read_only=True)
    try:
        yield FleetRepository(conn, TenantContext(ctx.company_id, ctx.actor_id))
    finally:
        conn.close()


def _label_lookup(repo: FleetRepository) -> Callable[[str], str]:
    """Name a device by its display hostname, or by its id when the repository has no such device."""

    def label(device_id: str) -> str:
        try:
            return display_hostname(repo.get_device(device_id).hostname, device_id)
        except NotFound:
            return device_id

    return label


def _ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _dump(envelope: AnswerEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json")


def _failure(ctx: AgentContext, exc: BaseException | None = None) -> dict[str, Any]:
    build = outcomes.not_recorded if isinstance(exc, CassetteMiss) else outcomes.tool_failure
    return {
        "envelope": _dump(build(company_id=ctx.company_id, as_of=ctx.as_of, trace_id=ctx.trace_id))
    }


def _provider_refused(ctx: AgentContext, exc: ContentFiltered, stage: str) -> dict[str, Any]:
    """The provider's own filter refused the call: answer with the fixed refusal, not a tool error."""
    data: dict[str, Any] = {"intent": "refuse", "provider_filter": exc.category}
    if stage != "plan":
        data["stage"] = stage
    emit("plan", "The model provider declined the question", data=data)
    reason = "prompt_injection" if exc.category == "jailbreak" else "off_topic"
    return {
        "envelope": _dump(
            outcomes.refusal(
                reason, company_id=ctx.company_id, as_of=ctx.as_of, trace_id=ctx.trace_id
            )
        )
    }


def _plan_fallback(ctx: AgentContext, problems: list[str], started: float) -> dict[str, Any]:
    """The planner stayed invalid after its one retry: decline as off topic, in fixed words, never a tool error."""
    emit(
        "plan",
        "The plan stayed invalid after one retry; declined as off topic",
        data={
            "intent": "unsupported",
            "tools": [],
            "repaired": False,
            "fallback": "off_topic",
            "problems": problems,
        },
        latency_ms=_ms(started),
    )
    plan = Plan(intent="unsupported", summary="", refusal_reason="off_topic")
    return {
        "plan": plan.model_dump(mode="json"),
        "envelope": _dump(
            outcomes.refusal(
                "off_topic", company_id=ctx.company_id, as_of=ctx.as_of, trace_id=ctx.trace_id
            )
        ),
    }


def _status_lookup(ctx: AgentContext) -> Callable[[str], str | None] | None:
    """Read a proposal's current status from the action service, as this tenant; None when there is no store.

    Read-only: it only calls `ActionService.get`. A proposal the service does not know (or that belongs to
    another company) reads as gone.
    """
    if ctx.actions_db_path is None:
        return None
    service = ActionService(fleet_db=ctx.db_path, store=ActionStore(ctx.actions_db_path))
    identity = Identity(ctx.company_id, ctx.actor_id, "")

    def lookup(proposal_id: str) -> str | None:
        try:
            return service.get(identity, proposal_id).status
        except ProposalNotFound:
            return None

    return lookup


def _refreshed(state: TurnState, ctx: AgentContext) -> dict[str, Any]:
    """The memory update for the start of a turn: remembered proposals read at their live status."""
    held = memory_module.load(state.get("memory"))
    if not any(turn.proposals for turn in held.turns):
        return {}
    try:
        lookup = _status_lookup(ctx)
        if lookup is None:
            return {}
        current = memory_module.refresh_proposals(held, lookup)
    except Exception as exc:  # the remembered status stays as it was; the turn goes on
        emit("error", "Reading proposal status failed", data={"error": type(exc).__name__})
        return {}
    return {} if current == held else {"memory": memory_module.dump(current)}


def inject_focus(name: str, args: ToolCall, focus_ids: list[str] | None) -> ToolCall:
    """Replace model-supplied device ids with the ids code resolved from the user's follow-up."""
    if not focus_ids:
        return dict(args)
    if name in DEVICE_FILTER_TOOLS:
        return {**args, "device_ids": list(focus_ids)}
    # device_history and the four proposal tools take one device. With several resolved ids there is nothing to
    # inject, so the model's own `device_id` stays; it is still looked up through the tenant-scoped repository,
    # so a foreign or unknown id gets the uniform not-found.
    if name in SINGLE_DEVICE_TOOLS and len(focus_ids) == 1:
        return {**args, "device_id": focus_ids[0]}
    return dict(args)


def _is_input_error(result: Any) -> bool:
    """The model sent arguments the tool rejected. It sees the message and may call again."""
    return (
        isinstance(result, ToolMessage)
        and result.status == "error"
        and str(result.content).startswith(INVALID_INPUT_PREFIX)
    )


def _is_transient_error(result: Any) -> bool:
    """A failed tool call worth one retry. Input errors are the model's to fix, so they are not retried."""
    return (
        isinstance(result, ToolMessage) and result.status == "error" and not _is_input_error(result)
    )


NOT_PLANNED = "that tool is not available for this question"


def bound_tools(plan: Plan, widened: bool = False) -> frozenset[str] | None:
    """The tools the act step may use for this plan, or None for every tool.

    A plan that names tools gets those plus the two lookups. Proposal tools are only ever offered to a
    `propose` plan, and then only the one(s) it names (all four when it names none). A plan that names
    nothing usable, an advise plan, or one already widened this turn, gets every tool.
    """
    if widened or not plan.tools or plan.intent == "advise":
        return None
    planned = set(plan.tools)
    reads = planned - PROPOSAL_TOOLS
    if plan.intent == "propose":
        return frozenset(reads | LOOKUP_TOOLS | ((planned & PROPOSAL_TOOLS) or PROPOSAL_TOOLS))
    if not reads:
        return None
    return frozenset(reads | LOOKUP_TOOLS)


def without_summary_tool(plan: Plan) -> Plan:
    """The plan without `fleet_health` when it also names an area tool: the summary is for summaries only.

    A question about one area (battery, disk, memory, one check) is answered by that area's tool; the plan
    that lists both would have the summary list every flagged device. A plan that names only `fleet_health`
    (and perhaps the lookups) is a real summary question and is kept.
    """
    if "fleet_health" not in plan.tools:
        return plan
    areas = [t for t in plan.tools if t not in ({"fleet_health"} | PROPOSAL_TOOLS | LOOKUP_TOOLS)]
    if not areas:
        return plan
    return plan.model_copy(update={"tools": [t for t in plan.tools if t != "fleet_health"]})


def filter_sources(state: Mapping[str, Any]) -> str:
    """The words a model-chosen filter may come from: this turn's question and the active notes."""
    notes = memory_module.load(state.get("memory")).notes
    return "\n".join([str(state.get("question") or ""), *(note.text for note in notes)])


def effective_args(name: str, args: ToolCall, state: Mapping[str, Any]) -> ToolCall:
    """The arguments a call really runs with: ungrounded filters and blank ids gone, resolved ids injected.

    One place for both the executed call and its record, so the record's args always equal the executed args.
    """
    return inject_focus(
        name,
        without_empty_ids(grounded_args(args, filter_sources(state))),
        state.get("focus_ids"),
    )


def without_empty_ids(args: ToolCall) -> ToolCall:
    """The call's arguments without blank device ids: no devices named is no filter, not "no such device".

    The model sometimes sends `[""]` for a fleet-wide question; read as an id, that is a uniform "not found".
    Blank entries go, and with none left the filter goes. Real ids stay, whoever's they are.
    """
    ids = args.get("device_ids")
    if not isinstance(ids, list):
        return args
    kept = [i for i in ids if not (isinstance(i, str) and not i.strip())]
    if len(kept) == len(ids) and kept:
        return args
    rest = {key: value for key, value in args.items() if key != "device_ids"}
    return {**rest, "device_ids": kept} if kept else rest


def _tool_order(names: frozenset[str]) -> list[str]:
    return [t.name for t in TOOLS if t.name in names]


def _not_bound(name: str, state: Any) -> str | None:
    """Why this call is outside the tools bound for the plan (fixed text for the model), or None."""
    plan = state.get("plan")
    if not plan:
        return None
    bound = bound_tools(Plan.model_validate(plan), bool(state.get("widened")))
    return None if bound is None or name in bound else NOT_PLANNED


PROPOSE_ONLY_FOR_ACTIONS = "proposal tools are only for action requests"
ONE_PROPOSAL_PER_TURN = "one proposal per turn"
RECOMMEND_ONLY_FOR_ADVICE = "recommend_actions is only for advice requests"
ONE_RECOMMENDATION_PER_TURN = "one recommendation per turn"


def _propose_blocked(name: str, state: Any) -> str | None:
    """Why this proposal-tool call must not run (fixed text for the model), or None.

    The four proposal tools are one class (`PROPOSAL_TOOLS`). Only a propose plan may create a proposal, and
    a turn holds at most one across all four: a call after a proposal or refusal is rejected whatever the
    model says.
    """
    if name not in PROPOSAL_TOOLS:
        return None
    if (state.get("plan") or {}).get("intent") != "propose":
        return PROPOSE_ONLY_FOR_ACTIONS
    if any(r.get("tool") in PROPOSAL_TOOLS for r in state.get("records") or []):
        return ONE_PROPOSAL_PER_TURN
    return None


def _recommend_blocked(name: str, state: Any) -> str | None:
    """Why this `recommend_actions` call must not run (fixed text for the model), or None.

    Only an advise plan may ask for recommendations, and a turn holds at most one.
    """
    if name != "recommend_actions":
        return None
    if (state.get("plan") or {}).get("intent") != "advise":
        return RECOMMEND_ONLY_FOR_ADVICE
    if any(r.get("tool") == "recommend_actions" for r in state.get("records") or []):
        return ONE_RECOMMENDATION_PER_TURN
    return None


def _result_status(result: Any, record: ToolRecord) -> str:
    if _is_input_error(result):
        return "invalid_input"
    if record.suggestions is not None:
        return "recommended"
    if record.proposal is not None:
        return "proposed"
    if record.action_refusal is not None:
        return "refused"
    return record.error or "ok"


def _company_scope_envelope(plan: Plan, question: str, ctx: AgentContext) -> AnswerEnvelope | None:
    """The reply when the question is about a company other than the user's own, or None to carry on.

    A general question about another company ("devices at another company", "low disk at Globex") gets the
    `other_company` card, which names only the user's own company. A question that names one particular
    device id, hostname or employee id is always the uniform "not found" instead, so it cannot be told
    apart from an id that does not exist. A near miss of the user's own company name (a typo) is asked about.
    """
    kwargs = {
        "company_id": ctx.company_id,
        "as_of": ctx.as_of,
        "trace_id": ctx.trace_id,
        "company_name": ctx.company_name,
    }
    written = (plan.named_company or "").strip()
    own = ctx.company_name
    named = bool(written and own and written.lower() in question.lower())
    specific = names_specific_identifier(question)
    if (
        named
        and not specific
        and plan.intent != "unsupported"
        and plan.refusal_reason not in ("prompt_injection", "cross_tenant")
        and is_company_typo(written, own)
    ):
        typo = company_typo_clarification(question, written, own)
        if typo is not None:
            return outcomes.clarification(
                typo.question,
                [],
                choices=typo.choices,
                company_id=ctx.company_id,
                as_of=ctx.as_of,
                trace_id=ctx.trace_id,
            )
    if plan.intent == "refuse" and plan.refusal_reason == "other_company":
        return outcomes.refusal("cross_tenant" if specific else "other_company", **kwargs)
    if not named or plan.intent in ("refuse", "unsupported") or is_own_company(written, own):
        return None
    return outcomes.refusal("cross_tenant" if specific else "other_company", **kwargs)


def _device_choice_envelope(plan: Plan, question: str, ctx: AgentContext) -> AnswerEnvelope | None:
    """Code-built clarification when the planner says the user's device phrase could be several devices."""
    if not plan.device_phrase:
        return None
    with _repo(ctx) as repo:
        built = device_clarification(question, plan.device_phrase, repo.list_devices())  # type: ignore[arg-type]
    if built is None:
        return None
    return outcomes.clarification(
        built.question,
        [],
        choices=built.choices,
        company_id=ctx.company_id,
        as_of=ctx.as_of,
        trace_id=ctx.trace_id,
    )


async def _tool_wrapper(
    request: ToolCallRequest, execute: Callable[[ToolCallRequest], Awaitable[Any]]
) -> Any:
    """Inject resolved device ids, trace the call, and retry a transient failure once."""
    call = request.tool_call
    name = call["name"]
    if name not in _TOOL_NAMES:
        # A made-up tool is the model's mistake: tell it (without echoing the name), never retry it.
        emit(
            "tool_result",
            "A tool call named no known tool",
            tool=UNKNOWN_TOOL,
            data={"status": "invalid_input", "matches": 0, "population": None},
        )
        return ToolMessage(
            content=f"{INVALID_INPUT_PREFIX}: unknown tool. Use one of: {', '.join(sorted(_TOOL_NAMES))}",
            tool_call_id=call["id"],
            status="error",
        )
    blocked = (
        _not_bound(name, request.state)
        or _propose_blocked(name, request.state)
        or _recommend_blocked(name, request.state)
    )
    if blocked:
        emit(
            "tool_result",
            f"{name} was not run",
            tool=name,
            data={"status": "invalid_input", "matches": 0, "population": None},
        )
        return ToolMessage(
            content=f"{INVALID_INPUT_PREFIX}: {blocked}", tool_call_id=call["id"], status="error"
        )
    dropped = ungrounded_filters(call["args"], filter_sources(request.state))
    if dropped:
        emit(
            "tool_call",
            "A filter the question never named was dropped",
            tool=name,
            data={"dropped_filters": dropped},
        )
    args = effective_args(name, call["args"], request.state)
    if args != call["args"]:
        request = request.override(tool_call={**call, "args": args})
    # Only the tool's own parameter names are traced; the model cannot smuggle text in as a key.
    known = request.tool.args if request.tool is not None else {}
    traced = {key: value for key, value in args.items() if key in known}
    emit("tool_call", f"Calling {name}", tool=name, data={"args": trace_args(traced)})
    started = time.perf_counter()
    result = await execute(request)
    if _is_transient_error(result):
        emit("retry", f"Retrying {name} once after a failed lookup", tool=name)
        result = await execute(request)
    record = record_from_message(result, tool=name, args=args)
    emit(
        "tool_result",
        f"{name} returned",
        tool=name,
        data={
            "status": _result_status(result, record),
            "matches": len(record.findings)
            if record.suggestions is None
            else len(record.suggestions),
            "population": record.population,
        },
        latency_ms=_ms(started),
    )
    return result


def _list_suggestions(
    ctx: AgentContext, composition: Any, records: list[ToolRecord], resolve: Resolver
) -> dict[str, Any] | None:
    """Code-only suggested actions for the devices a list answer found, or None.

    The same deterministic `recommend_actions` an advise turn runs, over the devices the answer lists
    (in the order it lists them), with the same cap, but keeping only the next steps that belong to the
    answer's subject (`present.SUBJECT_ACTIONS`). It makes no model call. The result is kept out of the
    draft the model reads, and merged into the final envelope after the prose is verified.
    """
    advisable = [r for r in records if r.tool in ADVISABLE_TOOLS and not r.error]
    if not advisable:
        return None
    matched = {device_id for r in advisable for device_id in r.device_ids}
    listed = dict.fromkeys(
        item.device_id for entity_list in composition.entity_lists for item in entity_list.items
    )
    ids = [device_id for device_id in listed if device_id in matched]
    if not ids:
        return None
    try:
        record = execute_recommend({"device_ids": ids}, ctx, only=relevant_to(advisable))
        if record.error or not record.suggestions:
            return None
        suggestions = [SuggestedAction.model_validate(s) for s in record.suggestions]
        evidence = suggestion_evidence(composition, suggestions, resolve)
    except Exception as exc:  # suggestions are an extra: the answer stands without them
        emit("error", "Suggesting actions failed", data={"error": type(exc).__name__})
        return None
    return {
        "suggested_actions": [s.model_dump(mode="json") for s in suggestions],
        "evidence": [e.model_dump(mode="json") for e in evidence],
    }


def _with_extras(draft: AnswerEnvelope, extras: dict[str, Any]) -> AnswerEnvelope | None:
    """The draft with its suggestions and their evidence, or None if they do not form a valid envelope."""
    try:
        suggestions = [SuggestedAction.model_validate(s) for s in extras["suggested_actions"]]
        evidence = [EvidenceItem.model_validate(e) for e in extras["evidence"]]
        merged = draft.model_copy(
            update={"evidence": [*draft.evidence, *evidence], "suggested_actions": suggestions}
        )
        return AnswerEnvelope.model_validate(merged.model_dump())
    except (KeyError, ValueError):
        return None


def _apply_notes(plan: Plan, held: memory_module.ConversationMemory) -> dict[str, Any]:
    """The memory update for what the user asked to remember or forget this turn (none when neither).

    Never for a refused or unsupported question, so a hostile message cannot plant a note. The trace
    carries counts only, never the note.
    """
    if not plan.remember and not plan.forget:
        return {}
    kept = memory_module.forget_last_note(held) if plan.forget else held
    current = memory_module.add_note(kept, plan.remember) if plan.remember else kept
    emit(
        "memory",
        "Updated the notes for this conversation",
        data={
            "remembered": current.notes != kept.notes,
            "forgot": kept.notes != held.notes,
            "notes": len(current.notes),
            "turns": len(current.turns),
        },
    )
    return {"memory": memory_module.dump(current)}


def _planner_memory(state: TurnState) -> memory_module.ConversationMemory | None:
    """The memory the planner reads. A conversation saved before memory existed has only `history`, so it
    keeps reading that until its first turn is remembered."""
    held = memory_module.load(state.get("memory"))
    if held.turns or held.notes or not state.get("history"):
        return held
    return None


def _talks(draft: AnswerEnvelope) -> bool:
    """Whether the talk step runs for this draft: answers, proposals, chat, and the refusals code declined for
    lack of evidence or data. Never a security refusal, an off-topic one, a clarification or a failure."""
    if draft.outcome in ("answer", "proposal", "chat"):
        return True
    return draft.outcome in ("denied", "unsupported") and draft.reason_code in REPLY_REASONS


def _repeat_hint(draft: AnswerEnvelope, held: memory_module.ConversationMemory) -> str | None:
    """What the talk step is told when this turn gives the same result as an earlier one, in code's words."""
    if draft.repeat_of is None:
        return None
    number = next((t.turn for t in held.turns if t.trace_id == draft.repeat_of), None)
    where = f"turn {number}" if number is not None else "an earlier turn"
    return (
        f"this is the same result as {where}; say so briefly in one short line and add nothing new."
    )


def _guard_plan(plan: Plan, question: str, focus: list[EntityList]) -> tuple[Plan, str, str] | None:
    """A corrected plan, the trace summary and the change's name, or None when the plan stands.

    Three misreads the planner makes on live runs, fixed by code. A message that proposes a course of action
    for the devices just discussed ("can't we ask the users to clean up?") is advice, never chat: the
    suggestions are computed for it. A misspelled approval ("aprove it now") that
    the planner turned into a question is an unsupported action: nothing here approves anything. A bulk
    follow-up ("mark every one of those laptops for replacement") after exactly one list is advice for that
    list, not a question about which list is meant.
    """
    if plan.intent == "chat":
        if focus and suggests_course_of_action(question):
            return (
                plan.model_copy(
                    update={
                        "intent": "advise",
                        "summary": "Advise on the devices of the last answer.",
                        "tools": [],
                        "action_type": bulk_action_type(question),
                    }
                ),
                "The message puts forward an action for the devices already discussed; answered with advice",
                "chat_to_advise",
            )
        return None
    if plan.intent != "clarify":
        return None
    if typoed_approval(question):
        return (
            Plan(intent="unsupported", summary="", refusal_reason="unsupported_action"),
            "The question asks for an approval in other words; answered as an unsupported action",
            "clarify_to_unsupported_action",
        )
    if (
        len(focus) == 1
        and not plan.device_phrase
        and _BULK_REFERENCE.search(question)
        and names_an_action(question)
    ):
        action = bulk_action_type(question)
        return (
            Plan(
                intent="advise",
                summary="Advise on every device of the last list.",
                reference=Reference(kind="all"),
                action_type=action,  # type: ignore[arg-type]
            ),
            "The follow-up points at the one list shown last; answered with advice for it",
            "clarify_to_advise",
        )
    return None


def build_graph(llm: LLMGateway, checkpointer: Any):
    """Compile the agent graph around one model gateway and one checkpointer."""

    async def scope(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        emit("scope", "Turn started", data={"company_id": runtime.context.company_id})
        return {
            **_refreshed(state, runtime.context),
            "turn_messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
            "plan": None,
            "focus_ids": None,
            "pending_calls": False,
            "rounds": 0,
            "limit_hit": False,
            "widened": False,
            "incomplete": False,
            "unfixed_calls": [],
            "records": [],
            "draft": None,
            "extras": None,
            "fingerprint": None,
            "prose": None,
            "envelope": None,
        }

    async def plan_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        focus = [EntityList.model_validate(item) for item in state.get("focus") or []]
        started = time.perf_counter()
        messages = plan_messages(
            question=state["question"],
            as_of=ctx.as_of,
            history=state.get("history") or [],
            focus_lines=focus_summary(focus),
            company_name=ctx.company_name,
            memory=_planner_memory(state),
        )
        repaired = False
        try:
            try:
                plan = await llm.plan(messages)
            except Exception as exc:
                problems = plan_problems(exc)
                if problems is None:
                    raise
                # The model's plan did not validate: ask once more, naming only the fields and error types.
                emit(
                    "retry",
                    "The plan was invalid; asking the planner once more",
                    data={"problems": problems},
                )
                try:
                    plan = await llm.plan([*messages, plan_retry_message(problems)])
                    repaired = True
                except Exception as second:
                    second_problems = plan_problems(second)
                    if second_problems is None:
                        raise
                    return _plan_fallback(ctx, second_problems, started)
        except ContentFiltered as exc:
            return _provider_refused(ctx, exc, "plan")
        except Exception as exc:  # model outage or unusable structured output
            emit("error", "Planning failed", data={"error": type(exc).__name__})
            return _failure(ctx, exc)
        guarded = _guard_plan(plan, state["question"], focus)
        if guarded is not None:
            plan = guarded[0]
            emit("plan", guarded[1], data={"changed": guarded[2]})
        converted = False
        if (
            plan.intent == "unsupported"
            and plan.refusal_reason == "unsupported_action"
            and not names_an_action(state["question"], strict=True)
        ):
            # Nothing in the question asks for anything to be done ("os update failures?", "screen lock
            # failing"): it is a question about the data. Answer it with every tool on offer.
            plan = Plan(
                intent="answer",
                summary="Answer the question from the fleet data.",
                window_days=plan.window_days,
                reference=plan.reference,
            )
            converted = True
        before = plan.tools
        plan = without_summary_tool(plan)
        adjusted = adjust_tools(plan, state["question"])
        if adjusted.tools != plan.tools:
            emit(
                "plan",
                "The question names its tool plainly; the planned tools were corrected",
                data={"changed": "tools"},
            )
            plan = adjusted
            before = plan.tools
        emit(
            "plan",
            "Planned the turn",
            data={
                "intent": plan.intent,
                "window_days": plan.window_days,
                "tools": list(plan.tools),
                "has_reference": plan.reference is not None,
                "reference_kind": plan.reference.kind if plan.reference else None,
                "action_type": plan.action_type,
                "missing_topic": plan.missing_topic,
                "repaired": repaired,
                "fallback": False,
            },
            latency_ms=_ms(started),
        )
        if converted:
            emit(
                "plan",
                "The plan called the question an unsupported action but it names no action; answering it",
                data={"changed": "unsupported_action_to_answer"},
            )
        if plan.tools != before:
            emit(
                "plan",
                "The plan named the fleet summary with an area tool; the summary tool was dropped",
                data={"dropped": "fleet_health"},
            )
        update: dict[str, Any] = {"plan": plan.model_dump(mode="json")}
        kwargs = {"company_id": ctx.company_id, "as_of": ctx.as_of, "trace_id": ctx.trace_id}
        scoped = _company_scope_envelope(plan, state["question"], ctx)
        if scoped is None and plan.intent not in ("refuse", "unsupported"):
            update.update(_apply_notes(plan, memory_module.load(state.get("memory"))))
        if scoped is not None:
            update["envelope"] = _dump(scoped)
        elif plan.intent == "clarify":
            built = _device_choice_envelope(plan, state["question"], ctx)
            if built is not None:
                update["envelope"] = _dump(built)
            else:
                checked = verify_clarification(
                    plan.clarification_question or "", list(plan.clarification_options)
                )
                question, options = checked if checked else (None, [])
                update["envelope"] = _dump(outcomes.clarification(question, options, **kwargs))
        elif plan.intent == "chat" and plan.forget and not plan.remember:
            # The notes update above already dropped the note. The words are code-written and final: the
            # talk step has no say, so it cannot answer as if the note still held.
            removed = bool(memory_module.load(state.get("memory")).notes)
            update["envelope"] = _dump(outcomes.forgot(removed, **kwargs))
        elif plan.intent == "chat":
            # No tools and no lookup: the talk step answers from the conversation's memory and the
            # capability card. Any note the message carried has already been kept.
            update["draft"] = _dump(outcomes.chat(**kwargs))
        elif plan.intent in ("refuse", "unsupported") and plan.refusal_reason:
            refusal = _dump(
                outcomes.refusal(
                    plan.refusal_reason,
                    missing_topic=plan.missing_topic,
                    company_name=ctx.company_name,
                    **kwargs,
                )
            )
            # Only "the data holds no such thing" gets a framing sentence from the talk step; every other
            # refusal is final as written.
            update["draft" if plan.refusal_reason in REPLY_REASONS else "envelope"] = refusal
        return update

    async def reference_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        plan = Plan.model_validate(state["plan"])
        focus = [EntityList.model_validate(item) for item in state.get("focus") or []]
        kwargs = {"company_id": ctx.company_id, "as_of": ctx.as_of, "trace_id": ctx.trace_id}
        reference = plan.reference
        advise = plan.intent == "advise"
        if advise and (reference is None or reference.kind != "named"):
            if not focus:
                emit("reference", "Nothing to advise on yet", data={"resolved": False})
                return {
                    "envelope": _dump(
                        outcomes.clarification(texts.ADVISE_NEEDS_DEVICES, [], **kwargs)
                    )
                }
            # No named device: advice covers every device of the last substantive answer.
            reference = reference or Reference(kind="all")
        assert reference is not None
        with _repo(ctx) as repo:
            resolution = resolve_reference(reference, focus, repo.list_devices())  # type: ignore[arg-type]
        if isinstance(resolution, Resolved):
            emit(
                "reference",
                "Resolved the follow-up",
                data={"kind": reference.kind, "devices": len(resolution.device_ids)},
            )
            if plan.intent == "propose" and len(resolution.device_ids) > 1:
                # One device per proposal. A request to act on several devices is answered as advice for
                # those devices instead: code-computed suggestions, each of which can be approved on its own.
                emit(
                    "reference",
                    "The action request names several devices; answering with advice",
                    data={"devices": len(resolution.device_ids)},
                )
                advised = plan.model_copy(update={"intent": "advise", "tools": []})
                return {
                    "focus_ids": list(resolution.device_ids),
                    "plan": advised.model_dump(mode="json"),
                }
            return {"focus_ids": list(resolution.device_ids)}
        if isinstance(resolution, NeedsClarification):
            emit("reference", "The follow-up needs clarification", data={"resolved": False})
            return {
                "envelope": _dump(
                    outcomes.clarification(resolution.question, list(resolution.options), **kwargs)
                )
            }
        assert isinstance(resolution, NotFoundRef)
        emit("reference", "The follow-up did not resolve to a device", data={"resolved": False})
        return {"envelope": _dump(outcomes.refusal("cross_tenant", **kwargs))}

    async def _ask(bound: frozenset[str] | None, messages: list[Any]) -> AIMessage:
        if bound is None:
            return await llm.act(messages)
        return await llm.act(messages, tools=_tool_order(bound))

    async def act_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        plan = Plan.model_validate(state["plan"])
        if plan.intent == "advise":
            # Advice never depends on the model's choice of tool: code makes the one call, then stops.
            if any(r.get("tool") == "recommend_actions" for r in state.get("records") or []):
                return {"pending_calls": False}
            args: dict[str, Any] = {"device_ids": list(state.get("focus_ids") or [])}
            if plan.action_type:
                if names_an_action(state["question"]):
                    args["action_type"] = plan.action_type
                else:
                    emit(
                        "plan",
                        "Generic advice: the planner's action filter was ignored",
                        data={"action_type": plan.action_type},
                    )
            call = {
                "name": "recommend_actions",
                "args": args,
                "id": f"advise-{uuid.uuid4().hex[:12]}",
                "type": "tool_call",
            }
            return {
                "turn_messages": [AIMessage(content="", tool_calls=[call])],
                "pending_calls": True,
            }
        started = time.perf_counter()
        widened = bool(state.get("widened"))
        messages = act_messages(
            question=state["question"],
            as_of=ctx.as_of,
            plan_summary=plan.summary,
            window_days=plan.window_days,
            resolved_ids=state.get("focus_ids"),
            history=state.get("history") or [],
            turn_messages=state.get("turn_messages") or [],
        )
        try:
            bound = bound_tools(plan, widened)
            message = await _ask(bound, messages)
            calls = list(message.tool_calls)
            if (
                not calls
                and bound is not None
                and not state.get("records")
                and state.get("rounds", 0) < policy.MAX_TOOL_ROUNDS
            ):
                # The planned tools gathered nothing: offer every tool once, for the rest of the turn.
                emit(
                    "plan",
                    "The planned tools gathered nothing; every tool is offered instead",
                    data={"widened": True, "planned": list(plan.tools)},
                )
                widened = True
                message = await _ask(None, messages)
        except ContentFiltered as exc:
            return _provider_refused(ctx, exc, "act")
        except Exception as exc:
            emit("error", "Gathering evidence failed", data={"error": type(exc).__name__})
            return _failure(ctx, exc)
        calls = list(message.tool_calls)
        grown: dict[str, Any] = {"widened": True} if widened else {}
        if not calls:
            return {"pending_calls": False, **grown}
        if state.get("rounds", 0) >= policy.MAX_TOOL_ROUNDS:
            emit(
                "compose",
                "Stopped at the tool-round limit",
                data={"max_rounds": policy.MAX_TOOL_ROUNDS},
                latency_ms=_ms(started),
            )
            return {"pending_calls": False, "limit_hit": True, **grown}
        dropped_any = False
        proposals = [i for i, call in enumerate(calls) if call["name"] in PROPOSAL_TOOLS]
        if len(proposals) > 1:
            # One proposal per turn, whichever of the four tools: only the first request is kept.
            emit("tool_call", "Dropped extra proposal calls", data={"dropped": len(proposals) - 1})
            extra = set(proposals[1:])
            calls = [call for i, call in enumerate(calls) if i not in extra]
            dropped_any = True
        if len(calls) > policy.MAX_CALLS_PER_ROUND:
            emit(
                "tool_call",
                "Dropped tool calls beyond the per-round limit",
                data={"dropped": len(calls) - policy.MAX_CALLS_PER_ROUND},
            )
            calls = calls[: policy.MAX_CALLS_PER_ROUND]
            dropped_any = True
        # Free text from the model is not kept: only the calls are.
        update: dict[str, Any] = {
            "turn_messages": [AIMessage(content="", tool_calls=calls)],
            "pending_calls": True,
            **grown,
        }
        if dropped_any:
            update["incomplete"] = True  # something the model asked for will never run
        return update

    async def collect_node(state: TurnState) -> dict[str, Any]:
        """Turn this round's tool messages into records, and redact the call arguments kept in state."""
        messages = state["turn_messages"]
        ai = next(m for m in reversed(messages) if isinstance(m, AIMessage) and m.tool_calls)
        replies = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
        records = list(state.get("records") or [])
        unfixed = list(state.get("unfixed_calls") or [])
        for call in ai.tool_calls:
            args = effective_args(call["name"], call["args"], state)
            reply = replies.get(call["id"])
            # Names only, and never a name the model made up: it is compared, not shown.
            name = call["name"] if call["name"] in _TOOL_NAMES else UNKNOWN_TOOL
            if _is_input_error(reply):
                # The model can correct this call; it must not turn the whole answer into an error.
                # Until a later call of the same tool goes through, the answer is incomplete.
                if name not in unfixed:
                    unfixed.append(name)
                continue
            if name in unfixed:
                unfixed.remove(name)
            records.append(
                (
                    record_from_message(reply, tool=call["name"], args=args)
                    if reply is not None
                    else ToolRecord(tool=call["name"], args={}, error="tool_error")
                ).model_dump(mode="json")
            )
        redacted = AIMessage(
            content="", tool_calls=redact_tool_calls(list(ai.tool_calls)), id=ai.id
        )
        return {
            "turn_messages": [redacted],
            "records": records,
            "unfixed_calls": unfixed,
            "rounds": state.get("rounds", 0) + 1,
        }

    async def compose_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        records = [ToolRecord.model_validate(r) for r in state.get("records") or []]
        kwargs = {"company_id": ctx.company_id, "as_of": ctx.as_of, "trace_id": ctx.trace_id}
        if not records and state.get("limit_hit"):
            # Every round was spent on calls the model got wrong: the search was cut short, not empty.
            return {"envelope": _dump(outcomes.step_limit(**kwargs))}
        if not records:
            emit("compose", "No evidence was gathered", data={"records": 0})
            return {"envelope": _dump(outcomes.clarification(None, [], **kwargs))}
        plan_intent = (state.get("plan") or {}).get("intent") or ""
        extras: dict[str, Any] | None = None
        try:
            with _repo(ctx) as repo:
                composition = compose(
                    records, resolve=repo.resolve, device_label=_label_lookup(repo)
                )
                if composition.outcome == "answer" and plan_intent != "advise":
                    extras = _list_suggestions(ctx, composition, records, repo.resolve)
            if state.get("limit_hit"):
                if composition.outcome in ("no_match", "insufficient_data"):
                    # Nothing was found and the search was cut short: say that, not "nothing matched".
                    emit(
                        "compose",
                        "Round limit reached without evidence",
                        data={"records": len(records)},
                    )
                    return {"envelope": _dump(outcomes.step_limit(**kwargs))}
                if composition.outcome == "answer":
                    composition.notes.append(texts.ROUND_LIMIT_NOTE)
            if (state.get("incomplete") or state.get("unfixed_calls")) and (
                composition.outcome in ("answer", "no_match", "insufficient_data")
                and texts.ROUND_LIMIT_NOTE not in composition.notes  # that note already says it
            ):
                composition.notes.append(texts.INCOMPLETE_NOTE)
            draft = composition.to_envelope(
                company_id=ctx.company_id,
                as_of=ctx.as_of,
                trace_id=ctx.trace_id,
                company_name=ctx.company_name,
            )
        except Exception as exc:  # a composer or contract bug must not become a wrong answer
            emit("error", "Composing the answer failed", data={"error": type(exc).__name__})
            return _failure(ctx)
        fingerprint: str | None = None
        if draft.outcome == "answer":
            fingerprint = turn_fingerprint(plan_intent, records, ctx.as_of)
            earlier = earlier_turn(state.get("history"), fingerprint)
            if earlier is not None:
                draft = draft.model_copy(update={"repeat_of": earlier})
        emit(
            "compose",
            "Composed the answer from tool records",
            data={
                "outcome": draft.outcome,
                "claims": len(draft.claims),
                "evidence": len(draft.evidence),
                "records": len(records),
                **({"repeat": True} if draft.repeat_of else {}),
            },
        )
        if draft.outcome == "tool_error" or (
            draft.outcome == "denied" and draft.reason_code not in REPLY_REASONS
        ):
            return {"envelope": _dump(draft)}
        return {"draft": _dump(draft), "extras": extras, "fingerprint": fingerprint}

    async def talk_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        draft = AnswerEnvelope.model_validate(state["draft"])
        if not _talks(draft):
            return {"prose": None}
        held = memory_module.load(state.get("memory"))
        started = time.perf_counter()
        try:
            reply = await llm.reply(
                talk_messages(
                    state["question"],
                    talk_facts(draft, state.get("extras")),
                    memory_module.render(held),
                    texts.CAPABILITY_CARD,
                    _repeat_hint(draft, held),
                )
            )
        except Exception as exc:  # the answer is complete without a reply
            emit(
                "explain",
                "Reply unavailable",
                data={"ok": False, "error": type(exc).__name__},
            )
            return {"prose": None}
        emit(
            "explain",
            "Model wrote a reply",
            data={"ok": True},
            latency_ms=_ms(started),
        )
        return {"prose": reply.model_dump(mode="json")}

    async def verify_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        draft = AnswerEnvelope.model_validate(state["draft"])
        extras = state.get("extras")
        merged: AnswerEnvelope | None = None
        with _repo(ctx) as repo:
            problems = verify_evidence(draft, repo.resolve)
            if extras and not problems:
                # The suggestions' own evidence is checked like the rest; if it fails, the answer is
                # shown without suggestions rather than not at all.
                candidate = _with_extras(draft, extras)
                if candidate is not None and not verify_evidence(candidate, repo.resolve):
                    merged = candidate
        if problems:
            emit("verify", "Evidence failed verification", data={"problems": problems})
            return _failure(ctx)
        prose = state.get("prose") or {}
        verdict = verify_prose(
            composition_of(draft),
            ctx.as_of,
            explanation=prose.get("text"),
            memory=memory_module.memory_facts(memory_module.load(state.get("memory"))),
            suggestions=extra_suggestions(extras),
        )
        emit(
            "verify",
            "Checked the answer against the evidence",
            data={
                "evidence_ok": True,
                "model_prose": bool(prose),
                "explanation_shown": verdict.explanation is not None,
                "reply_dropped": dropped_count(verdict.problems),
                "reply_chars": len(verdict.explanation or ""),
                "problems": list(verdict.problems),
            },
        )
        # The checked reply goes in `reply`. `explanation` carries the same text for the outcomes that may have one.
        update: dict[str, Any] = {"headline": verdict.headline, "reply": verdict.explanation or ""}
        if draft.outcome not in ("denied", "unsupported", "tool_error"):
            update["explanation"] = verdict.explanation
        final = (merged or draft).model_copy(update=update)
        return {"envelope": _dump(AnswerEnvelope.model_validate(final.model_dump()))}

    async def respond_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        envelope = AnswerEnvelope.model_validate(state["envelope"])
        emit(
            "respond",
            "Answer ready",
            data={
                "outcome": envelope.outcome,
                "reason_code": envelope.reason_code,
                "claims": len(envelope.claims),
                "evidence": len(envelope.evidence),
            },
        )
        held = memory_module.remember_turn(
            memory_module.load(state.get("memory")),
            envelope,
            state["question"],
            envelope.trace_id,
        )
        envelope = envelope.model_copy(update={"notes_count": len(held.notes)})
        return {
            "memory": memory_module.dump(held),
            "history": next_history(
                state.get("history"),
                state["question"],
                envelope,
                state.get("fingerprint") if envelope.outcome == "answer" else None,
            ),
            "focus": next_focus(state.get("focus"), envelope),
            "envelope": _dump(envelope),
        }

    def after_plan(state: TurnState) -> str:
        if state.get("envelope"):
            return "respond"
        if state.get("draft"):
            return "talk"  # a chat turn, or a refusal that code declined for lack of data
        plan = state.get("plan") or {}
        # An advise turn always resolves its devices first: a named or pointed-at device, else the last answer's.
        return "reference" if plan.get("reference") or plan.get("intent") == "advise" else "act"

    def after_reference(state: TurnState) -> str:
        return "respond" if state.get("envelope") else "act"

    def after_act(state: TurnState) -> str:
        if state.get("envelope"):
            return "respond"
        return "tools" if state.get("pending_calls") else "compose"

    def after_compose(state: TurnState) -> str:
        if state.get("envelope"):
            return "respond"
        return "talk"

    graph = StateGraph(TurnState, context_schema=AgentContext)
    graph.add_node("scope", scope)
    graph.add_node("plan", plan_node)
    graph.add_node("reference", reference_node)
    graph.add_node("act", act_node)
    graph.add_node(
        "tools",
        ToolNode(
            TOOLS,
            messages_key="turn_messages",
            handle_tool_errors=tool_error_text,
            awrap_tool_call=_tool_wrapper,
        ),
    )
    graph.add_node("collect", collect_node)
    graph.add_node("compose", compose_node)
    graph.add_node("talk", talk_node)
    graph.add_node("verify", verify_node)
    graph.add_node("respond", respond_node)

    graph.add_edge(START, "scope")
    graph.add_edge("scope", "plan")
    graph.add_conditional_edges("plan", after_plan, ["respond", "talk", "reference", "act"])
    graph.add_conditional_edges("reference", after_reference, ["respond", "act"])
    graph.add_conditional_edges("act", after_act, ["respond", "tools", "compose"])
    graph.add_edge("tools", "collect")
    graph.add_edge("collect", "act")
    graph.add_conditional_edges("compose", after_compose, ["respond", "talk"])
    graph.add_edge("talk", "verify")
    graph.add_edge("verify", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
