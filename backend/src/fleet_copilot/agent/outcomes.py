"""Envelopes the model never words: refusals, clarifications, failures and the step limit.

Every function here returns a complete, validated `AnswerEnvelope` built from fixed text, so a
prompt-injected or confused model cannot change what the user reads for these outcomes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fleet_copilot.agent import texts
from fleet_copilot.agent.llm import RefusalReason
from fleet_copilot.contracts import AnswerEnvelope, Clarification, ReasonCode, ScopeInfo

_REFUSALS: dict[RefusalReason, tuple[str, ReasonCode, str]] = {
    # cross_tenant reads exactly like a nonexistent record: the answer must not confirm another company exists.
    "cross_tenant": ("denied", "not_found", texts.NOT_FOUND),
    "prompt_injection": ("denied", "prompt_injection", texts.PROMPT_INJECTION),
    "off_topic": ("unsupported", "off_topic", texts.OFF_TOPIC),
    "unsupported_action": ("unsupported", "unsupported_action", texts.UNSUPPORTED_ACTION),
}


def _scope(company_id: str, as_of: datetime) -> ScopeInfo:
    return ScopeInfo(company_id=company_id, as_of=as_of)


def refusal(
    reason: RefusalReason, *, company_id: str, as_of: datetime, trace_id: str
) -> AnswerEnvelope:
    outcome, reason_code, headline = _REFUSALS[reason]
    return AnswerEnvelope(
        outcome=outcome,  # type: ignore[arg-type]
        trace_id=trace_id,
        headline=headline,
        scope=_scope(company_id, as_of),
        reason_code=reason_code,
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
    reason: Literal["not_eligible", "insufficient_evidence"],
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
