"""The plan names the tools it expects to need, and that list decides which tools the act step may use."""

from typing import get_args

import pytest
from graph_support import ACME_ADMIN, answer_plan, explanation, make_service

from fleet_copilot import policy
from fleet_copilot.agent import graph
from fleet_copilot.agent.llm import (
    Plan,
    Reference,
    ScriptedLLM,
    ToolName,
    final_message,
    tool_calls_message,
)
from fleet_copilot.agent.tools import PROPOSAL_TOOLS, TOOLS

LOOKUPS = ["find_devices", "device_history"]
SCREEN_LOCK = ("compliance_failures_now", {"check_id": "screen_lock"})
PROPOSE_TICKET = ("open_remediation_ticket", {"device_id": "X", "check_id": "screen_lock"})
FLAG = ("flag_device_for_replacement", {"device_id": "X"})


async def ask(service, question="which devices are low on disk?"):
    return await service.run_turn(ACME_ADMIN, "c1", question)


def plan_of(result):
    return next(e for e in result.trace if e.kind == "plan")


def widened_events(result):
    return [e for e in result.trace if e.kind == "plan" and e.data.get("widened")]


def ran(result):
    return [e.tool for e in result.trace if e.kind == "tool_call" and e.tool]


# ---- the plan field ----------------------------------------------------------------------------------


def test_the_named_tools_are_exactly_the_agent_tools_the_model_may_pick():
    assert set(get_args(ToolName)) == {t.name for t in TOOLS} - {"recommend_actions"}
    assert PROPOSAL_TOOLS <= set(get_args(ToolName))


def test_a_plan_keeps_the_valid_tools_it_names_in_order():
    plan = Plan(intent="answer", tools=["low_disk_devices", "trend"])
    assert plan.tools == ["low_disk_devices", "trend"]
    assert Plan(intent="answer").tools == []


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        (["low_disk_devices", "no_such_tool", 7, None, ""], ["low_disk_devices"]),
        (["Low-Disk-Devices", " TREND "], ["low_disk_devices", "trend"]),
        (["trend", "trend", "trend"], ["trend"]),
        ("fleet_health", ["fleet_health"]),
        (None, []),
        ({"tool": "trend"}, []),
        (42, []),
        (["recommend_actions"], []),
    ],
)
def test_unknown_tool_names_are_dropped_and_never_fail_the_plan(raw, kept):
    assert Plan.model_validate({"intent": "answer", "tools": raw}).tools == kept


def test_a_plan_names_at_most_four_tools():
    names = ["low_disk_devices", "macos_version_check", "trend", "fleet_health", "find_devices"]
    assert Plan.model_validate({"intent": "answer", "tools": names}).tools == names[:4]


@pytest.mark.parametrize(
    "data",
    [
        {"intent": "clarify", "clarification_question": "Which one?"},
        {"intent": "refuse", "refusal_reason": "prompt_injection"},
        {"intent": "unsupported", "refusal_reason": "off_topic"},
        {"intent": "advise"},
    ],
)
def test_a_plan_that_answers_nothing_names_no_tools(data):
    assert Plan.model_validate({**data, "tools": ["trend"]}).tools == []


# ---- which tools are bound ---------------------------------------------------------------------------


def test_a_plan_with_tools_binds_them_and_the_two_lookups():
    bound = graph.bound_tools(Plan(intent="answer", tools=["low_disk_devices"]))
    assert bound == {"low_disk_devices", *LOOKUPS}


def test_a_plan_without_tools_binds_every_tool():
    assert graph.bound_tools(Plan(intent="answer")) is None
    assert graph.bound_tools(Plan(intent="answer", tools=["trend"]), widened=True) is None
    assert graph.bound_tools(Plan(intent="advise")) is None


def test_an_answer_plan_never_binds_a_proposal_tool():
    plan = Plan(intent="answer", tools=["trend", "notify_employee"])
    assert graph.bound_tools(plan) == {"trend", *LOOKUPS}
    assert graph.bound_tools(Plan(intent="answer", tools=["notify_employee"])) is None


def test_a_propose_plan_binds_only_the_proposal_tool_it_names():
    plan = Plan(intent="propose", tools=["open_remediation_ticket"])
    assert graph.bound_tools(plan) == {"open_remediation_ticket", *LOOKUPS}


def test_a_propose_plan_that_names_no_proposal_tool_binds_all_four():
    bound = graph.bound_tools(Plan(intent="propose", tools=["find_devices"]))
    assert bound == {*PROPOSAL_TOOLS, *LOOKUPS}


async def test_the_act_call_is_limited_to_the_planned_tools_and_the_lookups(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["low_disk_devices"])],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm))
    assert result.envelope.outcome == "answer"
    offered = [call.tools for call in llm.calls_of("act")]
    expected = tuple(t.name for t in TOOLS if t.name in {"low_disk_devices", *LOOKUPS})
    assert offered == [expected, expected]


async def test_a_plan_without_tools_offers_every_tool(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    await ask(make_service(real_db_path, llm))
    assert [call.tools for call in llm.calls_of("act")] == [None, None]


async def test_a_call_outside_the_bound_tools_does_not_run(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["low_disk_devices"])],
        acts=[
            tool_calls_message(("low_disk_devices", {}), ("trend", {"metric": "disk"})),
            final_message(),
        ],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm))
    assert result.envelope.outcome == "answer"
    assert ran(result) == ["low_disk_devices"]
    rejected = next(e for e in result.trace if e.kind == "tool_result" and e.tool == "trend")
    assert rejected.data["status"] == "invalid_input"
    reply = llm.calls_of("act")[-1].messages[-1]
    assert graph.NOT_PLANNED in str(reply.content)


# ---- proposals stay gated ----------------------------------------------------------------------------


@pytest.fixture
def actions_db(tmp_path):
    return tmp_path / "state" / "actions.db"


def propose_plan(**kw):
    return Plan(
        intent="propose",
        summary="open a remediation ticket",
        reference=Reference(kind="ordinal", index=1),
        **kw,
    )


async def propose_after_list(real_db_path, actions_db, plan, act):
    llm = ScriptedLLM(
        plans=[answer_plan(), plan],
        acts=[tool_calls_message(SCREEN_LOCK), final_message(), act, final_message()],
        explains=[explanation(headline="Five of 10 devices fail screen_lock")],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    await ask(service, "Which devices fail the screen lock check?")
    return llm, await ask(service, "Open a ticket for the first one")


async def test_a_propose_plan_binds_only_the_named_proposal_tool(real_db_path, actions_db):
    llm, result = await propose_after_list(
        real_db_path,
        actions_db,
        propose_plan(tools=["open_remediation_ticket"]),
        tool_calls_message(PROPOSE_TICKET),
    )
    assert result.envelope.outcome == "proposal"
    assert set(llm.calls_of("act")[2].tools) == {"open_remediation_ticket", *LOOKUPS}


async def test_an_unnamed_proposal_tool_is_not_run_on_a_propose_plan(real_db_path, actions_db):
    _, result = await propose_after_list(
        real_db_path,
        actions_db,
        propose_plan(tools=["open_remediation_ticket"]),
        tool_calls_message(FLAG),
    )
    assert result.envelope.outcome != "proposal"
    assert FLAG[0] not in ran(result)


async def test_one_proposal_per_turn_still_holds_when_two_are_named(real_db_path, actions_db):
    _, result = await propose_after_list(
        real_db_path,
        actions_db,
        propose_plan(tools=["open_remediation_ticket", "flag_device_for_replacement"]),
        tool_calls_message(PROPOSE_TICKET, FLAG),
    )
    assert result.envelope.outcome == "proposal"
    assert ran(result) == ["open_remediation_ticket"]


async def test_a_proposal_tool_named_by_an_answer_plan_still_cannot_run(real_db_path, actions_db):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["low_disk_devices", "flag_device_for_replacement"])],
        acts=[tool_calls_message(("low_disk_devices", {}), FLAG), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm, actions_db_path=actions_db))
    assert result.envelope.outcome == "answer"
    assert ran(result) == ["low_disk_devices"]
    assert FLAG[0] not in (llm.calls_of("act")[0].tools or ())


# ---- widening ----------------------------------------------------------------------------------------


async def test_when_the_planned_tools_gather_nothing_every_tool_is_offered_once(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["trend"])],
        acts=[final_message(), tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm))
    assert result.envelope.outcome == "answer"
    restricted = tuple(t.name for t in TOOLS if t.name in {"trend", *LOOKUPS})
    assert [call.tools for call in llm.calls_of("act")] == [restricted, None, None]
    assert [e.data for e in widened_events(result)] == [{"widened": True, "planned": ["trend"]}]
    assert ran(result) == ["low_disk_devices"]  # a tool outside the plan could run once widened


async def test_widening_happens_once_and_then_the_turn_ends_without_evidence(real_db_path):
    llm = ScriptedLLM(plans=[answer_plan(tools=["trend"])], acts=[final_message(), final_message()])
    result = await ask(make_service(real_db_path, llm))
    assert result.envelope.outcome == "clarification"
    assert len(llm.calls_of("act")) == 2
    assert len(widened_events(result)) == 1


async def test_a_round_with_evidence_does_not_widen(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["low_disk_devices"])],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm))
    assert not widened_events(result)


async def test_widening_keeps_the_proposal_gate(real_db_path, actions_db):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["trend"])],
        acts=[final_message(), tool_calls_message(FLAG), final_message()],
    )
    result = await ask(make_service(real_db_path, llm, actions_db_path=actions_db))
    assert result.envelope.outcome != "proposal"
    assert FLAG[0] not in ran(result)


async def test_widening_respects_the_tool_round_limit(real_db_path):
    rounds = policy.MAX_TOOL_ROUNDS
    bad = (
        "trend",
        {"metric": "nonsense"},
    )  # an input error each round: no record, the round is spent
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["trend"])],
        acts=[tool_calls_message(bad) for _ in range(rounds)] + [final_message()],
    )
    result = await ask(make_service(real_db_path, llm))
    assert len(llm.calls_of("act")) == rounds + 1
    assert not widened_events(result)


# ---- the plan in the trace ---------------------------------------------------------------------------


async def test_the_plan_event_shows_the_plan_without_the_users_words(real_db_path):
    question = "which devices are low on disk? my secret-token is hunter2"
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["low_disk_devices"], window_days=7)],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), question)
    data = plan_of(result).data
    assert data == {
        "intent": "answer",
        "window_days": 7,
        "tools": ["low_disk_devices"],
        "has_reference": False,
        "reference_kind": None,
        "action_type": None,
        "missing_topic": None,
        "repaired": False,
        "fallback": False,
    }
    assert "hunter2" not in str(data)


async def test_the_plan_event_carries_the_reference_action_and_missing_topic(real_db_path):
    llm = ScriptedLLM(
        plans=[
            Plan(intent="unsupported", missing_topic="network", refusal_reason="data_unavailable")
        ]
    )
    result = await ask(make_service(real_db_path, llm), "how is the wifi?")
    data = plan_of(result).data
    assert data["intent"] == "unsupported" and data["missing_topic"] == "network"
    assert data["tools"] == [] and data["action_type"] is None and data["fallback"] is False
