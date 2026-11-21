"""The planner never crashes: one corrective retry, then a fixed off-topic refusal; missing data topics get code-written replies."""

import pytest
from graph_support import ACME_ADMIN, answer_plan, explanation, kinds, make_service
from pydantic import ValidationError

from fleet_copilot.agent import texts
from fleet_copilot.agent.llm import (
    ContentFiltered,
    Plan,
    PlanRejected,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)


async def ask(service, question):
    return await service.run_turn(ACME_ADMIN, "c1", question)


def bad_plan_error() -> ValidationError:
    try:
        Plan.model_validate({"intent": "bogus", "summary": "s", "window_days": 0})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


async def test_a_malformed_plan_is_retried_once_and_the_valid_plan_proceeds(real_db_path):
    llm = ScriptedLLM(
        plans=[bad_plan_error(), answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    question = "which devices are low on disk? my secret-token is hunter2"
    result = await ask(make_service(real_db_path, llm), question)
    assert result.envelope.outcome == "answer"
    assert llm.remaining == {"plan": 0, "act": 0, "explain": 0}
    first, second = llm.calls_of("plan")
    assert len(second.messages) == len(first.messages) + 1
    assert second.messages[:-1] == first.messages
    corrective = str(second.messages[-1].content)
    assert "intent:" in corrective and "window_days:" in corrective
    assert "bogus" not in corrective and "hunter2" not in corrective
    retry = next(e for e in result.trace if e.kind == "retry")
    assert retry.data["problems"]
    assert "error" not in kinds(result)
    planned = next(e for e in result.trace if e.kind == "plan")
    assert planned.data["repaired"] is True


async def test_a_plan_malformed_twice_is_a_fixed_off_topic_refusal_not_a_tool_error(real_db_path):
    llm = ScriptedLLM(plans=[bad_plan_error(), bad_plan_error()])
    result = await ask(make_service(real_db_path, llm), "What's the weather in Austin?")
    envelope = result.envelope
    assert (envelope.outcome, envelope.reason_code) == ("unsupported", "off_topic")
    assert envelope.headline == texts.OFF_TOPIC
    assert envelope.alternatives and not envelope.claims
    assert len(llm.calls_of("plan")) == 2
    assert "error" not in kinds(result)
    assert kinds(result).count("retry") == 1
    planned = [e for e in result.trace if e.kind == "plan"]
    assert planned[-1].data["fallback"] == "off_topic"
    assert not llm.calls_of("act") and not llm.calls_of("explain")


async def test_a_plan_that_fails_then_hits_an_outage_keeps_the_outage_handling(real_db_path):
    llm = ScriptedLLM(plans=[bad_plan_error(), RuntimeError("endpoint down")])
    result = await ask(make_service(real_db_path, llm), "anything")
    assert (result.envelope.outcome, result.envelope.reason_code) == ("tool_error", "tool_failure")


async def test_a_plan_that_fails_then_hits_the_provider_filter_is_the_filter_refusal(real_db_path):
    llm = ScriptedLLM(plans=[bad_plan_error(), ContentFiltered("jailbreak")])
    result = await ask(make_service(real_db_path, llm), "anything")
    assert (result.envelope.outcome, result.envelope.reason_code) == ("denied", "prompt_injection")


async def test_a_plan_the_gateway_rejects_is_retried_like_a_validation_error(real_db_path):
    llm = ScriptedLLM(plans=[PlanRejected("no plan"), PlanRejected("no plan")])
    result = await ask(make_service(real_db_path, llm), "weather?")
    assert (result.envelope.outcome, result.envelope.reason_code) == ("unsupported", "off_topic")
    assert len(llm.calls_of("plan")) == 2


async def test_an_outage_at_the_first_attempt_is_not_retried(real_db_path):
    llm = ScriptedLLM(plans=[RuntimeError("endpoint down")])
    result = await ask(make_service(real_db_path, llm), "anything")
    assert result.envelope.outcome == "tool_error"
    assert len(llm.calls_of("plan")) == 1


async def test_a_refuse_plan_with_an_off_topic_reason_is_the_off_topic_refusal(real_db_path):
    plan = Plan.model_validate({"intent": "refuse", "summary": "s", "refusal_reason": "off_topic"})
    result = await ask(make_service(real_db_path, ScriptedLLM(plans=[plan])), "weather in Austin?")
    envelope = result.envelope
    assert (envelope.outcome, envelope.reason_code) == ("unsupported", "off_topic")
    assert envelope.headline == texts.OFF_TOPIC
    assert envelope.notes == [texts.CAPABILITY_NOTE]
    assert len(envelope.alternatives) == 3


@pytest.mark.parametrize(
    ("topic", "text"),
    [
        ("network", "Wi-Fi and network quality aren't something the fleet data records."),
        ("location", texts.MISSING_TOPIC_TEXT["location"]),
        ("performance", texts.MISSING_TOPIC_TEXT["performance"]),
        ("user_activity", texts.MISSING_TOPIC_TEXT["user_activity"]),
        ("peripherals", texts.MISSING_TOPIC_TEXT["peripherals"]),
        ("other", texts.MISSING_TOPIC_TEXT["other"]),
    ],
)
async def test_a_missing_topic_gets_the_fixed_text_the_capability_line_and_alternatives(
    real_db_path, topic, text
):
    plan = Plan(intent="unsupported", summary="s", missing_topic=topic)
    llm = ScriptedLLM(plans=[plan])
    result = await ask(make_service(real_db_path, llm), "Which devices have a weak Wi-Fi signal?")
    envelope = result.envelope
    assert (envelope.outcome, envelope.reason_code) == ("unsupported", "data_unavailable")
    assert envelope.headline == text
    assert envelope.notes == [texts.CAPABILITY_NOTE]
    assert 3 <= len(envelope.alternatives) <= 4
    assert not envelope.claims and not envelope.evidence and envelope.explanation is None
    labels = [a.label for a in envelope.alternatives]
    assert ("Memory pressure" in labels) == (topic == "performance")
    assert not llm.calls_of("act") and not llm.calls_of("explain")


async def test_the_model_cannot_word_a_missing_topic_refusal(real_db_path):
    plan = Plan(
        intent="unsupported",
        summary="Wi-Fi is fine, your devices are all healthy!",
        missing_topic="network",
    )
    result = await ask(make_service(real_db_path, ScriptedLLM(plans=[plan])), "wifi?")
    assert "healthy" not in result.envelope.model_dump_json()
