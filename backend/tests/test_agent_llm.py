import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from fleet_copilot.agent.llm import (
    Explanation,
    Plan,
    Reference,
    ScriptedLLM,
    ScriptExhausted,
    final_message,
    tool_calls_message,
)

# ---- schemas ---------------------------------------------------------------------------------------


def test_a_plain_answer_plan_is_valid():
    plan = Plan(intent="answer", summary="Find low disk devices")
    assert plan.reference is None and plan.window_days is None and plan.clarification_options == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"intent": "clarify"}, "needs a clarification_question"),
        ({"intent": "refuse"}, "cross_tenant or prompt_injection"),
        ({"intent": "refuse", "refusal_reason": "off_topic"}, "cross_tenant or prompt_injection"),
        ({"intent": "unsupported"}, "off_topic or unsupported_action"),
        ({"intent": "propose", "refusal_reason": "off_topic"}, "refusal_reason is only valid"),
        (
            {"intent": "unsupported", "refusal_reason": "cross_tenant"},
            "off_topic or unsupported_action",
        ),
        (
            {"intent": "answer", "refusal_reason": "off_topic"},
            "only valid for refuse or unsupported",
        ),
        (
            {
                "intent": "clarify",
                "clarification_question": "Which?",
                "refusal_reason": "off_topic",
            },
            "only valid",
        ),
    ],
)
def test_plans_with_inconsistent_intent_and_reason_are_rejected(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        Plan(summary="s", **kwargs)


def test_every_valid_refusal_pairing_is_accepted():
    for intent, reason in (
        ("refuse", "cross_tenant"),
        ("refuse", "prompt_injection"),
        ("unsupported", "off_topic"),
        ("unsupported", "unsupported_action"),
    ):
        assert Plan(intent=intent, summary="s", refusal_reason=reason).refusal_reason == reason


def test_plan_limits_and_unknown_fields_are_enforced():
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", window_days=0)
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", window_days=61)
    with pytest.raises(ValidationError):
        Plan(
            intent="clarify",
            summary="s",
            clarification_question="Which?",
            clarification_options=["a"] * 7,
        )
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="s", surprise=True)
    with pytest.raises(ValidationError):
        Plan(intent="answer", summary="x" * 301)


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


def test_explanation_has_loose_hard_limits_and_verify_enforces_the_real_ones():
    assert Explanation(headline="h").explanation is None
    with pytest.raises(ValidationError):
        Explanation(headline="h" * 301)
    with pytest.raises(ValidationError):
        Explanation(headline="h", explanation="e" * 1501)


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
    llm = ScriptedLLM(explains=[RuntimeError("model down"), Explanation(headline="ok")])
    with pytest.raises(RuntimeError, match="model down"):
        await llm.explain([HumanMessage("a")])
    assert (await llm.explain([HumanMessage("b")])).headline == "ok"


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
