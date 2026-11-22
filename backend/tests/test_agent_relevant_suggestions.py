"""A list answer offers only the next steps that belong to its subject; an advise turn still offers all."""

from typing import get_args

import pytest
from graph_support import (
    ACME_ADMIN,
    GLOBEX_ADMIN,
    INITECH_ADMIN,
    answer_plan,
    explanation,
    make_service,
)

from fleet_copilot.actions.recommend import check_name
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.llm import Plan, ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.present import SUBJECT_ACTIONS
from fleet_copilot.agent.tools import execute_recommend
from fleet_copilot.repository import dataset_as_of


async def answer_to(real_db_path, identity, call):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(call), final_message()],
        explains=[explanation(headline="Matches found")],
    )
    return (await make_service(real_db_path, llm).run_turn(identity, "c1", "q")).envelope


# ---- the table is the one mapping --------------------------------------------------------------


def test_the_mapping_names_real_action_types_components_topics_and_checks():
    from fleet_copilot.actions.eligibility import TOPICS
    from fleet_copilot.agent.tools import CheckId
    from fleet_copilot.contracts import ActionType

    actions, checks = set(get_args(ActionType)), set(get_args(CheckId))
    for rules in SUBJECT_ACTIONS.values():
        for action, name, value in rules:
            assert action in actions
            if name == "topic":
                assert value in TOPICS
            elif name == "component":
                assert value in ("storage", "memory")
            elif name == "check_id":
                assert value in checks or value == "answer"
            else:
                assert name is None and value is None


# ---- low disk ---------------------------------------------------------------------------------


def assert_storage_steps(envelope):
    """Every suggestion is a storage upgrade or a low-disk notification; nothing else."""
    assert envelope.suggested_actions
    for s in envelope.suggested_actions:
        assert (s.action_type == "upgrade_order" and "storage upgrade" in s.label) or (
            s.action_type == "employee_notification" and "low disk space" in s.label
        ), s.label


@pytest.mark.parametrize("identity", [ACME_ADMIN, GLOBEX_ADMIN, INITECH_ADMIN])
async def test_a_low_disk_answer_offers_only_storage_upgrades_and_low_disk_notices(
    real_db_path, identity
):
    assert_storage_steps(await answer_to(real_db_path, identity, ("low_disk_devices", {})))


async def test_the_acme_low_disk_answer_shows_the_notice_the_battery_one_used_to_hide(
    real_db_path,
):
    envelope = await answer_to(real_db_path, ACME_ADMIN, ("low_disk_devices", {}))
    # Unfiltered, a device whose battery needs service gets the battery notice instead of the disk one.
    assert {s.action_type for s in envelope.suggested_actions} == {
        "upgrade_order",
        "employee_notification",
    }


@pytest.mark.parametrize("identity", [ACME_ADMIN, GLOBEX_ADMIN, INITECH_ADMIN])
async def test_a_disk_trend_gets_the_same_next_steps_as_the_low_disk_list(real_db_path, identity):
    assert_storage_steps(await answer_to(real_db_path, identity, ("trend", {"metric": "disk"})))


# ---- memory -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call", [("memory_pressure_now", {}), ("trend", {"metric": "memory"})], ids=["now", "trend"]
)
@pytest.mark.parametrize("identity", [ACME_ADMIN, GLOBEX_ADMIN, INITECH_ADMIN])
async def test_memory_answers_offer_only_memory_upgrades(real_db_path, identity, call):
    envelope = await answer_to(real_db_path, identity, call)
    assert envelope.suggested_actions
    assert {s.action_type for s in envelope.suggested_actions} == {"upgrade_order"}
    assert all("memory upgrade" in s.label for s in envelope.suggested_actions)


# ---- macOS ------------------------------------------------------------------------------------


@pytest.mark.parametrize("identity", [ACME_ADMIN, GLOBEX_ADMIN, INITECH_ADMIN])
async def test_a_macos_answer_offers_only_os_up_to_date_tickets(real_db_path, identity):
    envelope = await answer_to(
        real_db_path, identity, ("macos_version_check", {"older_than": "15"})
    )
    assert envelope.suggested_actions
    assert {s.action_type for s in envelope.suggested_actions} == {"remediation_ticket"}
    assert all("OS update ticket" in s.label for s in envelope.suggested_actions)


async def test_the_initech_macos_answer_has_no_storage_upgrade_and_no_screen_lock_ticket(
    real_db_path,
):
    envelope = await answer_to(
        real_db_path, INITECH_ADMIN, ("macos_version_check", {"older_than": "15"})
    )
    assert {s.device_id for s in envelope.suggested_actions} == {
        "DARFCP0BQM2G",
        "RLD7WV7J82SH",
        "SPKRRDF0L786",
        "XZYK11QTPWEC",
    }
    assert not any(
        "storage" in s.label or "screen lock" in s.label for s in envelope.suggested_actions
    )
    assert not any(s.action_type == "employee_notification" for s in envelope.suggested_actions)


# ---- compliance -------------------------------------------------------------------------------


@pytest.mark.parametrize("identity", [ACME_ADMIN, GLOBEX_ADMIN, INITECH_ADMIN])
async def test_a_compliance_answer_offers_tickets_for_the_checks_it_lists(real_db_path, identity):
    envelope = await answer_to(real_db_path, identity, ("compliance_failures_now", {}))
    shown = {c.text.split()[1] for c in envelope.claims}  # "Failing <check> (...)"
    assert envelope.suggested_actions
    assert {s.action_type for s in envelope.suggested_actions} == {"remediation_ticket"}
    for s in envelope.suggested_actions:
        assert any(f"{check_name(check)} ticket" in s.label for check in shown), (s.label, shown)


async def test_a_one_check_compliance_answer_offers_only_that_checks_tickets(real_db_path):
    envelope = await answer_to(
        real_db_path,
        INITECH_ADMIN,
        ("compliance_failures_now", {"check_id": "screen_lock"}),
    )
    assert envelope.suggested_actions
    assert all(
        s.action_type == "remediation_ticket" and "screen lock ticket" in s.label
        for s in envelope.suggested_actions
    )


async def test_a_compliance_trend_offers_tickets_for_its_check(real_db_path):
    envelope = await answer_to(
        real_db_path,
        ACME_ADMIN,
        ("trend", {"metric": "compliance", "check_id": "screen_lock"}),
    )
    assert envelope.suggested_actions
    assert all(
        s.action_type == "remediation_ticket" and "screen lock ticket" in s.label
        for s in envelope.suggested_actions
    )


# ---- battery ----------------------------------------------------------------------------------


async def test_a_battery_trend_offers_replacement_flags_and_the_battery_notice(real_db_path):
    envelope = await answer_to(real_db_path, ACME_ADMIN, ("trend", {"metric": "battery"}))
    assert envelope.suggested_actions
    for s in envelope.suggested_actions:
        assert s.action_type == "replacement_flag" or (
            s.action_type == "employee_notification" and "battery" in s.label
        ), s.label
    assert any(s.action_type == "replacement_flag" for s in envelope.suggested_actions)


# ---- cap, order, evidence, and what is left alone --------------------------------------------


async def test_the_answer_keeps_the_cap_and_every_suggestion_cites_shown_evidence(real_db_path):
    envelope = await answer_to(real_db_path, ACME_ADMIN, ("compliance_failures_now", {}))
    assert 0 < len(envelope.suggested_actions) <= 6
    shown = {e.ref for e in envelope.evidence}
    assert all(set(s.refs) <= shown for s in envelope.suggested_actions)


async def test_the_filter_runs_before_the_cap_so_relevant_steps_are_not_crowded_out(
    real_db_path, real_conn
):
    envelope = await answer_to(real_db_path, ACME_ADMIN, ("compliance_failures_now", {}))
    ctx = AgentContext(
        db_path=real_db_path,
        company_id="acme-001",
        actor_id="admin",
        as_of=dataset_as_of(real_conn),
    )
    unfiltered = execute_recommend(
        {
            "device_ids": list(
                dict.fromkeys(i.device_id for el in envelope.entity_lists for i in el.items)
            )
        },
        ctx,
    ).suggestions
    # the unfiltered top six holds no ticket at all for this answer; the filtered six are all tickets
    assert not any(s["action_type"] == "remediation_ticket" for s in unfiltered)
    assert all(s.action_type == "remediation_ticket" for s in envelope.suggested_actions)


async def test_an_advise_turn_still_recommends_over_every_rule(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), Plan(intent="advise", summary="recommend actions")],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation(), explanation(headline="Suggested next steps")],
    )
    service = make_service(real_db_path, llm)
    await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    advised = (await service.run_turn(ACME_ADMIN, "c1", "what should we do?")).envelope
    assert {"upgrade_order", "remediation_ticket"} <= {
        s.action_type for s in advised.suggested_actions
    }
