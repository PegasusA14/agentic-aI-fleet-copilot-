"""The agent as an explicit LangGraph: scope -> plan -> (reference) -> act <-> tools -> compose -> explain -> verify -> respond.

Rules the graph enforces, whatever the model says:
  * tenant and actor come from `AgentContext`; no node or tool accepts them from the model;
  * follow-up references are resolved by code against the lists last shown, and the resolved device
    ids are injected into the tool calls, replacing any ids the model supplied;
  * tool rounds, calls per round and retries are bounded;
  * claims, evidence and numbers come from the composer; model prose is shown only if it verifies.
Nodes are small and take what they need from state; `llm` is the only model dependency.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime

from fleet_copilot import policy
from fleet_copilot.agent import outcomes, texts
from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.focus import (
    NeedsClarification,
    NotFoundRef,
    Resolved,
    focus_summary,
    resolve_reference,
)
from fleet_copilot.agent.llm import (
    ContentFiltered,
    LLMGateway,
    Plan,
    Reference,
    plan_problems,
)
from fleet_copilot.agent.prompts import (
    act_messages,
    explain_messages,
    plan_messages,
    plan_retry_message,
)
from fleet_copilot.agent.records import ToolRecord, record_from_message, redact_tool_calls
from fleet_copilot.agent.state import TurnState, next_focus, next_history
from fleet_copilot.agent.tools import (
    DEVICE_FILTER_TOOLS,
    INVALID_INPUT_PREFIX,
    TOOLS,
    tool_error_text,
)
from fleet_copilot.agent.trace import emit, trace_args
from fleet_copilot.agent.verify import (
    composition_of,
    verify_clarification,
    verify_evidence,
    verify_prose,
)
from fleet_copilot.contracts import AnswerEnvelope, EntityList
from fleet_copilot.data.loader import connect
from fleet_copilot.repository import FleetRepository, TenantContext

# A turn is at most MAX_TOOL_ROUNDS x (act, tools, collect) plus the fixed nodes; leave headroom.
RECURSION_LIMIT = 4 * policy.MAX_TOOL_ROUNDS + 20

ToolCall = dict[str, Any]

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


def _ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _dump(envelope: AnswerEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json")


def _failure(ctx: AgentContext) -> dict[str, Any]:
    return {
        "envelope": _dump(
            outcomes.tool_failure(company_id=ctx.company_id, as_of=ctx.as_of, trace_id=ctx.trace_id)
        )
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
        data={"intent": "unsupported", "fallback": "off_topic", "problems": problems},
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


def inject_focus(name: str, args: ToolCall, focus_ids: list[str] | None) -> ToolCall:
    """Replace model-supplied device ids with the ids code resolved from the user's follow-up."""
    if not focus_ids:
        return dict(args)
    if name in DEVICE_FILTER_TOOLS:
        return {**args, "device_ids": list(focus_ids)}
    # device_history and propose_action take one device. With several resolved ids there is nothing to inject, so the
    # model's own `device_id` stays; it is still looked up through the tenant-scoped repository, so a
    # foreign or unknown id gets the uniform not-found.
    if name in ("device_history", "propose_action") and len(focus_ids) == 1:
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


PROPOSE_ONLY_FOR_ACTIONS = "propose_action is only for action requests"
ONE_PROPOSAL_PER_TURN = "one proposal per turn"
RECOMMEND_ONLY_FOR_ADVICE = "recommend_actions is only for advice requests"
ONE_RECOMMENDATION_PER_TURN = "one recommendation per turn"


def _propose_blocked(name: str, state: Any) -> str | None:
    """Why this `propose_action` call must not run (fixed text for the model), or None.

    Only a propose plan may create a proposal, and a turn holds at most one: a call after a proposal or
    refusal is rejected whatever the model says.
    """
    if name != "propose_action":
        return None
    if (state.get("plan") or {}).get("intent") != "propose":
        return PROPOSE_ONLY_FOR_ACTIONS
    if any(r.get("tool") == "propose_action" for r in state.get("records") or []):
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
    blocked = _propose_blocked(name, request.state) or _recommend_blocked(name, request.state)
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
    args = inject_focus(name, call["args"], request.state.get("focus_ids"))
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


def build_graph(llm: LLMGateway, checkpointer: Any):
    """Compile the agent graph around one model gateway and one checkpointer."""

    async def scope(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        emit("scope", "Turn started", data={"company_id": runtime.context.company_id})
        return {
            "turn_messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
            "plan": None,
            "focus_ids": None,
            "pending_calls": False,
            "rounds": 0,
            "limit_hit": False,
            "incomplete": False,
            "unfixed_calls": [],
            "records": [],
            "draft": None,
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
            return _failure(ctx)
        emit(
            "plan",
            "Planned the turn",
            data={
                "intent": plan.intent,
                "window_days": plan.window_days,
                "has_reference": plan.reference is not None,
                "reference_kind": plan.reference.kind if plan.reference else None,
                **({"repaired": True} if repaired else {}),
                **({"missing_topic": plan.missing_topic} if plan.missing_topic else {}),
            },
            latency_ms=_ms(started),
        )
        update: dict[str, Any] = {"plan": plan.model_dump(mode="json")}
        kwargs = {"company_id": ctx.company_id, "as_of": ctx.as_of, "trace_id": ctx.trace_id}
        if plan.intent == "clarify":
            checked = verify_clarification(
                plan.clarification_question or "", list(plan.clarification_options)
            )
            question, options = checked if checked else (None, [])
            update["envelope"] = _dump(outcomes.clarification(question, options, **kwargs))
        elif plan.intent in ("refuse", "unsupported") and plan.refusal_reason:
            update["envelope"] = _dump(
                outcomes.refusal(plan.refusal_reason, missing_topic=plan.missing_topic, **kwargs)
            )
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
                advised = plan.model_copy(update={"intent": "advise"})
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

    async def act_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        plan = Plan.model_validate(state["plan"])
        if plan.intent == "advise":
            # Advice never depends on the model's choice of tool: code makes the one call, then stops.
            if any(r.get("tool") == "recommend_actions" for r in state.get("records") or []):
                return {"pending_calls": False}
            args: dict[str, Any] = {"device_ids": list(state.get("focus_ids") or [])}
            if plan.action_type:
                args["action_type"] = plan.action_type
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
        try:
            message = await llm.act(
                act_messages(
                    question=state["question"],
                    as_of=ctx.as_of,
                    plan_summary=plan.summary,
                    window_days=plan.window_days,
                    resolved_ids=state.get("focus_ids"),
                    history=state.get("history") or [],
                    turn_messages=state.get("turn_messages") or [],
                )
            )
        except ContentFiltered as exc:
            return _provider_refused(ctx, exc, "act")
        except Exception as exc:
            emit("error", "Gathering evidence failed", data={"error": type(exc).__name__})
            return _failure(ctx)
        calls = list(message.tool_calls)
        if not calls:
            return {"pending_calls": False}
        if state.get("rounds", 0) >= policy.MAX_TOOL_ROUNDS:
            emit(
                "compose",
                "Stopped at the tool-round limit",
                data={"max_rounds": policy.MAX_TOOL_ROUNDS},
                latency_ms=_ms(started),
            )
            return {"pending_calls": False, "limit_hit": True}
        dropped_any = False
        proposals = [i for i, call in enumerate(calls) if call["name"] == "propose_action"]
        if len(proposals) > 1:
            # One proposal per turn: only the first request is kept.
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
            args = inject_focus(call["name"], call["args"], state.get("focus_ids"))
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
        try:
            with _repo(ctx) as repo:
                composition = compose(records, resolve=repo.resolve)
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
        emit(
            "compose",
            "Composed the answer from tool records",
            data={
                "outcome": draft.outcome,
                "claims": len(draft.claims),
                "evidence": len(draft.evidence),
                "records": len(records),
            },
        )
        if draft.outcome in ("denied", "tool_error"):
            return {"envelope": _dump(draft)}
        return {"draft": _dump(draft)}

    async def explain_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        draft = AnswerEnvelope.model_validate(state["draft"])
        if draft.outcome != "answer":
            return {"prose": None}
        started = time.perf_counter()
        try:
            explanation = await llm.explain(
                explain_messages(question=state["question"], facts=composition_of(draft).facts())
            )
        except Exception as exc:  # the answer is complete without prose
            emit(
                "explain",
                "Explanation unavailable",
                data={"ok": False, "error": type(exc).__name__},
            )
            return {"prose": None}
        emit(
            "explain",
            "Model wrote a headline and explanation",
            data={"ok": True},
            latency_ms=_ms(started),
        )
        return {"prose": explanation.model_dump(mode="json")}

    async def verify_node(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        ctx = runtime.context
        draft = AnswerEnvelope.model_validate(state["draft"])
        with _repo(ctx) as repo:
            problems = verify_evidence(draft, repo.resolve)
        if problems:
            emit("verify", "Evidence failed verification", data={"problems": problems})
            return _failure(ctx)
        prose = state.get("prose") or {}
        verdict = verify_prose(
            composition_of(draft),
            ctx.as_of,
            headline=prose.get("headline"),
            explanation=prose.get("explanation"),
        )
        emit(
            "verify",
            "Checked the answer against the evidence",
            data={
                "evidence_ok": True,
                "model_prose": bool(prose),
                "headline_from_model": bool(prose) and verdict.headline != draft.headline,
                "explanation_shown": verdict.explanation is not None,
                "problems": list(verdict.problems),
            },
        )
        final = draft.model_copy(
            update={"headline": verdict.headline, "explanation": verdict.explanation}
        )
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
        return {
            "history": next_history(state.get("history"), state["question"], envelope),
            "focus": next_focus(state.get("focus"), envelope),
            "envelope": _dump(envelope),
        }

    def after_plan(state: TurnState) -> str:
        if state.get("envelope"):
            return "respond"
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
        return "respond" if state.get("envelope") else "explain"

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
    graph.add_node("explain", explain_node)
    graph.add_node("verify", verify_node)
    graph.add_node("respond", respond_node)

    graph.add_edge(START, "scope")
    graph.add_edge("scope", "plan")
    graph.add_conditional_edges("plan", after_plan, ["respond", "reference", "act"])
    graph.add_conditional_edges("reference", after_reference, ["respond", "act"])
    graph.add_conditional_edges("act", after_act, ["respond", "tools", "compose"])
    graph.add_edge("tools", "collect")
    graph.add_edge("collect", "act")
    graph.add_conditional_edges("compose", after_compose, ["respond", "explain"])
    graph.add_edge("explain", "verify")
    graph.add_edge("verify", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
