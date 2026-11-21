"""An advise turn: code recommends evidence-backed actions for the devices in focus; nothing is proposed."""

import json

import pytest
from agent_support import ACME, context_for
from factories import make_row
from graph_support import (
    ACME_ADMIN,
    answer_plan,
    assert_clean,
    explanation,
    kinds,
    listed_ids,
    make_service,
)

from fleet_copilot import policy
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent import texts
from fleet_copilot.agent.graph import ONE_RECOMMENDATION_PER_TURN, _recommend_blocked
from fleet_copilot.agent.llm import (
    Plan,
    Reference,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.agent.tools import execute_recommend
from fleet_copilot.data.loader import load_dataset

LOW_DISK_QUESTION = "Which devices are low on disk?"
ADVISE = ("recommend_actions", {"device_ids": ["WRONGDEVICE1"]})


def advise_plan(summary="recommend actions", **kw) -> Plan:
    return Plan(intent="advise", summary=summary, **kw)


def named_advise(device_ref) -> Plan:
    return advise_plan(reference=Reference(kind="named", device_ref=device_ref))


@pytest.fixture
def actions_db(tmp_path):
    return tmp_path / "state" / "actions.db"


async def ask(service, question, conversation="c1"):
    return await service.run_turn(ACME_ADMIN, conversation, question)


def low_disk_turn():
    return dict(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation(headline="Two of 10 devices are low on disk")],
    )


async def low_disk_then(real_db_path, plan, acts, explains=(), *more_plans, **kwargs):
    first = low_disk_turn()
    llm = ScriptedLLM(
        plans=[*first["plans"], plan, *more_plans],
        acts=[*first["acts"], *acts],
        explains=[*first["explains"], *explains],
    )
    service = make_service(real_db_path, llm, **kwargs)
    listed = await ask(service, LOW_DISK_QUESTION)
    return service, llm, listed


def ticket_state(path):
    store = ActionStore(path)
    with store.transaction() as conn:
        return store.list(conn, "acme-001"), store.audit(conn, None)


# ---- the happy path ------------------------------------------------------------------------------


async def test_advise_after_a_low_disk_answer_recommends_for_the_listed_devices(
    real_db_path, actions_db
):
    service, llm, listed = await low_disk_then(
        real_db_path,
        advise_plan(),
        [],
        [explanation(headline="Suggested next steps", text=None)],
        actions_db_path=actions_db,
    )
    listed_devices = listed_ids(listed.envelope)
    result = await ask(service, "so what should I do?")
    envelope = result.envelope

    assert envelope.outcome == "answer" and envelope.reason_code is None
    assert envelope.suggested_actions
    suggested = {s.device_id for s in envelope.suggested_actions}
    assert suggested <= set(listed_devices)  # the resolved focus
    assert any(
        s.action_type == "upgrade_order" and s.label.startswith("Order a storage upgrade")
        for s in envelope.suggested_actions
    )
    # one claim per suggestion, in the same order, citing that suggestion's refs
    assert len(envelope.claims) == len(envelope.suggested_actions)
    shown = {item.ref for item in envelope.evidence}
    for claim, suggestion in zip(envelope.claims, envelope.suggested_actions, strict=True):
        assert claim.text == suggestion.reason
        assert claim.device_ids == [suggestion.device_id] and claim.count == 1
        assert claim.refs == suggestion.refs and set(claim.refs) <= shown
    assert len(envelope.suggested_actions) <= policy.MAX_SUGGESTIONS
    (suggestions_list,) = envelope.entity_lists
    assert suggestions_list.title == "Suggested actions"
    assert [i.device_id for i in suggestions_list.items] == list(
        dict.fromkeys(s.device_id for s in envelope.suggested_actions)
    )

    # nothing was proposed or written, and the explain step ran for the answer
    assert envelope.proposal is None
    proposals, audit = ticket_state(actions_db)
    assert proposals == [] and audit == []
    assert len(llm.calls_of("explain")) == 2
    assert "explain" in kinds(result)
    assert_clean(result, llm)
    # code made the one call itself: the model was asked to act only on the first (read) turn
    assert len(llm.calls_of("act")) == 2
    call = next(e for e in result.trace if e.kind == "tool_call" and e.tool == "recommend_actions")
    assert call.data["args"] == {"device_ids": len(listed_devices)}
    done = next(
        e for e in result.trace if e.kind == "tool_result" and e.tool == "recommend_actions"
    )
    assert done.data["status"] == "recommended"
    assert done.data["matches"] == len(envelope.suggested_actions)
    assert done.data["population"] == len(listed_devices)
    assert kinds(result).index("reference") < kinds(result).index("tool_call")


async def test_an_advise_turn_makes_no_act_call_on_the_model(real_db_path):
    service, llm, _ = await low_disk_then(
        real_db_path, advise_plan(), [], [explanation(headline="Suggested next steps")]
    )
    before = len(llm.calls_of("act"))
    result = await ask(service, "what should I do about these?")
    assert (
        len(llm.calls_of("act")) == before == 2
    )  # the low-disk turn's two rounds, none for advice
    assert result.envelope.outcome == "answer" and result.envelope.suggested_actions
    assert [e.tool for e in result.trace if e.kind == "tool_call"] == ["recommend_actions"]
    assert [e.tool for e in result.trace if e.kind == "tool_result"] == ["recommend_actions"]


async def test_an_advise_turn_alone_never_calls_the_act_model(real_db_path):
    llm = ScriptedLLM(plans=[named_advise("MT7PJB7N5LRE")], explains=[explanation()])
    result = await ask(make_service(real_db_path, llm), "how do I fix MT7PJB7N5LRE?")
    assert llm.calls_of("act") == [] and result.envelope.outcome == "answer"


async def test_a_named_device_advise_recommends_for_that_device_only(real_db_path):
    llm = ScriptedLLM(
        plans=[named_advise("MT7PJB7N5LRE")],
        explains=[explanation(headline="Suggested next steps")],
    )
    result = await ask(make_service(real_db_path, llm), "how do I fix MT7PJB7N5LRE?")
    envelope = result.envelope
    assert envelope.outcome == "answer"
    assert {s.device_id for s in envelope.suggested_actions} == {"MT7PJB7N5LRE"}
    assert any(s.action_type == "upgrade_order" for s in envelope.suggested_actions)
    assert_clean(result, llm)


async def test_advise_with_no_reference_uses_every_device_of_the_last_answer(real_db_path):
    service, _, listed = await low_disk_then(
        real_db_path,
        advise_plan(),  # no reference, no named device
        [],
        [explanation(headline="Suggested next steps")],
    )
    result = await ask(service, "any recommendations?")
    reference = [e for e in result.trace if e.kind == "reference"]
    assert reference and reference[0].data == {
        "kind": "all",
        "devices": len(listed_ids(listed.envelope)),
    }
    assert {s.device_id for s in result.envelope.suggested_actions} <= set(
        listed_ids(listed.envelope)
    )


async def test_advise_follows_advise_using_the_suggested_devices(real_db_path):
    service, llm, _ = await low_disk_then(
        real_db_path,
        advise_plan(),
        [],
        [
            explanation(headline="Suggested next steps"),
            explanation(headline="Suggested next steps"),
        ],
        advise_plan(),
    )
    first = await ask(service, "what should I do?")
    second = await ask(service, "and now?")
    assert second.envelope.outcome == "answer"
    assert {s.device_id for s in second.envelope.suggested_actions} <= {
        s.device_id for s in first.envelope.suggested_actions
    }


# ---- nothing eligible ----------------------------------------------------------------------------


async def test_nothing_eligible_is_no_match_with_a_note_per_device(tmp_path):
    db = tmp_path / "fleet.sqlite"
    data = tmp_path / "data.ndjson"
    data.write_text(
        "\n".join(json.dumps(make_row(device=d, battery=None)) for d in ("CALM1", "CALM2")) + "\n",
        encoding="utf-8",
    )
    load_dataset(data, db)
    # Two healthy devices listed by a first (answer) turn, then asked about.
    llm = ScriptedLLM(
        plans=[answer_plan(), advise_plan()],
        acts=[
            tool_calls_message(("find_devices", {})),
            final_message(),
        ],
        explains=[explanation(headline="Two devices")],
    )
    service = make_service(db, llm)
    await ask(service, "list my devices")
    result = await ask(service, "what should I do?")
    envelope = result.envelope
    assert envelope.outcome == "no_match" and envelope.reason_code == "no_matching_records"
    assert envelope.headline == texts.NO_SUGGESTIONS
    assert envelope.suggested_actions == [] and envelope.claims == []
    assert len(envelope.notes) == 2
    assert envelope.notes == [
        texts.no_suggestion_note("acme-host-CALM1"),
        texts.no_suggestion_note("acme-host-CALM2"),
    ]
    assert len(llm.calls_of("explain")) == 1  # only the first (answer) turn explained
    assert "explain" not in kinds(result)
    assert len(llm.calls_of("act")) == 2  # the model acted for the listing only


# ---- clarification, gates ------------------------------------------------------------------------


async def test_advise_with_nothing_in_focus_asks_which_devices(real_db_path):
    llm = ScriptedLLM(plans=[advise_plan()])
    result = await ask(make_service(real_db_path, llm), "what should I do?")
    envelope = result.envelope
    assert envelope.outcome == "clarification"
    assert envelope.clarification.question == texts.ADVISE_NEEDS_DEVICES
    assert llm.remaining["act"] == 0 and llm.calls_of("act") == []


async def test_advise_with_a_reference_but_nothing_in_focus_asks_which_devices(real_db_path):
    llm = ScriptedLLM(plans=[advise_plan(reference=Reference(kind="all"))])
    result = await ask(make_service(real_db_path, llm), "what should I do about those?")
    assert result.envelope.outcome == "clarification"
    assert result.envelope.clarification.question == texts.ADVISE_NEEDS_DEVICES


async def test_a_named_device_of_another_company_is_the_uniform_not_found(real_db_path):
    llm = ScriptedLLM(plans=[named_advise("E4V0SB5I0MYU")])  # a Globex device
    result = await ask(make_service(real_db_path, llm), "how do I fix E4V0SB5I0MYU?")
    assert result.envelope.outcome == "denied" and result.envelope.reason_code == "not_found"


async def test_recommend_actions_on_an_answer_plan_is_an_input_error(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(("low_disk_devices", {}), ADVISE),
            final_message(),
        ],
        explains=[explanation()],
    )
    result = await ask(make_service(real_db_path, llm), LOW_DISK_QUESTION)
    assert result.envelope.outcome == "answer"
    # the model's own recommend call was refused; any suggestions are the code's, for the listed devices
    assert {s.device_id for s in result.envelope.suggested_actions} <= {
        "MT7PJB7N5LRE",
        "8NM23J95R5I6",
    }
    results = [e for e in result.trace if e.kind == "tool_result" and e.tool == "recommend_actions"]
    assert [e.data["status"] for e in results] == ["invalid_input"]
    reply = [m for m in llm.calls_of("act")[-1].messages if getattr(m, "tool_call_id", None)]
    assert any("recommend_actions is only for advice requests" in str(m.content) for m in reply)


def test_a_second_recommendation_in_a_turn_is_blocked_by_the_gate():
    advise = {"plan": {"intent": "advise"}, "records": [{"tool": "recommend_actions"}]}
    assert _recommend_blocked("recommend_actions", advise) == ONE_RECOMMENDATION_PER_TURN
    assert _recommend_blocked("recommend_actions", {**advise, "records": []}) is None
    assert _recommend_blocked("low_disk_devices", advise) is None


# ---- a request to act on several devices is advice, not a refusal --------------------------------

NO_TICKET_NOTE = (
    "None of these devices qualifies for a remediation ticket; here is what the evidence supports."
)


def several_propose(**kw) -> Plan:
    return Plan(
        intent="propose",
        summary="raise a ticket for these",
        reference=Reference(kind="all", list_hint="those"),
        **kw,
    )


async def test_a_propose_for_several_devices_after_a_list_is_advice_for_those_devices(
    real_db_path, actions_db
):
    service, llm, listed = await low_disk_then(
        real_db_path,
        several_propose(),
        [],
        [explanation(headline="Suggested next steps")],
        actions_db_path=actions_db,
    )
    listed_devices = listed_ids(listed.envelope)
    assert len(listed_devices) >= 2
    result = await ask(service, "So shall we raise the ticket or something?")
    envelope = result.envelope

    assert envelope.outcome == "answer" and envelope.reason_code is None
    assert envelope.suggested_actions
    assert {s.device_id for s in envelope.suggested_actions} <= set(listed_devices)
    assert envelope.proposal is None
    proposals, audit = ticket_state(actions_db)
    assert proposals == [] and audit == []
    # code made the one recommendation call; the model was not asked to act on this turn
    assert len(llm.calls_of("act")) == 2
    assert [e.tool for e in result.trace if e.kind == "tool_call"] == ["recommend_actions"]
    assert_clean(result, llm)


async def test_a_ticket_request_for_devices_with_no_failing_check_falls_back_with_a_note(
    tmp_path,
):
    db = tmp_path / "fleet.sqlite"
    data = tmp_path / "data.ndjson"
    worn = {
        "battery_present": True,
        "condition": "Service Recommended",
        "cycle_count": 1200,
        "full_charge_capacity": 3000,
        "percentage": 80,
    }
    data.write_text(
        "\n".join(json.dumps(make_row(device=d, battery=worn)) for d in ("WORN1", "WORN2")) + "\n",
        encoding="utf-8",
    )
    load_dataset(data, db)
    llm = ScriptedLLM(
        plans=[answer_plan(), several_propose(action_type="remediation_ticket")],
        acts=[tool_calls_message(("find_devices", {})), final_message()],
        explains=[
            explanation(headline="Two devices"),
            explanation(headline="Suggested next steps"),
        ],
    )
    actions_db = tmp_path / "state" / "actions.db"
    service = make_service(db, llm, actions_db_path=actions_db)
    await ask(service, "list my devices")
    result = await ask(service, "raise a ticket for those")
    envelope = result.envelope

    assert envelope.outcome == "answer"
    assert envelope.suggested_actions  # the fallback: everything the evidence supports
    assert not any(s.action_type == "remediation_ticket" for s in envelope.suggested_actions)
    assert NO_TICKET_NOTE in envelope.notes
    assert envelope.proposal is None
    proposals, audit = ticket_state(actions_db)
    assert proposals == [] and audit == []
    assert_clean(result, llm)


async def test_a_request_for_one_action_type_keeps_only_that_type_and_adds_no_note(
    real_db_path, actions_db
):
    service, llm, listed = await low_disk_then(
        real_db_path,
        several_propose(action_type="upgrade_order"),
        [],
        [explanation(headline="Suggested next steps")],
        actions_db_path=actions_db,
    )
    result = await ask(service, "order upgrades for those")
    envelope = result.envelope
    assert envelope.outcome == "answer" and envelope.suggested_actions
    assert {s.action_type for s in envelope.suggested_actions} == {"upgrade_order"}
    assert not any("qualifies for" in n for n in envelope.notes)
    assert envelope.proposal is None
    assert_clean(result, llm)


async def test_a_propose_resolving_to_one_device_still_proposes(real_db_path, actions_db):
    plan = Plan(
        intent="propose",
        summary="open a remediation ticket",
        reference=Reference(kind="named", device_ref="MT7PJB7N5LRE"),
    )
    llm = ScriptedLLM(
        plans=[plan],
        acts=[
            tool_calls_message(
                (
                    "propose_action",
                    {
                        "action_type": "upgrade_order",
                        "device_id": "MT7PJB7N5LRE",
                        "component": "storage",
                    },
                )
            ),
            final_message(),
        ],
        explains=[explanation(headline="Proposed an upgrade")],
    )
    result = await ask(
        make_service(real_db_path, llm, actions_db_path=actions_db),
        "order a storage upgrade for MT7PJB7N5LRE",
    )
    assert result.envelope.outcome == "proposal" and result.envelope.proposal is not None
    assert result.envelope.suggested_actions == []


def test_the_tool_filters_to_an_action_type_and_flags_the_fallback(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn, ACME)
    ids = ["MT7PJB7N5LRE", "8NM23J95R5I6"]
    upgrades = execute_recommend({"device_ids": ids, "action_type": "upgrade_order"}, ctx)
    assert upgrades.suggestions and {s["action_type"] for s in upgrades.suggestions} == {
        "upgrade_order"
    }
    assert upgrades.advice_fallback is None
    # MT7PJB7N5LRE has no battery finding: a replacement flag falls back to what else is eligible
    fallback = execute_recommend(
        {"device_ids": ["MT7PJB7N5LRE"], "action_type": "replacement_flag"}, ctx
    )
    assert fallback.suggestions
    assert not any(s["action_type"] == "replacement_flag" for s in fallback.suggestions)
    assert fallback.advice_fallback == "replacement_flag"
    plain = execute_recommend({"device_ids": ids}, ctx)
    assert plain.advice_fallback is None


# ---- plan leniency end to end --------------------------------------------------------------------


async def test_an_unsupported_plan_without_a_reason_is_the_fixed_refusal_not_a_tool_error(
    real_db_path,
):
    plan = Plan.model_validate({"intent": "unsupported", "summary": "cannot do that"})
    llm = ScriptedLLM(plans=[plan])
    result = await ask(make_service(real_db_path, llm), "delete everything")
    envelope = result.envelope
    assert envelope.outcome == "unsupported" and envelope.reason_code == "unsupported_action"
    assert envelope.headline == texts.UNSUPPORTED_ACTION
    assert "error" not in kinds(result)


async def test_a_clarify_plan_without_a_question_asks_the_default_clarification(real_db_path):
    plan = Plan.model_validate(
        {"intent": "clarify", "summary": "need more", "clarification_question": "  "}
    )
    llm = ScriptedLLM(plans=[plan])
    result = await ask(make_service(real_db_path, llm), "hmm")
    assert result.envelope.outcome == "clarification"
    assert result.envelope.clarification.question == texts.DEFAULT_CLARIFICATION


# ---- the tool itself -----------------------------------------------------------------------------


def test_the_tool_records_suggestions_and_the_devices_it_considered(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn, ACME)
    record = execute_recommend({"device_ids": ["MT7PJB7N5LRE", "NOPE"]}, ctx)
    assert record.tool == "recommend_actions" and record.error is None
    assert record.suggestions and all(s["device_id"] == "MT7PJB7N5LRE" for s in record.suggestions)
    assert record.population == 1 and record.unresolved_ids == 1
    assert [f.device_id for f in record.findings] == ["MT7PJB7N5LRE"]


def test_the_tool_treats_unknown_and_foreign_devices_alike(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn, ACME)
    for ids in (["NOPE"], ["E4V0SB5I0MYU"]):
        record = execute_recommend({"device_ids": ids}, ctx)
        assert record.error == "not_found" and record.suggestions is None


def test_the_tool_without_devices_is_a_tool_error(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn, ACME)
    assert execute_recommend({"device_ids": None}, ctx).error == "tool_error"


def test_the_model_view_of_a_real_recommendation_is_counts_and_action_types_only(
    real_db_path, real_conn
):
    ctx = context_for(real_db_path, real_conn, ACME)
    record = execute_recommend({"device_ids": ["MT7PJB7N5LRE", "8NM23J95R5I6"]}, ctx)
    text = record.llm_view()
    body = json.loads(text)
    assert set(body) == {"tool", "status", "device_count", "suggestion_count", "action_types"}
    assert body["suggestion_count"] == len(record.suggestions) and body["action_types"]
    for s in record.suggestions:
        assert s["reason"] not in text and s["target_label"] not in text
        assert not any(ref in text for ref in s["refs"])
    assert "emp-" not in text and "MT7PJB7N5LRE" not in text
