"""Envelopes the model never words: refusals, clarifications, failures and the step limit.

Every function here returns a complete, validated `AnswerEnvelope` built from fixed text, so a
prompt-injected or confused model cannot change what the user reads for these outcomes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fleet_copilot.agent import texts
from fleet_copilot.agent.llm import MissingTopic, RefusalReason
from fleet_copilot.contracts import (
    Alternative,
    AnswerEnvelope,
    Clarification,
    ReasonCode,
    ScopeInfo,
)

_REFUSALS: dict[RefusalReason, tuple[str, ReasonCode, str]] = {
    # cross_tenant reads exactly like a nonexistent record: the answer must not confirm another company exists.
    "cross_tenant": ("denied", "not_found", texts.NOT_FOUND),
    "prompt_injection": ("denied", "prompt_injection", texts.PROMPT_INJECTION),
    "off_topic": ("unsupported", "off_topic", texts.OFF_TOPIC),
    "unsupported_action": ("unsupported", "unsupported_action", texts.UNSUPPORTED_ACTION),
    "data_unavailable": ("unsupported", "data_unavailable", texts.MISSING_TOPIC_TEXT["other"]),
}


def _scope(company_id: str, as_of: datetime) -> ScopeInfo:
    return ScopeInfo(company_id=company_id, as_of=as_of)


def refusal(
    reason: RefusalReason,
    *,
    company_id: str,
    as_of: datetime,
    trace_id: str,
    missing_topic: MissingTopic | None = None,
) -> AnswerEnvelope:
    """A refusal in fixed words. Off-topic and missing-data refusals also say what can be answered and offer
    code-written alternatives; `missing_topic` picks the wording for data the telemetry does not hold."""
    outcome, reason_code, headline = _REFUSALS[reason]
    notes: list[str] = []
    alternatives: list[dict[str, str]] = []
    if reason == "data_unavailable":
        headline = texts.MISSING_TOPIC_TEXT[missing_topic or "other"]
    if reason in ("off_topic", "data_unavailable"):
        notes = [texts.CAPABILITY_NOTE]
        alternatives = texts.alternatives_for(
            missing_topic if reason == "data_unavailable" else None
        )
    return AnswerEnvelope(
        outcome=outcome,  # type: ignore[arg-type]
        trace_id=trace_id,
        headline=headline,
        scope=_scope(company_id, as_of),
        reason_code=reason_code,
        notes=notes,
        alternatives=[Alternative(**a) for a in alternatives],
    )


def tool_failure(*, company_id: str, as_of: datetime, trace_id: str) -> AnswerEnvelope:
    return AnswerEnvelope(
        outcome="tool_error",
        trace_id=trace_id,
        headline=texts.TOOL_FAILED,
        scope=_scope(company_id, as_of),
        reason_code="tool_failure",
    )


def step_limit(*, company_id: str, as_of: datetime, trace_id: str) -> AnswerEnvelope:
    return AnswerEnvelope(
        outcome="insufficient_data",
        trace_id=trace_id,
        headline=texts.STEP_LIMIT,
        scope=_scope(company_id, as_of),
        reason_code="step_limit",
    )


def clarification(
    question: str | None,
    options: list[str],
    *,
    company_id: str,
    as_of: datetime,
    trace_id: str,
) -> AnswerEnvelope:
    """Ask for one missing detail. `question` and `options` must already be code-written or verified."""
    return AnswerEnvelope(
        outcome="clarification",
        trace_id=trace_id,
        headline=texts.CLARIFICATION_HEADLINE,
        scope=_scope(company_id, as_of),
        clarification=Clarification(
            question=question or texts.DEFAULT_CLARIFICATION, options=options
        ),
    )


def action_refusal(
    reason: Literal["not_eligible", "insufficient_evidence", "already_done", "too_many_pending"],
    detail: str,
    *,
    company_id: str,
    as_of: datetime,
    trace_id: str,
) -> AnswerEnvelope:
    """A proposal that code declined. `detail` is the code-written reason, never model text."""
    return AnswerEnvelope(
        outcome="denied",
        trace_id=trace_id,
        headline=texts.ACTION_REFUSAL_HEADLINES[reason],
        scope=_scope(company_id, as_of),
        reason_code=reason,
        notes=[detail],
    )
