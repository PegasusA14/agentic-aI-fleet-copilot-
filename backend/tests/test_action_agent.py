"""The agent proposes actions only through the four proposal tools, only on a propose plan, one per turn."""

import json

import pytest
from graph_support import (
    ACME_ADMIN,
    answer_plan,
    explanation,
    kinds,
    listed_ids,
    make_service,
)

from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent import texts
from fleet_copilot.agent.tools import PROPOSAL_TOOLS
from fleet_copilot.agent.llm import (
    Plan,
    Reference,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.data.loader import connect
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import FleetRepository, TenantContext

SCREEN_LOCK_QUESTION = "Which devices fail the screen lock check?"
ASK_TICKET = "Open a remediation ticket for the first one"
PROPOSE_TICKET = (
    "open_remediation_ticket",
    {"device_id": "WRONGDEVICE1", "check_id": "screen_lock"},
)


def propose_plan(summary="open a remediation ticket", **kw) -> Plan:
    return Plan(intent="propose", summary=summary, **kw)


def follow_up_propose(index=1, **kw) -> Plan:
    return propose_plan(reference=Reference(kind="ordinal", index=index), **kw)


def named_propose(device_ref) -> Plan:
    return propose_plan(reference=Reference(kind="named", device_ref=device_ref))


@pytest.fixture
def actions_db(tmp_path):
    return tmp_path / "state" / "actions.db"


async def ask(service, question, conversation="c1"):
    return await service.run_turn(ACME_ADMIN, conversation, question)


def state_of(path):
    store = ActionStore(path)
    with store.transaction() as conn:
        return (
            store.list(conn, "acme-001"),
            store.effect_counts(conn),
            store.audit(conn, None),
        )


def effect_total(effects):
    return sum(sum(rows.values()) for rows in effects.values())


def screen_lock_list_script():
    return dict(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(("compliance_failures_now", {"check_id": "screen_lock"})),
            final_message(),
        ],
        explains=[explanation(headline="Five of 10 devices fail screen_lock")],
    )


async def list_then(
    real_db_path, actions_db, plan, act_messages, extra_plans=(), later_explains=()
):
    first = screen_lock_list_script()
    llm = ScriptedLLM(
        plans=[*first["plans"], plan, *extra_plans],
        acts=[*first["acts"], *act_messages],
        explains=[*first["explains"], *later_explains],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    listed = await ask(service, SCREEN_LOCK_QUESTION)
    return service, llm, listed


# ---- the happy path ------------------------------------------------------------------------------


async def test_a_follow_up_ordinal_becomes_a_pending_proposal_for_the_injected_device(
    real_db_path, actions_db
):
    service, llm, listed = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [tool_calls_message(PROPOSE_TICKET), final_message()],
    )
    first_device = listed_ids(listed.envelope)[0]
    result = await ask(service, ASK_TICKET)
    envelope = result.envelope

    assert envelope.outcome == "proposal"
    card = envelope.proposal
    assert card.status == "pending" and card.action_type == "remediation_ticket"
    assert card.target_device_id == first_device  # the injected id, not the model's
    assert card.scope["check_id"] == "screen_lock"
    assert envelope.headline == texts.proposal_headline(
        texts.ACTION_LABELS["remediation_ticket"], card.target_label
    )
    assert card.evidence_refs and {e.ref for e in envelope.evidence} == set(card.evidence_refs)
    conn = connect(real_db_path, read_only=True)
    try:
        repo = FleetRepository(conn, TenantContext("acme-001", "test"))
        for ref in card.evidence_refs:
            assert repo.resolve(EvidenceRef.parse(ref)).ref == ref
    finally:
        conn.close()

    proposals, effects, audit = state_of(actions_db)
    assert [p.proposal_id for p in proposals] == [card.proposal_id]
    assert effect_total(effects) == 0
    assert [a["event"] for a in audit] == ["proposal_created"]
    assert len(llm.calls_of("explain")) == 1  # only the first (answer) turn explained
    assert "explain" not in kinds(result)
    assert kinds(result)[-3:] == ["compose", "verify", "respond"]


async def test_the_model_sees_no_card_text_employee_or_refs(real_db_path, actions_db):
    service, llm, _ = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [tool_calls_message(PROPOSE_TICKET), final_message()],
    )
    await ask(service, ASK_TICKET)
    reply = llm.calls_of("act")[-1].messages[-1]
    body = json.loads(reply.content)
    assert body == {
        "tool": "open_remediation_ticket",
        "status": "proposed",
        "action_type": "remediation_ticket",
    }


async def test_asking_again_returns_the_same_proposal(real_db_path, actions_db):
    # A proposal outcome keeps the list as the focus, so the same ordinal points at the same device.
    service, _, _ = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [
            tool_calls_message(PROPOSE_TICKET),
            final_message(),
            tool_calls_message(PROPOSE_TICKET),
            final_message(),
        ],
        extra_plans=[follow_up_propose()],
    )
    first = await ask(service, ASK_TICKET)
    second = await ask(service, ASK_TICKET)
    assert second.envelope.proposal.proposal_id == first.envelope.proposal.proposal_id
    proposals, _, audit = state_of(actions_db)
    assert len(proposals) == 1
    assert [a["event"] for a in audit] == ["proposal_created"]


# ---- refusals ------------------------------------------------------------------------------------


async def test_a_bulk_follow_up_becomes_per_device_advice_and_creates_nothing(
    real_db_path, actions_db
):
    plan = propose_plan(
        "flag them for replacement", reference=Reference(kind="all", list_hint="those")
    )
    service, llm, listed = await list_then(
        real_db_path,
        actions_db,
        plan,
        [],
        later_explains=[explanation(headline="Suggested next steps")],
    )
    assert len(listed_ids(listed.envelope)) >= 3
    result = await ask(service, "Flag all of them for replacement")
    assert result.envelope.outcome in ("answer", "no_match")
    assert result.envelope.reason_code != "unsupported_action"
    assert result.envelope.proposal is None
    assert {s.device_id for s in result.envelope.suggested_actions} <= set(
        listed_ids(listed.envelope)
    )
    proposals, effects, _ = state_of(actions_db)
    assert proposals == [] and effect_total(effects) == 0
    assert llm.remaining["act"] == 0 and len(llm.calls_of("act")) == 2


async def test_an_ineligible_action_is_denied_with_a_note_and_one_refusal_audit(
    real_db_path, actions_db
):
    llm = ScriptedLLM(
        plans=[named_propose("MT7PJB7N5LRE")],
        acts=[
            tool_calls_message(
                (
                    "open_remediation_ticket",
                    {
                        "device_id": "ignored",
                        "check_id": "disk_encryption",
                    },
                )
            ),
            final_message(),
        ],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    result = await ask(service, "Open a ticket for disk_encryption on MT7PJB7N5LRE")
    envelope = result.envelope
    assert envelope.outcome == "denied" and envelope.reason_code == "not_eligible"
    assert envelope.headline == texts.ACTION_NOT_ELIGIBLE
    assert envelope.notes and envelope.claims == [] and envelope.evidence == []
    proposals, effects, audit = state_of(actions_db)
    assert proposals == [] and effect_total(effects) == 0
    assert [a["event"] for a in audit] == ["proposal_refused"]


async def test_another_tenants_device_by_name_is_the_uniform_not_found(real_db_path, actions_db):
    llm = ScriptedLLM(plans=[named_propose("E4V0SB5I0MYU")])  # a Globex device
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    result = await ask(service, "Open a ticket for E4V0SB5I0MYU")
    assert result.envelope.outcome == "denied" and result.envelope.reason_code == "not_found"
    assert result.envelope.headline == texts.NOT_FOUND
    proposals, effects, audit = state_of(actions_db)
    assert proposals == [] and effect_total(effects) == 0 and audit == []


async def test_a_model_supplied_foreign_device_id_is_the_uniform_not_found(
    real_db_path, actions_db
):
    llm = ScriptedLLM(
        plans=[propose_plan()],
        acts=[
            tool_calls_message(
                (
                    "flag_device_for_replacement",
                    {"device_id": "E4V0SB5I0MYU"},
                )
            ),
            final_message(),
        ],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    result = await ask(service, "Flag E4V0SB5I0MYU")
    assert result.envelope.outcome == "denied" and result.envelope.reason_code == "not_found"
    assert result.envelope.headline == texts.NOT_FOUND
    proposals, _, _ = state_of(actions_db)
    assert proposals == []


async def test_without_an_action_store_the_tool_fails_closed(real_db_path):
    llm = ScriptedLLM(
        plans=[propose_plan()],
        acts=[tool_calls_message(PROPOSE_TICKET), final_message()],
    )
    service = make_service(real_db_path, llm)  # no actions_db_path
    result = await ask(service, "Open a ticket")
    assert result.envelope.outcome == "tool_error"


# ---- graph rules ---------------------------------------------------------------------------------


async def test_a_proposal_tool_on_an_answer_plan_is_an_input_error(real_db_path, actions_db):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(
                ("low_disk_devices", {}),
                (
                    "flag_device_for_replacement",
                    {"device_id": "8NM23J95R5I6"},
                ),
            ),
            final_message(),
        ],
        explains=[explanation()],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    result = await ask(service, "which devices are low on disk?")
    assert result.envelope.outcome == "answer"
    assert result.envelope.claims  # still composed from the read tool
    proposals, effects, audit = state_of(actions_db)
    assert proposals == [] and effect_total(effects) == 0 and audit == []
    results = [e for e in result.trace if e.kind == "tool_result" and e.tool == "flag_device_for_replacement"]
    assert [e.data["status"] for e in results] == ["invalid_input"]


async def test_the_model_is_told_why_the_call_was_rejected(real_db_path, actions_db):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(
                (
                    "flag_device_for_replacement",
                    {"device_id": "8NM23J95R5I6"},
                )
            ),
            final_message(),
        ],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    await ask(service, "flag it")
    reply = llm.calls_of("act")[-1].messages[-1]
    assert "proposal tools are only for action requests" in str(reply.content)


async def test_two_propose_calls_in_one_message_make_one_proposal(real_db_path, actions_db):
    second = ("flag_device_for_replacement", {"device_id": "OTHER"})
    service, _, listed = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [tool_calls_message(PROPOSE_TICKET, second), final_message()],
    )
    result = await ask(service, ASK_TICKET)
    assert result.envelope.outcome == "proposal"
    proposals, _, _ = state_of(actions_db)
    assert len(proposals) == 1
    calls = [e for e in result.trace if e.kind == "tool_call" and e.tool in PROPOSAL_TOOLS]
    assert len(calls) == 1


async def test_a_second_proposal_after_the_first_is_an_input_error(real_db_path, actions_db):
    service, llm, _ = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [
            tool_calls_message(PROPOSE_TICKET),
            tool_calls_message(PROPOSE_TICKET),
            final_message(),
        ],
    )
    result = await ask(service, ASK_TICKET)
    assert result.envelope.outcome == "proposal"
    assert "one proposal per turn" in str(llm.calls_of("act")[-1].messages[-1].content)
    proposals, _, audit = state_of(actions_db)
    assert len(proposals) == 1 and [a["event"] for a in audit] == ["proposal_created"]


async def test_a_different_proposal_tool_after_the_first_is_an_input_error(
    real_db_path, actions_db
):
    # The one-per-turn rule covers the whole class of proposal tools, not each tool by itself.
    service, llm, _ = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [
            tool_calls_message(PROPOSE_TICKET),
            tool_calls_message(("flag_device_for_replacement", {"device_id": "x"})),
            final_message(),
        ],
    )
    result = await ask(service, ASK_TICKET)
    assert result.envelope.outcome == "proposal"
    assert "one proposal per turn" in str(llm.calls_of("act")[-1].messages[-1].content)
    proposals, _, audit = state_of(actions_db)
    assert len(proposals) == 1 and [a["event"] for a in audit] == ["proposal_created"]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("create_upgrade_order", {"device_id": "MT7PJB7N5LRE", "component": "storage"}),
        ("open_remediation_ticket", {"device_id": "DLT35MHGTH2D", "check_id": "screen_lock"}),
        ("flag_device_for_replacement", {"device_id": "8NM23J95R5I6"}),
        ("notify_employee", {"device_id": "MT7PJB7N5LRE", "topic": "low_disk"}),
    ],
)
async def test_every_proposal_tool_is_dropped_without_a_propose_plan(
    real_db_path, actions_db, tool, args
):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message((tool, args)), final_message()],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    await ask(service, "do the thing")
    proposals, effects, audit = state_of(actions_db)
    assert proposals == [] and effect_total(effects) == 0 and audit == []
    reply = llm.calls_of("act")[-1].messages[-1]
    assert "proposal tools are only for action requests" in str(reply.content)


@pytest.mark.parametrize(
    "pair",
    [
        (
            ("open_remediation_ticket", {"device_id": "DLT35MHGTH2D", "check_id": "screen_lock"}),
            ("notify_employee", {"device_id": "MT7PJB7N5LRE", "topic": "low_disk"}),
        ),
        (
            ("create_upgrade_order", {"device_id": "MT7PJB7N5LRE", "component": "storage"}),
            ("flag_device_for_replacement", {"device_id": "8NM23J95R5I6"}),
        ),
    ],
)
async def test_two_different_proposal_tools_in_one_message_make_one_proposal(
    real_db_path, actions_db, pair
):
    llm = ScriptedLLM(
        plans=[propose_plan(reference=Reference(kind="named", device_ref=pair[0][1]["device_id"]))],
        acts=[tool_calls_message(*pair), final_message()],
    )
    service = make_service(real_db_path, llm, actions_db_path=actions_db)
    result = await ask(service, "propose both")
    proposals, _, _ = state_of(actions_db)
    assert len(proposals) <= 1
    calls = [e for e in result.trace if e.kind == "tool_call" and e.tool in PROPOSAL_TOOLS]
    assert [c.tool for c in calls] == [pair[0][0]]


async def test_the_trace_shows_enumerated_action_arguments_and_hides_the_device(
    real_db_path, actions_db
):
    service, _, _ = await list_then(
        real_db_path,
        actions_db,
        follow_up_propose(),
        [tool_calls_message(PROPOSE_TICKET), final_message()],
    )
    result = await ask(service, ASK_TICKET)
    call = next(e for e in result.trace if e.kind == "tool_call" and e.tool == "open_remediation_ticket")
    assert call.data["args"] == {
        "device_id": "applied",
        "check_id": "screen_lock",
    }
    assert "WRONGDEVICE1" not in repr(result.trace)


async def test_the_topology_has_no_new_nodes(real_db_path):
    llm = ScriptedLLM()
    service = make_service(real_db_path, llm)
    names = set(service._graph.get_graph().nodes) - {"__start__", "__end__"}
    assert names == {
        "scope", "plan", "reference", "act", "tools", "collect", "compose", "explain", "verify", "respond",
    }  # fmt: skip
