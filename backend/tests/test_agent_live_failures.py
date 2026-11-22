"""Failure shapes found in a live run, each reproduced with a scripted model and fixed in code.

Compliance questions planned as actions, battery questions answered by the fleet summary, wrong-word
refusal reasons, an empty device list read as "no such device", over-long prose, and the headline.
"""

import pytest
from agent_support import run
from graph_support import ACME_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent import graph, prompts
from fleet_copilot.agent.llm import (
    Plan,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.agent.tools import TOOLS

SCREEN_LOCK = ("compliance_failures_now", {"check_id": "screen_lock"})


async def ask(service, question):
    return await service.run_turn(ACME_ADMIN, "c1", question)


def unsupported_action(summary="the user wants something done"):
    return Plan(intent="unsupported", summary=summary, refusal_reason="unsupported_action")


def notes(result):
    return [e for e in result.trace if e.kind == "plan" and "intent" not in e.data]


# ---- 1. compliance questions are answers, never actions ----------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "os update failures?",
        "which devices fail the screen lock check?",
        "screen lock failing",
        "OS update persistent",
        "which devices still need the OS update?",
        "who changed screen lock status this month?",
        "which laptops are not disk encrypted?",
        "zx81qw07nope battery history pls",
        "which devices pass the os_up_to_date check",
        "screen_lock failures on desktops",
    ],
)
def test_a_question_that_names_no_action_is_not_an_action(question):
    assert graph.names_an_action(question, strict=True) is False


@pytest.mark.parametrize(
    "question",
    [
        "approve it",
        "yes go ahead and execute it",
        "flag all of them",
        "run it",
        "Open a ticket to wipe the first device",
        "delete everything",
        "remove the app from those",
        "reboot them",
        "restart the first one",
        "install the update on all of them",
        "uninstall utorrent",
        "lock the laptops",
        "disable the firewall",
        "enable disk encryption on them",
        "deploy it",
        "push the patch",
        "order more memory",
        "upgrade the ram",
        "notify the owners",
        "tell the employee",
        "send a message to them",
        "email them",
        "remind the user",
        "yes do it",
        "go ahead",
        "approval for the ticket",
    ],
)
def test_a_question_that_names_an_action_still_does(question):
    assert graph.names_an_action(question, strict=True) is True


def test_the_everyday_check_name_screen_lock_is_not_the_lock_action():
    assert graph.names_an_action("screen lock failing", strict=True) is False
    assert graph.names_an_action("lock the screens", strict=True) is True
    assert graph.names_an_action("screen lock failing, then lock them", strict=True) is True


def test_the_looser_advice_check_is_unchanged():
    assert graph.names_an_action("raise a ticket for those") is True
    assert graph.names_an_action("approve it") is False
    assert graph.names_an_action("what should we do?") is False


@pytest.mark.parametrize(
    "question",
    [
        "os update failures?",
        "which devices fail the screen lock check?",
        "screen lock failing",
        "OS update persistent",
    ],
)
async def test_an_unsupported_action_plan_for_a_compliance_question_becomes_an_answer(
    real_db_path, question
):
    llm = ScriptedLLM(
        plans=[unsupported_action()],
        acts=[tool_calls_message(SCREEN_LOCK), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), question)
    assert result.envelope.outcome == "answer"
    assert result.envelope.reason_code is None
    planned = next(e for e in result.trace if e.kind == "plan" and "intent" in e.data)
    assert planned.data["intent"] == "answer" and planned.data["tools"] == []
    assert len(notes(result)) == 1
    assert llm.calls_of("act")[0].tools is None  # every tool is offered
    assert "error" not in [e.kind for e in result.trace]


async def test_the_unknown_device_battery_question_flows_to_the_uniform_not_found(real_db_path):
    llm = ScriptedLLM(
        plans=[unsupported_action()],
        acts=[
            tool_calls_message(
                (
                    "device_history",
                    {"device_id": "ZX81QW07NOPE", "metric": "battery_capacity"},
                )
            ),
            final_message(),
        ],
    )
    result = await ask(make_service(real_db_path, llm), "zx81qw07nope battery history pls")
    assert (result.envelope.outcome, result.envelope.reason_code) == ("denied", "not_found")


@pytest.mark.parametrize(
    "question",
    [
        "approve it",
        "yes go ahead and execute it",
        "flag all of them",
        "approve the ticket",
        "execute it",
    ],
)
async def test_a_request_that_names_an_action_stays_unsupported(real_db_path, question):
    llm = ScriptedLLM(plans=[unsupported_action()])
    result = await ask(make_service(real_db_path, llm), question)
    assert (result.envelope.outcome, result.envelope.reason_code) == (
        "unsupported",
        "unsupported_action",
    )
    assert llm.calls_of("act") == []
    assert not notes(result)


async def test_other_unsupported_reasons_are_never_turned_into_answers(real_db_path):
    llm = ScriptedLLM(plans=[Plan(intent="unsupported", refusal_reason="off_topic")])
    result = await ask(make_service(real_db_path, llm), "what is the weather")
    assert result.envelope.reason_code == "off_topic"
    assert llm.calls_of("act") == []


def test_the_plan_prompt_names_the_three_checks_and_calls_questions_about_them_answers():
    text = prompts.PLAN_SYSTEM
    for words in ("screen lock (screen_lock)", "os_up_to_date", "disk_encryption"):
        assert words in text
    assert "never an action" in text


def test_the_plan_prompt_counts_a_message_to_the_employee_as_a_proposal():
    assert "send a notification" in prompts.PLAN_SYSTEM
    assert "employee_notification" in prompts.PLAN_SYSTEM


# ---- 2. one area, one tool: the summary tool is for summaries ----------------------------------------


def test_the_prompts_and_the_tool_say_fleet_health_is_only_for_a_summary():
    assert "ONLY for an overall summary" in prompts.PLAN_SYSTEM
    assert "ONLY for an overall summary" in prompts.ACT_SYSTEM
    doc = next(t for t in TOOLS if t.name == "fleet_health").description
    assert "ONLY" in doc and "one area" in doc


def test_a_plan_that_lists_fleet_health_with_an_area_tool_drops_it():
    plan = Plan(intent="answer", tools=["fleet_health", "trend", "find_devices"])
    assert graph.without_summary_tool(plan).tools == ["trend", "find_devices"]


def test_a_plan_that_lists_only_fleet_health_keeps_it():
    plan = Plan(intent="answer", tools=["fleet_health"])
    assert graph.without_summary_tool(plan).tools == ["fleet_health"]
    with_lookup = Plan(intent="answer", tools=["fleet_health", "find_devices"])
    assert graph.without_summary_tool(with_lookup).tools == ["fleet_health", "find_devices"]


async def test_a_battery_plan_naming_fleet_health_binds_the_trend_tool_only(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["fleet_health", "trend", "find_devices"])],
        acts=[tool_calls_message(("trend", {"metric": "battery"})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), "Which laptops show signs of battery wear?")
    offered = llm.calls_of("act")[0].tools
    assert offered is not None and "fleet_health" not in offered and "trend" in offered
    planned = next(e for e in result.trace if e.kind == "plan" and "intent" in e.data)
    assert "fleet_health" not in planned.data["tools"]


async def test_a_call_to_fleet_health_on_that_plan_does_not_run(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["fleet_health", "trend"])],
        acts=[
            tool_calls_message(("fleet_health", {}), ("trend", {"metric": "battery"})),
            final_message(),
        ],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), "which batteries are wearing out?")
    ran = [e.tool for e in result.trace if e.kind == "tool_call"]
    assert ran == ["trend"]


async def test_a_real_summary_question_keeps_fleet_health(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["fleet_health"])],
        acts=[tool_calls_message(("fleet_health", {})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), "what needs attention?")
    assert [e.tool for e in result.trace if e.kind == "tool_call"] == ["fleet_health"]


# ---- 3. a data-unavailable word in the wrong field ---------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "topic"),
    [
        ("network", "network"),
        ("location", "location"),
        ("performance", "performance"),
        ("user_activity", "user_activity"),
        ("peripherals", "peripherals"),
        ("Network", "network"),
        ("user-activity", "user_activity"),
        ("data_unavailable", "other"),
        ("missing_data", "other"),
        ("not_in_data", "other"),
        ("missing_topic", "other"),
    ],
)
def test_a_missing_topic_name_in_the_reason_field_is_a_missing_topic(reason, topic):
    plan = Plan.model_validate({"intent": "unsupported", "refusal_reason": reason})
    assert (plan.intent, plan.refusal_reason, plan.missing_topic) == (
        "unsupported",
        "data_unavailable",
        topic,
    )


def test_such_a_reason_is_a_missing_topic_whatever_the_intent():
    for intent in ("refuse", "answer", "unsupported"):
        plan = Plan.model_validate({"intent": intent, "refusal_reason": "network"})
        assert plan.intent == "unsupported" and plan.missing_topic == "network"


def test_a_topic_in_its_own_field_wins_over_a_synonym_reason():
    plan = Plan.model_validate(
        {"intent": "unsupported", "refusal_reason": "missing_data", "missing_topic": "location"}
    )
    assert plan.missing_topic == "location"


def test_a_reason_nobody_knows_is_still_rejected_so_the_planner_is_asked_again():
    with pytest.raises(Exception, match="refusal_reason"):
        Plan.model_validate({"intent": "unsupported", "refusal_reason": "gibberish"})


def test_the_retry_message_still_names_the_valid_ways_to_refuse():
    text = str(prompts.plan_retry_message(["refusal_reason: literal_error"]).content)
    assert "refusal_reason: literal_error" in text
    assert "off_topic" in text and "missing_topic" in text


# ---- 4. an empty device list is no filter -------------------------------------------------------------


async def test_an_empty_device_list_is_no_filter_not_no_such_device(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(("find_devices", {"form_factor": "laptop", "device_ids": []})),
            final_message(),
        ],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), "how many laptops do we have")
    assert result.envelope.outcome == "answer"
    assert result.envelope.reason_code != "not_found"


@pytest.mark.parametrize(
    "call",
    [
        ("low_disk_devices", {"device_ids": []}),
        ("trend", {"metric": "disk", "device_ids": []}),
        ("fleet_health", {"device_ids": []}),
        ("memory_pressure_now", {"device_ids": []}),
    ],
)
async def test_an_empty_device_list_means_the_whole_fleet_for_every_filter_tool(real_db_path, call):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(call), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), "disk sustained week")
    assert result.envelope.reason_code != "not_found"
    recorded = next(e for e in result.trace if e.kind == "tool_result")
    assert recorded.data["status"] != "not_found"


def test_the_empty_list_is_dropped_before_the_tool_runs():
    assert graph.without_empty_ids({"device_ids": [], "x": 1}) == {"x": 1}
    assert graph.without_empty_ids({"device_ids": ["A"]}) == {"device_ids": ["A"]}
    assert graph.without_empty_ids({"device_ids": None, "y": 2}) == {"device_ids": None, "y": 2}


def test_the_tool_runner_itself_reads_an_empty_list_as_no_filter(real_db_path, real_conn):
    plain = run(real_db_path, real_conn, "low_disk_devices", {})
    empty = run(real_db_path, real_conn, "low_disk_devices", {"device_ids": []})
    assert empty.error is None and empty.device_ids == plain.device_ids and plain.device_ids
