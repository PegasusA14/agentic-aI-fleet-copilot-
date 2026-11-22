"""Planner misreads found in a live run, each fixed by prompt wording and a small code guard.

(a) a misspelled approval is an unsupported action, even when the planner asked a question about it;
(b) "all of those" after one list is advice for that list, not a question about which list;
(c) a named macOS version uses the version tool, not the compliance check;
(d) a hardware or model breakdown is an answer; (e) a battery-service question is a battery trend.
"""

import pytest
from graph_support import ACME_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent import graph
from fleet_copilot.agent.focus import NeedsClarification, Resolved, resolve_reference
from fleet_copilot.agent.llm import (
    Plan,
    Reference,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.contracts import EntityList, EntityRef
from fleet_copilot.repository import Device

LOW = ("low_disk_devices", {})


async def ask(service, question, conversation="c1"):
    return await service.run_turn(ACME_ADMIN, conversation, question)


# ---- (a) a typo must not hide an approval --------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "aprove it now",
        "aprove proposal a1b2c3d4 right now",
        "approev it",
        "please aproved that one",
        "exectue it",
        "exicute the plan",
        "go aprove them",
        "execte that",
    ],
)
def test_a_misspelled_approval_or_execution_is_an_action_word(question):
    assert graph.typoed_approval(question) is True
    assert graph.names_an_action(question, strict=True) is True


@pytest.mark.parametrize(
    "question",
    [
        "which devices are low on disk?",
        "which laptops are older than macOS 15?",
        "improved battery readings",
        "show me the executive team's laptops",
        "approx how many devices?",
        "which devices have the app prove installed",
        "what is the proof of that?",
    ],
)
def test_ordinary_words_are_not_read_as_a_misspelled_approval(question):
    assert graph.typoed_approval(question) is False


def test_a_correctly_spelled_approval_is_caught_by_the_existing_action_words_not_as_a_typo():
    assert graph.typoed_approval("approve it") is False
    assert graph.names_an_action("approve it", strict=True) is True


@pytest.mark.parametrize("question", ["aprove it now", "aprove proposal a1b2c3d4 right now"])
async def test_a_planner_clarification_of_a_misspelled_approval_becomes_unsupported_action(
    real_db_path, question
):
    plan = Plan(intent="clarify", clarification_question="Approve which proposal?")
    llm = ScriptedLLM(plans=[plan])
    result = await ask(make_service(real_db_path, llm), question)
    assert (result.envelope.outcome, result.envelope.reason_code) == (
        "unsupported",
        "unsupported_action",
    )
    assert llm.calls_of("act") == [] and llm.calls_of("explain") == []


@pytest.mark.parametrize("question", ["aprove it now", "aprove proposal a1b2c3d4 right now"])
async def test_an_unsupported_action_plan_for_a_misspelled_approval_is_believed(
    real_db_path, question
):
    plan = Plan(intent="unsupported", refusal_reason="unsupported_action")
    result = await ask(make_service(real_db_path, ScriptedLLM(plans=[plan])), question)
    assert result.envelope.reason_code == "unsupported_action"


async def test_a_question_about_an_earlier_approval_is_still_chat(real_db_path):
    # the guard only rewrites a clarify plan; a conversation question the planner calls chat stays chat
    llm = ScriptedLLM(plans=[Plan(intent="chat")], explains=[explanation(text="Not yet.")])
    result = await ask(make_service(real_db_path, llm), "did we aprove that ticket?")
    assert result.envelope.outcome == "chat" and result.envelope.reply == "Not yet."


# ---- (b) "all of those" after one list ------------------------------------------------------------------


def test_the_bulk_follow_up_names_its_action():
    assert (
        graph.bulk_action_type("mark every one of those laptops for replacement")
        == "replacement_flag"
    )
    assert (
        graph.bulk_action_type("Please put those three on the replacement list at once")
        == "replacement_flag"
    )
    assert graph.bulk_action_type("raise a ticket for all of them") == "remediation_ticket"
    assert graph.bulk_action_type("order upgrades for those") == "upgrade_order"
    assert graph.bulk_action_type("tell the owners of those") == "employee_notification"
    assert graph.bulk_action_type("what about those?") is None


@pytest.mark.parametrize(
    "question",
    [
        "mark every one of those laptops for replacement",
        "Please put those three on the replacement list at once",
    ],
)
async def test_a_bulk_follow_up_after_one_list_is_advice_not_a_clarification(
    real_db_path, question
):
    clarify = Plan(intent="clarify", clarification_question="Which devices do you mean?")
    llm = ScriptedLLM(
        plans=[answer_plan(), clarify],
        acts=[tool_calls_message(LOW), final_message()],
        explains=[explanation(text="Both are laptops."), explanation(text="Here is what applies.")],
    )
    service = make_service(real_db_path, llm)
    await ask(service, "which devices are low on disk?")
    result = await ask(service, question)
    assert result.envelope.outcome != "clarification"
    plan_event = [e for e in result.trace if e.kind == "plan" and "intent" in e.data][0]
    assert (
        plan_event.data["intent"] == "advise"
        and plan_event.data["action_type"] == "replacement_flag"
    )
    assert any(e.data.get("changed") == "clarify_to_advise" for e in result.trace)
    # advice resolved the devices of the one list shown last; nothing was created
    assert result.envelope.outcome in ("answer", "no_match")
    assert result.envelope.proposal is None
    assert len(llm.calls_of("act")) == 2  # only the first turn's two acting steps


async def test_a_bulk_follow_up_with_a_hint_that_names_no_list_still_resolves_the_one_list(
    real_db_path,
):
    # the planner asks to act on "all of those laptops": the hint describes devices, it does not pick a list
    propose = Plan(
        intent="propose",
        summary="flag them",
        action_type="replacement_flag",
        reference=Reference(kind="all", list_hint="laptops"),
    )
    llm = ScriptedLLM(
        plans=[answer_plan(), propose],
        acts=[tool_calls_message(LOW), final_message()],
        explains=[explanation(), explanation()],
    )
    service = make_service(real_db_path, llm)
    await ask(service, "which devices are low on disk?")
    result = await ask(service, "mark every one of those laptops for replacement")
    assert result.envelope.outcome != "clarification"


def _list(title, *ids):
    return EntityList(
        list_id=title,
        title=title,
        items=[EntityRef(rank=i, device_id=d, label=f"h-{d}") for i, d in enumerate(ids, 1)],
    )


def _devices(*ids):
    return [Device("acme-001", d, "emp", f"h-{d}", "macOS", "MacBook Pro", "laptop") for d in ids]


def test_all_with_a_hint_that_matches_no_title_resolves_when_there_is_one_list():
    focus = [_list("Devices low on disk", "A", "B")]
    reference = Reference(kind="all", list_hint="laptops")
    got = resolve_reference(reference, focus, _devices("A", "B", "C"))
    assert isinstance(got, Resolved) and got.device_ids == ("A", "B")


def test_all_with_a_hint_that_matches_no_title_still_asks_when_there_are_several_lists():
    focus = [_list("Devices low on disk", "A"), _list("Screen lock failures", "B")]
    got = resolve_reference(Reference(kind="all", list_hint="laptops"), focus, _devices("A", "B"))
    assert isinstance(got, NeedsClarification)


def test_an_ordinal_with_a_hint_that_matches_no_title_still_asks():
    focus = [_list("Devices low on disk", "A", "B")]
    got = resolve_reference(
        Reference(kind="ordinal", index=1, list_hint="laptops"), focus, _devices("A", "B")
    )
    assert isinstance(got, NeedsClarification)


# ---- (c) a named macOS version uses the version tool -----------------------------------------------------


MACS = "Which of our Macs still need the macOS 15 update?"


def test_a_named_macos_version_gets_the_version_tool_and_drops_the_compliance_check():
    plan = answer_plan(tools=["compliance_failures_now"])
    fixed = graph.adjust_tools(plan, MACS)
    assert fixed.tools == ["macos_version_check"]


def test_a_macos_version_with_a_compliance_question_keeps_both_tools():
    plan = answer_plan(tools=["compliance_failures_now"])
    fixed = graph.adjust_tools(plan, "Which Macs on macOS 14 fail the screen lock check?")
    assert fixed.tools == ["macos_version_check", "compliance_failures_now"]


def test_a_question_that_names_no_macos_version_keeps_the_compliance_check():
    plan = answer_plan(tools=["compliance_failures_now"])
    assert graph.adjust_tools(plan, "Which devices still need the OS update?") is plan


async def test_the_act_step_is_offered_the_version_tool_for_a_named_macos_version(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["compliance_failures_now"])],
        acts=[tool_calls_message(("macos_version_check", {"older_than": "15"})), final_message()],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), MACS)
    offered = set(llm.calls_of("act")[0].tools)
    assert "macos_version_check" in offered and "compliance_failures_now" not in offered
    assert any(e.data.get("changed") == "tools" for e in result.trace)


# ---- (e) a battery-service question is a battery trend ----------------------------------------------------


BATTERY = "wich laptops have a batery that the vendor says needs service"


def test_a_battery_question_with_only_the_summary_tool_uses_the_trend_tool():
    fixed = graph.adjust_tools(answer_plan(tools=["fleet_health"]), BATTERY)
    assert fixed.tools == ["trend"]


def test_a_battery_summary_request_keeps_the_fleet_summary():
    plan = answer_plan(tools=["fleet_health"])
    assert graph.adjust_tools(plan, "give me an overall battery and disk summary") is plan
    assert graph.adjust_tools(plan, "fleet health please") is plan


async def test_the_act_step_is_not_offered_the_fleet_summary_for_a_battery_question(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["fleet_health"])],
        acts=[tool_calls_message(("trend", {"metric": "battery"})), final_message()],
        explains=[explanation()],
    )
    await ask(make_service(real_db_path, llm), BATTERY)
    offered = set(llm.calls_of("act")[0].tools)
    assert "trend" in offered and "fleet_health" not in offered


# ---- (d) inventory and (c) are also said to the model in words ------------------------------------------


def test_the_planner_prompt_names_the_inventory_and_service_examples():
    from fleet_copilot.agent import prompts

    text = " ".join(prompts.PLAN_SYSTEM.split())
    assert "hw modle breakdwn pls" in text and "inventory by model" in text
    assert "needs service" in text and "never fleet_health" in text
