"""Action quality of a conversational reply.

A message that puts forward a course of action ("can't we ask the users to clean up?") is advice, never
chat. A reply may only offer an action that this turn's facts back, and may not judge how well an action
would work. Both are checked sentence by sentence by code.
"""

import dataclasses

import pytest
from agent_support import repo_for, run
from graph_support import ACME_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent import graph, memory, verify
from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.llm import Plan, ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.verify import (
    ACTION_RULES,
    HEDGE_RULES,
    action_pairs,
    judges_effectiveness,
    named_actions,
    parse_reply_problems,
    unbacked_actions,
    verify_prose,
)
from fleet_copilot.contracts import SuggestedAction
from fleet_copilot.repository import dataset_as_of

USER_MESSAGES = [
    "why needed upgrade can't we ask the users who owning the devices to clean up their spaces ?",
    "can't we just ask the owners to free up space?",
    "what about telling the users to clean up their disks?",
    "why not message the employees to delete some files instead?",
    "why not just tell the owners?",
    "could we notify them instead?",
    "what about asking them to free space?",
]

# The sentence a live run produced on a chat turn.
BAD_OFFER = (
    "If you want, we can propose a storage upgrade for acme-macbook-4 or a replacement path for "
    "acme-thinkpad-8, and those would still need approval."
)
BAD_JUDGMENT = (
    "We can ask users to tidy up, but that may only help a little and it does not change the fact "
    "that acme-macbook-4 is already at 2.0% free."
)
MACBOOK_UPGRADE = ("upgrade_order", "acme-macbook-4")


def suggestion(action_type="upgrade_order", host="acme-macbook-4", device_id="DEV1"):
    return SuggestedAction(
        action_type=action_type,
        device_id=device_id,
        target_label=host,
        label=f"{action_type} for {host}",
        reason=f"{host}: free disk is low",
        request=f"{action_type} {host}",
        refs=["acme-001/DEV1#disk"],
    )


# ---- the planner: a course of action is advice, never chat -------------------------------------------------


@pytest.mark.parametrize("question", USER_MESSAGES + ["shouldn't we just replace it?"])
def test_a_message_that_puts_forward_an_action_is_a_course_of_action(question):
    assert graph.suggests_course_of_action(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "hello",
        "good morning",
        "thanks!",
        "what can you do?",
        "can you tell me what data you have?",
        "can you remind me which one was lowest?",
        "which was the lowest one you mentioned?",
        "why did you suggest that?",
        "did we open that ticket?",
        "what is the status of that ticket?",
        "summarize what we found",
    ],
)
def test_greetings_thanks_capability_and_meta_questions_are_not_a_course_of_action(question):
    assert graph.suggests_course_of_action(question) is False


def test_asking_the_owners_is_the_notification_even_when_the_message_names_an_upgrade():
    assert graph.bulk_action_type(USER_MESSAGES[0]) == "employee_notification"
    assert graph.bulk_action_type("shouldn't we just replace it?") == "replacement_flag"
    for question in USER_MESSAGES:
        assert graph.names_an_action(question) is True


async def _after_low_disk(real_db_path, second_plan, question, explains=()):
    llm = ScriptedLLM(
        plans=[answer_plan(), second_plan],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation(text="Both are low on disk."), *explains],
    )
    service = make_service(real_db_path, llm)
    await service.run_turn(ACME_ADMIN, "c1", "Which devices are low on disk space?")
    return await service.run_turn(ACME_ADMIN, "c1", question), llm


@pytest.mark.parametrize("question", USER_MESSAGES)
async def test_a_chat_plan_for_an_action_suggestion_becomes_advice_over_the_last_answers_devices(
    real_db_path, question
):
    result, llm = await _after_low_disk(
        real_db_path,
        Plan(intent="chat", summary="chat"),
        question,
        explains=[explanation(text="Yes, there is a notification for that.")],
    )
    plan_event = [e for e in result.trace if e.kind == "plan" and "intent" in e.data][0]
    assert plan_event.data["intent"] == "advise"
    assert plan_event.data["action_type"] == "employee_notification"
    assert any(e.data.get("changed") == "chat_to_advise" for e in result.trace)
    envelope = result.envelope
    assert envelope.outcome == "answer" and envelope.proposal is None
    kinds_offered = {s.action_type for s in envelope.suggested_actions}
    assert kinds_offered == {"employee_notification"}
    low = {s.target_label for s in envelope.suggested_actions}
    assert {"acme-macbook-4", "acme-thinkpad-8"} <= low
    assert len(llm.calls_of("act")) == 2  # only the first turn's acting steps


@pytest.mark.parametrize("question", ["hello", "thanks!", "what can you do?"])
async def test_greetings_stay_chat_after_a_list(real_db_path, question):
    result, _ = await _after_low_disk(
        real_db_path,
        Plan(intent="chat", summary="chat"),
        question,
        explains=[explanation(text="Hello.")],
    )
    assert result.envelope.outcome == "chat"
    assert not any(e.data.get("changed") == "chat_to_advise" for e in result.trace)


async def test_an_action_suggestion_with_nothing_discussed_yet_stays_chat(real_db_path):
    llm = ScriptedLLM(plans=[Plan(intent="chat", summary="chat")], explains=[explanation()])
    service = make_service(real_db_path, llm)
    result = await service.run_turn(ACME_ADMIN, "c1", USER_MESSAGES[1])
    assert result.envelope.outcome == "chat"


# ---- the reply check: an action must be backed by this turn's facts ----------------------------------------


def test_named_actions_ties_each_hostname_to_the_nearest_action():
    found = named_actions(BAD_OFFER)
    assert [(kind, hosts) for _, kind, hosts in found if kind] == [
        ("upgrade_order", {"acme-macbook-4"}),
        ("replacement_flag", {"acme-thinkpad-8"}),
    ]


def test_an_operating_system_update_is_not_a_hardware_upgrade():
    assert named_actions("Those Macs still need the macOS 15 upgrade.") == []
    assert named_actions("They are behind on the OS update.") == []


def test_a_sentence_naming_no_action_is_never_unbacked():
    assert unbacked_actions("acme-macbook-4 is at 2.0% free.", set()) is False


def test_the_offer_is_unbacked_with_no_suggestions():
    assert unbacked_actions(BAD_OFFER, set()) is True


def test_an_offer_naming_only_the_backed_action_for_that_device_stands():
    backed = {MACBOOK_UPGRADE}
    one = "If you want, we can propose a storage upgrade for acme-macbook-4."
    assert unbacked_actions(one, backed) is False
    # the same action for a device no suggestion names is not backed
    assert unbacked_actions("We can propose a storage upgrade for acme-thinkpad-8.", backed) is True
    # an unbacked replacement in the same sentence drops the whole sentence
    assert unbacked_actions(BAD_OFFER, backed) is True


def test_an_action_named_with_no_device_needs_a_suggestion_of_that_type():
    assert unbacked_actions("A storage upgrade is one option.", {MACBOOK_UPGRADE}) is False
    assert unbacked_actions("You could notify the owner.", {MACBOOK_UPGRADE}) is True
    assert (
        unbacked_actions("You could notify the owner.", {("employee_notification", "acme-x-1")})
        is False
    )
    assert unbacked_actions("We can propose something.", set()) is True
    assert unbacked_actions("We can propose something.", {MACBOOK_UPGRADE}) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "We can ask users to tidy up.",
        "We could ask the owners to clean up.",
        "I can open a ticket for acme-macbook-4.",
        "That could be flagged for replacement.",
        "The replacement path is an option.",
        "Message the user about it.",
        "A notification could go to the employee.",
        "An upgrade is possible.",
        "You could order more storage.",
    ],
)
def test_each_way_of_offering_an_action_is_found(sentence):
    assert named_actions(sentence), sentence


def test_action_pairs_come_from_suggestions_and_the_proposal_card():
    pairs = action_pairs(
        [suggestion(), suggestion("employee_notification", "Acme-Thinkpad-8")],
        ("remediation_ticket", "acme-x-1"),
    )
    assert pairs == {
        ("upgrade_order", "acme-macbook-4"),
        ("employee_notification", "acme-thinkpad-8"),
        ("remediation_ticket", "acme-x-1"),
    }


# ---- the reply check inside verify_prose --------------------------------------------------------------------


@pytest.fixture
def low_disk(real_db_path, real_conn):
    comp = compose(
        [run(real_db_path, real_conn, "low_disk_devices", {})],
        resolve=repo_for(real_conn).resolve,
    )
    return comp, dataset_as_of(real_conn)


def _chat(comp):
    return dataclasses.replace(
        comp,
        outcome="chat",
        claims=[],
        evidence=[],
        entity_lists=[],
        notes=[],
        coverage=None,
        suggested_actions=[],
        fallback_headline="Happy to help with your fleet.",
    )


REMEMBERED = {
    "labels": frozenset({"acme-macbook-4", "acme-thinkpad-8"}),
    "numbers": frozenset({"2.0", "2.6"}),
}


def _rules(verdict):
    return {rule for rule, _ in parse_reply_problems(verdict.problems)}


def test_the_bad_offer_is_dropped_on_a_chat_turn(low_disk):
    comp, as_of = low_disk
    reply = f"Here is where things stand. {BAD_OFFER}"
    verdict = verify_prose(_chat(comp), as_of, explanation=reply, memory=REMEMBERED)
    assert verdict.explanation == "Here is where things stand."
    assert _rules(verdict) == {"unbacked_action"}
    assert verdict.problems[:2] == ("reply:unbacked_action@1", "reply:dropped=1")


def test_the_offer_is_kept_on_an_advise_turn_that_has_the_upgrade_and_names_only_it(low_disk):
    comp, as_of = low_disk
    advise = dataclasses.replace(comp, suggested_actions=[suggestion()])
    one = "If you want, we can propose a storage upgrade for acme-macbook-4."
    reply = f"There is one available step. {one}"
    verdict = verify_prose(advise, as_of, explanation=reply, memory=REMEMBERED)
    assert verdict.problems == () and verdict.explanation == reply


def test_the_offer_is_dropped_on_an_advise_turn_when_it_also_names_an_unbacked_replacement(
    low_disk,
):
    comp, as_of = low_disk
    advise = dataclasses.replace(comp, suggested_actions=[suggestion()])
    verdict = verify_prose(
        advise, as_of, explanation=f"There is one step. {BAD_OFFER}", memory=REMEMBERED
    )
    assert verdict.explanation == "There is one step."
    assert _rules(verdict) == {"unbacked_action"}


def test_a_list_answers_next_steps_back_its_reply_even_though_they_join_the_envelope_later(
    low_disk,
):
    comp, as_of = low_disk
    reply = "There is a storage upgrade for acme-macbook-4 you could look at."
    plain = verify_prose(comp, as_of, explanation=f"Two are low. {reply}", memory=REMEMBERED)
    assert _rules(plain) == {"unbacked_action"}
    backed = verify_prose(
        comp,
        as_of,
        explanation=f"Two are low. {reply}",
        memory=REMEMBERED,
        suggestions=[suggestion()],
    )
    assert backed.problems == ()


def test_an_action_the_conversation_already_holds_may_be_recalled_on_a_chat_turn(low_disk):
    comp, as_of = low_disk
    held = {**REMEMBERED, "actions": frozenset({"remediation_ticket|acme-macbook-4"})}
    ok = "The ticket for acme-macbook-4 is still waiting for a person."
    verdict = verify_prose(_chat(comp), as_of, explanation=ok, memory=held)
    assert verdict.problems == () and verdict.explanation == ok
    other = "The ticket for acme-thinkpad-8 is still waiting for a person."
    assert "unbacked_action" in _rules(
        verify_prose(_chat(comp), as_of, explanation=f"Hello. {other}", memory=held)
    )


def test_a_chat_reply_may_describe_the_four_actions_without_tying_one_to_a_device(low_disk):
    comp, as_of = low_disk
    ok = "I can propose four actions, and a person approves each one."
    verdict = verify_prose(_chat(comp), as_of, explanation=ok)
    assert verdict.problems == () and verdict.explanation == ok


def test_a_refusals_reply_is_not_checked_for_actions(low_disk):
    comp, as_of = low_disk
    denied = dataclasses.replace(comp, outcome="denied", reason_code="not_eligible")
    verdict = verify_prose(denied, as_of, explanation="You could open a ticket instead.")
    assert verdict.problems == ()


def test_the_remembered_proposals_and_suggestions_become_backed_actions(real_db_path):
    facts = memory.memory_facts(memory.ConversationMemory())
    assert facts["actions"] == frozenset()
    held = memory.ConversationMemory(
        turns=[
            memory.TurnMemory(
                turn=1,
                question="q",
                outcome="answer",
                suggested=["upgrade_order|acme-macbook-4"],
                proposals=[
                    memory.RememberedProposal(
                        proposal_id="prp_1",
                        action_type="remediation_ticket",
                        device_id="DEV1",
                        label="Acme-Thinkpad-8",
                        status="pending",
                    )
                ],
            )
        ]
    )
    assert memory.memory_facts(held)["actions"] == frozenset(
        {"upgrade_order|acme-macbook-4", "remediation_ticket|acme-thinkpad-8"}
    )


# ---- effectiveness judgments --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "That may only help a little.",
        "Asking the owners won't help.",
        "It will not help much.",
        "A cleanup will fix it.",
        "An upgrade will solve this.",
        "That would solve the problem.",
        "A cleanup would fix it.",
        "Tidying up is not enough to get above the line.",
        "Ordering more storage would not fix it.",
    ],
)
def test_a_verdict_on_how_well_an_action_works_is_found(sentence):
    assert judges_effectiveness(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "acme-macbook-4 is at 2.0% free.",
        "There is a notification available for the owner of acme-macbook-4.",
        "The upgrade needs a person to approve it.",
        "That helps to see which one is lowest.",
    ],
)
def test_describing_readings_and_available_actions_is_not_an_effectiveness_judgment(sentence):
    assert judges_effectiveness(sentence) is False


def test_the_effectiveness_rule_drops_the_sentence_even_when_hedged(low_disk):
    comp, as_of = low_disk
    advise = dataclasses.replace(
        comp, suggested_actions=[suggestion("employee_notification", "acme-macbook-4")]
    )
    reply = "Two are low. Asking the owner of acme-macbook-4 may only help a little."
    verdict = verify_prose(advise, as_of, explanation=reply, memory=REMEMBERED)
    assert verdict.explanation == "Two are low."
    assert _rules(verdict) == {"effectiveness"}


def test_the_whole_bad_live_reply_keeps_only_what_the_facts_back(low_disk):
    comp, as_of = low_disk
    reply = (
        f"{BAD_JUDGMENT} Based on the readings we have, acme-macbook-4 is the lowest. {BAD_OFFER}"
    )
    advise = dataclasses.replace(
        comp, suggested_actions=[suggestion("employee_notification", "acme-macbook-4")]
    )
    verdict = verify_prose(advise, as_of, explanation=reply, memory=REMEMBERED)
    # the lead is the judgment, so there is no reply and the code headline stands
    assert verdict.explanation is None
    assert _rules(verdict) == {"effectiveness", "unbacked_action"}


# ---- the rule classes ---------------------------------------------------------------------------------------


def test_action_rules_are_their_own_class():
    assert ACTION_RULES == {"unbacked_action", "effectiveness"}
    assert not ACTION_RULES & (HEDGE_RULES | verify.FACT_RULES | verify.LENGTH_RULES)
