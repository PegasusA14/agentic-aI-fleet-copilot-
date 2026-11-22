import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from fleet_copilot.agent.llm import (
    Plan,
    PlanRejected,
    Reference,
    Reply,
    ScriptedLLM,
    ScriptExhausted,
    final_message,
    plan_problems,
    tool_calls_message,
)

# ---- schemas ---------------------------------------------------------------------------------------


def test_a_plain_answer_plan_is_valid():
    plan = Plan(intent="answer", summary="Find low disk devices")
    assert plan.reference is None and plan.window_days is None and plan.clarification_options == []


@pytest.mark.parametrize(
    "intent", ["answer", "clarify", "refuse", "propose", "advise", "unsupported"]
)
def test_any_plan_whose_reason_is_off_topic_becomes_unsupported_off_topic(intent):
    plan = Plan(intent=intent, summary="s", refusal_reason="off_topic")
    assert (plan.intent, plan.refusal_reason, plan.missing_topic) == (
        "unsupported",
        "off_topic",
        None,
    )


@pytest.mark.parametrize("intent", ["answer", "clarify", "propose", "advise", "unsupported"])
@pytest.mark.parametrize("reason", ["cross_tenant", "prompt_injection"])
def test_a_plan_naming_a_security_reason_is_always_a_refusal(intent, reason):
    plan = Plan(intent=intent, summary="s", refusal_reason=reason)
    assert (plan.intent, plan.refusal_reason) == ("refuse", reason)


@pytest.mark.parametrize("intent", ["answer", "propose", "advise", "refuse"])
def test_a_plan_naming_an_unsupported_action_is_never_an_answer(intent):
    plan = Plan(intent=intent, summary="s", refusal_reason="unsupported_action")
    assert (plan.intent, plan.refusal_reason) == ("unsupported", "unsupported_action")


@pytest.mark.parametrize("intent", ["off_topic", "Off-Topic", " OFF_TOPIC "])
def test_an_intent_that_names_the_reason_reads_as_off_topic(intent):
    plan = Plan.model_validate({"intent": intent, "summary": "s"})
    assert (plan.intent, plan.refusal_reason) == ("unsupported", "off_topic")


def test_an_unknown_intent_with_no_reason_is_still_rejected():
    with pytest.raises(ValidationError):
        Plan.model_validate({"intent": "decline", "summary": "s"})


def test_a_missing_topic_makes_the_plan_a_data_unavailable_refusal():
    for intent, reason in (
        ("unsupported", None),
        ("unsupported", "unsupported_action"),
        ("answer", None),
        ("refuse", None),
        ("unsupported", "data_unavailable"),
    ):
        plan = Plan.model_validate(
            {"intent": intent, "summary": "s", "refusal_reason": reason, "missing_topic": "network"}
        )
        assert (plan.intent, plan.refusal_reason, plan.missing_topic) == (
            "unsupported",
            "data_unavailable",
            "network",
        )


def test_data_unavailable_without_a_topic_defaults_to_other():
    plan = Plan(intent="unsupported", summary="s", refusal_reason="data_unavailable")
    assert plan.missing_topic == "other"


def test_an_unknown_missing_topic_reads_as_other():
    plan = Plan.model_validate({"intent": "unsupported", "summary": "s", "missing_topic": "wifi"})
    assert (plan.refusal_reason, plan.missing_topic) == ("data_unavailable", "other")


@pytest.mark.parametrize("reason", ["off_topic", "cross_tenant", "prompt_injection"])
def test_a_stronger_reason_beats_a_missing_topic(reason):
    plan = Plan.model_validate(
        {
            "intent": "unsupported",
            "summary": "s",
            "refusal_reason": reason,
            "missing_topic": "network",
        }
    )
    assert plan.refusal_reason == reason and plan.missing_topic is None


def test_a_blank_missing_topic_is_read_as_unset():
    plan = Plan.model_validate({"intent": "answer", "summary": "s", "missing_topic": " "})
    assert (plan.intent, plan.missing_topic) == ("answer", None)


def test_unknown_fields_are_ignored_not_rejected():
    plan = Plan.model_validate(
        {"intent": "answer", "summary": "s", "confidence": 0.9, "reasoning": "because"}
    )
    assert plan.intent == "answer"
    ref = Reference.model_validate({"kind": "last", "note": "x"})
    assert ref.kind == "last"


def test_null_and_oversized_fields_are_normalised():
    plan = Plan.model_validate(
        {
            "intent": "clarify",
            "summary": None,
            "clarification_question": "Which?",
            "clarification_options": ["a", "b" * 200, "", None, "c", "d", "e", "f", "g", "h"],
        }
    )
    assert plan.summary == ""
    assert plan.clarification_options == ["a", "c", "d", "e", "f", "g"]
    assert len(Plan.model_validate({"intent": "answer", "summary": "x" * 400}).summary) == 300


def test_an_advise_plan_is_valid_and_carries_references_like_an_answer():
    plan = Plan(intent="advise", summary="Recommend next steps for the listed devices")
    assert plan.intent == "advise" and plan.refusal_reason is None
    named = Plan(
        intent="advise",
        summary="s",
        reference=Reference(kind="named", device_ref="acme-thinkpad-8"),
    )
    assert named.reference is not None and named.reference.kind == "named"


def test_a_plan_missing_its_refusal_reason_is_repaired_not_rejected():
    assert Plan(intent="unsupported", summary="s").refusal_reason == "unsupported_action"
    assert Plan(intent="refuse", summary="s").refusal_reason == "prompt_injection"
    assert Plan(intent="unsupported", summary="s", refusal_reason=None).refusal_reason == (
        "unsupported_action"
    )


def test_a_blank_refusal_reason_is_read_as_missing():
    assert Plan(intent="unsupported", summary="s", refusal_reason="").refusal_reason == (
        "unsupported_action"
    )
    assert Plan(intent="refuse", summary="s", refusal_reason="  ").refusal_reason == (
        "prompt_injection"
    )


def test_a_clarify_plan_without_a_question_keeps_its_intent_and_leaves_the_question_unset():
    for question in (None, "", "   "):
        plan = Plan(intent="clarify", summary="s", clarification_question=question)
        assert plan.intent == "clarify" and plan.clarification_question is None
    assert Plan(intent="clarify", summary="s").clarification_question is None


def test_repair_leaves_a_valid_plan_untouched():
    plan = Plan(
        intent="refuse", summary="s", refusal_reason="cross_tenant", clarification_question="Q?"
    )
    assert plan.refusal_reason == "cross_tenant" and plan.clarification_question == "Q?"


def test_repair_does_not_invent_a_refusal_for_a_non_refusal_intent():
    for intent in ("answer", "propose", "advise"):
        assert Plan(intent=intent, summary="s").refusal_reason is None


def test_non_mapping_plan_input_is_still_rejected():
    with pytest.raises(ValidationError):
        Plan.model_validate("not a plan")


def test_every_valid_refusal_pairing_is_accepted():
    for intent, reason in (
        ("refuse", "cross_tenant"),
        ("refuse", "prompt_injection"),
        ("unsupported", "off_topic"),
        ("unsupported", "unsupported_action"),
    ):
        assert Plan(intent=intent, summary="s", refusal_reason=reason).refusal_reason == reason


def test_plan_limits_are_enforced():
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", window_days=0)
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", window_days=61)
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", refusal_reason="made_up")
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", missing_topic="network", window_days=0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "ordinal"}, "needs an index"),
        ({"kind": "last", "index": 2}, "only valid for an ordinal"),
        ({"kind": "named"}, "needs device_ref"),
        ({"kind": "all", "device_ref": "MT7PJB7N5LRE"}, "only valid for a named"),
    ],
)
def test_malformed_references_are_rejected(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        Reference(**kwargs)


def test_well_formed_references_round_trip():
    for ref in (
        Reference(kind="ordinal", index=2, list_hint="screen lock"),
        Reference(kind="last"),
        Reference(kind="all"),
        Reference(kind="named", device_ref="acme-macbook-2"),
    ):
        assert Reference.model_validate(ref.model_dump(mode="json")) == ref
    with pytest.raises(ValidationError):
        Reference(kind="ordinal", index=0)


def test_a_reply_has_a_loose_hard_limit_and_verify_enforces_the_real_ones():
    assert Reply(text="Hello").text == "Hello" and Reply().text == ""
    with pytest.raises(ValidationError):
        Reply(text="e" * 1501)


def test_a_reply_reads_a_recorded_explanation_as_its_text():
    # Responses recorded before replies existed carry `headline` and `explanation`: the explanation is the
    # text, and the headline (code writes it) is dropped.
    old = Reply.model_validate(
        {"headline": "Two devices are low", "explanation": "Both are laptops."}
    )
    assert old.text == "Both are laptops."
    assert Reply.model_validate({"headline": "h", "explanation": None}).text == ""
    assert Reply.model_validate({"headline": "h"}).text == ""
    assert Reply.model_validate({"text": "New", "explanation": "ignored"}).text == "New"


# ---- the scripted gateway --------------------------------------------------------------------------


async def test_scripted_llm_plays_responses_in_order_and_records_inputs():
    plans = [Plan(intent="answer", summary="first"), Plan(intent="answer", summary="second")]
    llm = ScriptedLLM(
        plans=plans, acts=[tool_calls_message(("low_disk_devices", {})), final_message()]
    )
    assert (await llm.plan([HumanMessage("a")])).summary == "first"
    assert (await llm.plan([HumanMessage("b")])).summary == "second"
    first = await llm.act([HumanMessage("c")])
    assert first.tool_calls[0]["name"] == "low_disk_devices" and first.tool_calls[0]["args"] == {}
    assert (await llm.act([HumanMessage("d")])).tool_calls == []
    assert [c.kind for c in llm.calls] == ["plan", "plan", "act", "act"]
    assert llm.calls_of("plan")[1].text == "b"
    assert llm.remaining == {"plan": 0, "act": 0, "explain": 0}


async def test_scripted_llm_raises_a_clear_error_when_exhausted():
    llm = ScriptedLLM()
    with pytest.raises(ScriptExhausted, match="no scripted plan response left"):
        await llm.plan([HumanMessage("a")])
    with pytest.raises(ScriptExhausted, match="no scripted explain response left"):
        await llm.explain([HumanMessage("a")])
    assert len(llm.calls) == 2  # the failed calls are still recorded


async def test_a_queued_exception_is_raised_to_simulate_an_outage():
    llm = ScriptedLLM(explains=[RuntimeError("model down"), Reply(text="ok")])
    with pytest.raises(RuntimeError, match="model down"):
        await llm.explain([HumanMessage("a")])
    assert (await llm.reply([HumanMessage("b")])).text == "ok"


def test_tool_call_helpers_build_valid_messages_with_unique_ids():
    message = tool_calls_message(("low_disk_devices", {}), ("trend", {"metric": "disk"}))
    assert isinstance(message, AIMessage)
    assert [c["name"] for c in message.tool_calls] == ["low_disk_devices", "trend"]
    ids = [c["id"] for c in message.tool_calls]
    assert len(set(ids)) == 2
    other = tool_calls_message(("trend", {}))
    assert other.tool_calls[0]["id"] not in ids


def test_a_null_options_list_from_the_model_means_no_options():
    """Live models send `null` for an empty optional list; that must not fail the whole plan."""
    plan = Plan.model_validate(
        {
            "intent": "answer",
            "summary": "Identify devices with low disk space from fleet telemetry.",
            "window_days": None,
            "reference": None,
            "clarification_question": None,
            "clarification_options": None,
            "refusal_reason": None,
        }
    )
    assert plan.clarification_options == []


def test_options_must_still_be_a_list_when_given():
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "intent": "clarify",
                "summary": "s",
                "clarification_question": "Which?",
                "clarification_options": "a",
            }
        )


def test_a_propose_plan_is_valid_without_a_refusal_reason():
    plan = Plan(intent="propose", summary="Open a ticket for one device")
    assert plan.intent == "propose" and plan.refusal_reason is None


def _validation_error() -> ValidationError:
    try:
        Plan.model_validate(
            {
                "intent": "bogus",
                "summary": "tell me about secret-user@example.com",
                "window_days": 0,
            }
        )
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


def test_plan_problems_name_fields_and_error_types_only():
    problems = plan_problems(_validation_error())
    assert problems is not None
    assert any(p.startswith("intent: ") for p in problems)
    assert any(p.startswith("window_days: ") for p in problems)
    assert "secret-user" not in " ".join(problems) and "bogus" not in " ".join(problems)


def test_plan_problems_recognise_a_rejected_plan_and_ignore_other_errors():
    assert plan_problems(PlanRejected("no plan")) == ["plan: not a structured plan"]
    assert plan_problems(RuntimeError("down")) is None
    assert plan_problems(TimeoutError()) is None
