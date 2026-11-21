from datetime import UTC, datetime

import pytest

from fleet_copilot.agent import outcomes, texts
from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.contracts import AnswerEnvelope

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
KW = {"company_id": "acme-001", "as_of": AS_OF, "trace_id": "t-9"}


@pytest.mark.parametrize(
    ("reason", "outcome", "code", "headline"),
    [
        ("cross_tenant", "denied", "not_found", texts.NOT_FOUND),
        ("prompt_injection", "denied", "prompt_injection", texts.PROMPT_INJECTION),
        ("off_topic", "unsupported", "off_topic", texts.OFF_TOPIC),
        ("unsupported_action", "unsupported", "unsupported_action", texts.UNSUPPORTED_ACTION),
    ],
)
def test_refusals_use_fixed_text_and_carry_nothing_else(reason, outcome, code, headline):
    envelope = outcomes.refusal(reason, **KW)
    assert (envelope.outcome, envelope.reason_code, envelope.headline) == (outcome, code, headline)
    assert not envelope.claims and not envelope.evidence and envelope.explanation is None
    assert envelope.scope.company_id == "acme-001" and envelope.trace_id == "t-9"


def test_a_cross_tenant_refusal_reads_exactly_like_a_missing_device():
    missing = compose(
        [ToolRecord(tool="find_devices", args={}, error="not_found")], resolve=lambda ref: None
    ).to_envelope(company_id="acme-001", as_of=AS_OF, trace_id="t-9")
    foreign = outcomes.refusal("cross_tenant", **KW)
    assert (foreign.outcome, foreign.reason_code, foreign.headline) == (
        missing.outcome,
        missing.reason_code,
        missing.headline,
    )
    assert foreign.model_dump() == missing.model_dump()


def test_tool_failure_and_step_limit():
    failure = outcomes.tool_failure(**KW)
    assert (failure.outcome, failure.reason_code) == ("tool_error", "tool_failure")
    assert failure.headline == texts.TOOL_FAILED
    limit = outcomes.step_limit(**KW)
    assert (limit.outcome, limit.reason_code) == ("insufficient_data", "step_limit")
    assert limit.headline == texts.STEP_LIMIT and not limit.claims


def test_clarification_carries_question_and_options_and_falls_back_to_a_default():
    asked = outcomes.clarification("Which check?", ["screen_lock", "firewall"], **KW)
    assert asked.outcome == "clarification" and asked.reason_code is None
    assert asked.clarification is not None
    assert asked.clarification.question == "Which check?"
    assert asked.clarification.options == ["screen_lock", "firewall"]
    assert asked.headline == texts.CLARIFICATION_HEADLINE
    default = outcomes.clarification(None, [], **KW)
    assert default.clarification is not None
    assert default.clarification.question == texts.DEFAULT_CLARIFICATION


@pytest.mark.parametrize(
    ("reason", "headline"),
    [
        ("not_eligible", texts.ACTION_NOT_ELIGIBLE),
        ("insufficient_evidence", texts.ACTION_INSUFFICIENT_EVIDENCE),
    ],
)
def test_action_refusal_is_denied_with_fixed_headline_and_the_code_written_note(reason, headline):
    envelope = outcomes.action_refusal(reason, "Disk is at 40% free.", **KW)
    assert (envelope.outcome, envelope.reason_code, envelope.headline) == (
        "denied",
        reason,
        headline,
    )
    assert envelope.notes == ["Disk is at 40% free."]
    assert not envelope.claims and not envelope.evidence and envelope.explanation is None
    assert envelope.proposal is None and envelope.trace_id == "t-9"
    assert AnswerEnvelope.model_validate(envelope.model_dump()) == envelope


def test_an_off_topic_refusal_carries_the_capability_line_and_fixed_alternatives():
    envelope = outcomes.refusal("off_topic", **KW)
    assert envelope.notes == [texts.CAPABILITY_NOTE]
    assert [(a.label, a.request) for a in envelope.alternatives] == [
        ("Fleet health summary", "Give me a fleet health summary"),
        ("Failing compliance checks", "Which devices are failing compliance checks?"),
        ("Devices low on disk", "Which devices are low on disk space?"),
    ]


def test_a_missing_topic_refusal_is_worded_by_the_fixed_table():
    envelope = outcomes.refusal("data_unavailable", missing_topic="network", **KW)
    assert (envelope.outcome, envelope.reason_code) == ("unsupported", "data_unavailable")
    assert envelope.headline == "Wi-Fi and network quality aren't something the fleet data records."
    assert envelope.notes == [
        "I can answer from hardware, OS, memory, disk, battery, installed software and compliance data."
    ]
    assert not envelope.claims and envelope.explanation is None


def test_performance_adds_the_memory_pressure_alternative_and_stays_within_four():
    labels = [
        a.label
        for a in outcomes.refusal(
            "data_unavailable", missing_topic="performance", **KW
        ).alternatives
    ]
    assert labels[-1] == "Memory pressure" and len(labels) == 4
    request = (
        outcomes.refusal("data_unavailable", missing_topic="performance", **KW)
        .alternatives[-1]
        .request
    )
    assert request == "Which devices are consistently RAM-constrained?"


def test_a_data_unavailable_refusal_without_a_topic_uses_the_other_text():
    envelope = outcomes.refusal("data_unavailable", **KW)
    assert envelope.headline == texts.MISSING_TOPIC_TEXT["other"]


def test_only_off_topic_and_missing_data_refusals_offer_alternatives():
    for reason in ("cross_tenant", "prompt_injection", "unsupported_action"):
        envelope = outcomes.refusal(reason, **KW)
        assert envelope.alternatives == [] and envelope.notes == []


def test_every_alternative_request_is_a_short_plain_question():
    for topic in texts.MISSING_TOPIC_TEXT:
        for alt in texts.alternatives_for(topic):
            assert 0 < len(alt["label"]) <= 40 and 0 < len(alt["request"]) <= 120
