"""Repeat detection, and code-only suggested actions on list answers. Neither may change what a model reads."""

from agent_support import run
from graph_support import (
    ACME_ADMIN,
    INITECH_ADMIN,
    answer_plan,
    assert_clean,
    explanation,
    kinds,
    make_service,
)

from fleet_copilot.agent.compose import Composition
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.present import relevant_to
from fleet_copilot.agent.state import history_entry, turn_fingerprint
from fleet_copilot.agent.tools import execute_recommend
from fleet_copilot.agent.verify import composition_of
from fleet_copilot.repository import dataset_as_of

LOW = ("low_disk_devices", {})
MEMORY = ("memory_pressure_now", {})
LOW_DISK_DEVICES = ["MT7PJB7N5LRE", "8NM23J95R5I6"]


def scripted(*turns):
    """One plan, tool call and explanation per (tool call) turn."""
    acts = []
    for call in turns:
        acts += [tool_calls_message(call), final_message()]
    return ScriptedLLM(
        plans=[answer_plan() for _ in turns],
        acts=acts,
        explains=[explanation(headline="Two of 10 devices are low on disk") for _ in turns],
    )


async def ask(service, question, identity=ACME_ADMIN, conversation="c1"):
    return await service.run_turn(identity, conversation, question)


# ---- repeat detection --------------------------------------------------------------------------


async def test_the_same_question_twice_marks_the_second_answer_and_says_so_in_one_line(
    real_db_path,
):
    llm = scripted(LOW, LOW)
    service = make_service(real_db_path, llm)
    first = await ask(service, "which devices are low on disk?")
    second = await ask(service, "which devices are low on disk?")

    assert first.envelope.repeat_of is None
    assert second.envelope.repeat_of == first.envelope.trace_id == "t1"
    # the talk step still runs on a repeat, with a code-written hint to say so briefly
    assert len(llm.calls_of("explain")) == 2
    assert "Repeat note: this is the same result as turn 1;" in llm.calls_of("explain")[1].text
    assert "Repeat note" not in llm.calls_of("explain")[0].text
    # the code-written headline stands, the scripted reply was empty, the facts are the same
    assert second.envelope.headline == "2 of your 10 devices are under 10% free disk space."
    assert second.envelope.explanation is None
    assert [c.model_dump() for c in second.envelope.claims] == [
        c.model_dump() for c in first.envelope.claims
    ]
    assert second.envelope.table == first.envelope.table
    assert "explain" in kinds(second) and "verify" in kinds(second)
    compose_event = next(e for e in second.trace if e.kind == "compose")
    assert compose_event.data["repeat"] is True


async def test_a_third_ask_still_points_at_the_original_answer(real_db_path):
    llm = scripted(LOW, LOW, LOW)
    service = make_service(real_db_path, llm)
    await ask(service, "low disk?")
    await ask(service, "low disk?")
    third = await ask(service, "low disk?")
    assert third.envelope.repeat_of == "t1"
    assert len(llm.calls_of("explain")) == 3
    assert "same result as turn 1;" in llm.calls_of("explain")[2].text


async def test_an_answer_in_between_does_not_hide_the_repeat(real_db_path):
    llm = scripted(LOW, MEMORY, LOW)
    service = make_service(real_db_path, llm)
    first = await ask(service, "low disk?")
    other = await ask(service, "high memory?")
    again = await ask(service, "low disk?")
    assert other.envelope.repeat_of is None
    assert again.envelope.repeat_of == first.envelope.trace_id
    assert len(llm.calls_of("explain")) == 3


async def test_a_reworded_question_that_makes_the_same_calls_is_a_repeat(real_db_path):
    llm = scripted(LOW, LOW)
    service = make_service(real_db_path, llm)
    first = await ask(service, "which devices are low on disk?")
    again = await ask(service, "show me the disks that are nearly full")
    assert again.envelope.repeat_of == first.envelope.trace_id


async def test_conversations_and_tenants_do_not_share_repeats(real_db_path):
    llm = scripted(LOW, LOW)
    service = make_service(real_db_path, llm)
    await ask(service, "low disk?", conversation="c1")
    other = await ask(service, "low disk?", conversation="c2")
    assert other.envelope.repeat_of is None
    assert len(llm.calls_of("explain")) == 2


async def test_only_answers_are_repeats(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), answer_plan()],
        acts=[final_message(), final_message()],  # no tool call: nothing gathered -> clarification
    )
    service = make_service(real_db_path, llm)
    first = await ask(service, "hmm")
    second = await ask(service, "hmm")
    assert first.envelope.outcome == second.envelope.outcome == "clarification"
    assert second.envelope.repeat_of is None


def test_the_fingerprint_depends_on_what_was_answered_not_on_the_words(real_db_path, real_conn):
    ctx_as_of = dataset_as_of(real_conn)
    low = run(real_db_path, real_conn, "low_disk_devices", {})
    other = run(real_db_path, real_conn, "low_disk_devices", {"threshold_pct": 5})
    mem = run(real_db_path, real_conn, "memory_pressure_now", {})
    base = turn_fingerprint("answer", [low], ctx_as_of)
    assert base == turn_fingerprint("answer", [low], ctx_as_of)
    assert base != turn_fingerprint("advise", [low], ctx_as_of)
    assert base != turn_fingerprint("answer", [other], ctx_as_of)
    assert base != turn_fingerprint("answer", [mem], ctx_as_of)
    assert base != turn_fingerprint("answer", [low], ctx_as_of.replace(hour=12))
    # tool order does not matter
    assert turn_fingerprint("answer", [low, mem], ctx_as_of) == turn_fingerprint(
        "answer", [mem, low], ctx_as_of
    )


async def test_the_planner_sees_the_earlier_turn_but_never_its_fingerprint_or_trace_id(
    real_db_path,
):
    llm = scripted(LOW, MEMORY)
    service = make_service(real_db_path, llm)
    first = await ask(service, "low disk?")
    await ask(service, "high memory?")
    state = await service._graph.aget_state(
        {"configurable": {"thread_id": "acme-001:admin-acme:c1"}}
    )
    entry = state.values["history"][0]
    assert set(entry) == {"question", "outcome", "headline", "fingerprint", "trace_id"}
    assert entry["trace_id"] == first.envelope.trace_id
    second_plan_text = llm.calls_of("plan")[1].text
    assert "Turn 1 | answer | asked: low disk?" in second_plan_text
    assert entry["fingerprint"] not in "\n".join(call.text for call in llm.calls)
    assert first.envelope.trace_id not in second_plan_text


def test_history_entries_of_unanswered_turns_keep_exactly_the_old_keys(real_db_path):
    from fleet_copilot.agent import outcomes

    envelope = outcomes.clarification(
        "Which one?", [], company_id="acme-001", as_of=_as_of(), trace_id="t9"
    )
    assert set(history_entry("q", envelope)) == {"question", "outcome", "headline"}


def _as_of():
    from datetime import UTC, datetime

    return datetime(2026, 6, 12, 11, 0, tzinfo=UTC)


# ---- suggested actions on list answers ------------------------------------------------------------


def _ctx(real_db_path, real_conn, tenant="acme-001"):
    return AgentContext(
        db_path=real_db_path,
        company_id=tenant,
        actor_id="admin",
        as_of=dataset_as_of(real_conn),
    )


async def test_a_low_disk_answer_carries_what_recommend_returns_for_the_listed_devices_on_its_subject(
    real_db_path, real_conn
):
    llm = scripted(LOW)
    result = await ask(make_service(real_db_path, llm), "low disk?")
    envelope = result.envelope
    record = run(real_db_path, real_conn, "low_disk_devices", {})
    expected = execute_recommend(
        {"device_ids": LOW_DISK_DEVICES}, _ctx(real_db_path, real_conn), only=relevant_to([record])
    )
    assert expected.suggestions  # the dataset has eligible actions for these devices
    assert [s.model_dump(mode="json") for s in envelope.suggested_actions] == [
        {k: v for k, v in s.items()} for s in expected.suggestions
    ]
    # a sustained low disk is eligible for a storage upgrade, per the eligibility rules
    assert any(
        s.action_type == "upgrade_order" and s.device_id in LOW_DISK_DEVICES
        for s in envelope.suggested_actions
    )
    shown = {e.ref for e in envelope.evidence}
    assert all(set(s.refs) <= shown for s in envelope.suggested_actions)
    assert len(envelope.suggested_actions) <= 6
    assert_clean(result, llm)


async def test_suggestions_reach_only_the_reply_step_and_only_as_code_wrote_them(real_db_path):
    llm = scripted(LOW)
    result = await ask(make_service(real_db_path, llm), "low disk?")
    assert result.envelope.suggested_actions
    # planning and gathering never see them
    seen = "\n".join(call.text for call in llm.calls if call.kind != "explain")
    for suggestion in result.envelope.suggested_actions:
        assert suggestion.label not in seen and suggestion.reason not in seen
    # the reply step reads each next step with its reason, so it can only offer what code suggested
    (talk,) = llm.calls_of("explain")
    assert '"suggested_actions"' in talk.text
    for suggestion in result.envelope.suggested_actions:
        assert suggestion.label in talk.text and suggestion.reason in talk.text


async def test_the_model_calls_and_inputs_match_a_run_without_suggestions(real_db_path):
    llm = scripted(LOW)
    result = await ask(make_service(real_db_path, llm), "low disk?")
    plain = composition_of(result.envelope.model_copy(update={"suggested_actions": []})).facts()
    (explain,) = llm.calls_of("explain")
    assert str(plain["claims"][0]) in explain.text
    assert [c.kind for c in llm.calls] == ["plan", "act", "act", "explain"]


async def test_the_draft_the_model_and_verifier_read_carries_no_suggestions(real_db_path):
    llm = scripted(LOW)
    service = make_service(real_db_path, llm)
    result = await ask(service, "low disk?")
    state = await service._graph.aget_state(
        {"configurable": {"thread_id": "acme-001:admin-acme:c1"}}
    )
    draft = state.values["draft"]
    assert draft["suggested_actions"] == []
    assert {e["ref"] for e in draft["evidence"]} < {e.ref for e in result.envelope.evidence}
    assert state.values["extras"]["suggested_actions"]
    verify = next(e for e in result.trace if e.kind == "verify")
    assert verify.data["evidence_ok"] is True


async def test_a_macos_answer_gets_whatever_recommend_returns_for_its_devices(
    real_db_path, real_conn
):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("macos_version_check", {"older_than": "15"})), final_message()],
        explains=[explanation(headline="4 of your 5 Macs are behind macOS 15.")],
    )
    result = await ask(make_service(real_db_path, llm), "old macs?", identity=INITECH_ADMIN)
    ids = [i.device_id for i in result.envelope.entity_lists[0].items]
    record = run(
        real_db_path, real_conn, "macos_version_check", {"older_than": "15"}, "initech-003"
    )
    expected = execute_recommend(
        {"device_ids": ids},
        _ctx(real_db_path, real_conn, "initech-003"),
        only=relevant_to([record]),
    ).suggestions
    assert expected
    assert [s.model_dump(mode="json") for s in result.envelope.suggested_actions] == expected


async def test_inventory_answers_get_no_suggestions(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("find_devices", {"os_name": "macOS"})), final_message()],
        explains=[explanation(headline="7 of 10 devices match")],
    )
    result = await ask(make_service(real_db_path, llm), "list the macs")
    assert result.envelope.outcome == "answer" and result.envelope.suggested_actions == []
    assert result.envelope.table is not None


async def test_an_advise_turn_is_unchanged_and_not_doubled(real_db_path):
    from fleet_copilot.agent.llm import Plan

    llm = ScriptedLLM(
        plans=[answer_plan(), Plan(intent="advise", summary="recommend actions")],
        acts=[tool_calls_message(LOW), final_message()],
        explains=[explanation(), explanation(headline="Suggested next steps")],
    )
    service = make_service(real_db_path, llm)
    await ask(service, "low disk?")
    advised = await ask(service, "what should we do?")
    assert advised.envelope.suggested_actions
    assert len(advised.envelope.claims) == len(advised.envelope.suggested_actions)
    assert advised.envelope.repeat_of is None
    assert advised.envelope.table is None


def test_the_composition_facts_ignore_the_presentation_fields():
    plain = Composition("answer", None, fallback_headline="x")
    with_ui = Composition("answer", None, fallback_headline="x", summary="Two are low.")
    assert plain.facts() == with_ui.facts()


async def test_a_repeat_keeps_repeat_of_and_shows_the_one_short_line_the_model_wrote(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), answer_plan()],
        acts=[tool_calls_message(LOW), final_message(), tool_calls_message(LOW), final_message()],
        explains=[
            explanation(text="Two laptops are close to full."),
            explanation(text="Same as before."),
        ],
    )
    service = make_service(real_db_path, llm)
    first = await ask(service, "low disk?")
    second = await ask(service, "low disk?")
    assert second.envelope.repeat_of == first.envelope.trace_id
    assert (
        second.envelope.reply == "Same as before."
        and second.envelope.explanation == "Same as before."
    )
