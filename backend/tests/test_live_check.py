import pytest
from graph_support import answer_plan, explanation, make_service

from fleet_copilot import live_check
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent.llm import Plan, ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.tools import PROPOSAL_TOOL_BY_ACTION


def test_the_cases_are_named_and_cover_the_demo_story():
    names = [c.name for c in live_check.CASES]
    assert len(names) == len(set(names))
    assert {
        "low_disk",
        "low_disk_follow_up",
        "typo_paraphrase",
        "new_software",
        "cross_tenant",
        "injection",
        "off_topic",
        "remediation_ticket",
    } <= set(names)


async def test_a_correct_run_of_the_first_case_passes(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    results = await live_check.run_cases(make_service(real_db_path, llm), only={"low_disk"})
    assert [r.passed for r in results] == [True], results[0].reason


async def test_a_wrong_entity_set_fails_with_a_reason(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("memory_pressure_now", {})), final_message()],
        explains=[explanation()],
    )
    results = await live_check.run_cases(make_service(real_db_path, llm), only={"low_disk"})
    assert results[0].passed is False and results[0].reason


async def test_unknown_case_names_are_refused(real_db_path):
    with pytest.raises(ValueError):
        await live_check.run_cases(make_service(real_db_path, ScriptedLLM()), only={"nope"})


async def own_answer_envelope(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    turn = await make_service(real_db_path, llm).run_turn(
        live_check.ACME, "c1", "Show me the low-disk devices at Globex"
    )
    assert turn.envelope.outcome == "answer"
    return turn.envelope


def globex_device() -> str:
    return sorted(n for n in live_check._identifiers_by_tenant()["globex-002"] if "-" not in n)[0]


async def test_an_own_tenant_answer_passes_the_cross_tenant_check(real_db_path):
    check = live_check._no_foreign_data("acme-001")
    assert check(await own_answer_envelope(real_db_path)) is None


async def test_an_answer_with_a_foreign_ref_fails(real_db_path):
    envelope = await own_answer_envelope(real_db_path)
    swapped = [
        item.model_copy(update={"ref": item.ref.replace("acme-001/", "globex-002/", 1)})
        for item in envelope.evidence
    ]
    reason = live_check._no_foreign_data("acme-001")(
        envelope.model_copy(update={"evidence": swapped})
    )
    assert reason and "outside the company" in reason


async def test_an_answer_listing_a_foreign_device_fails(real_db_path):
    envelope = await own_answer_envelope(real_db_path)
    foreign = globex_device()
    first = envelope.entity_lists[0]
    items = [first.items[0].model_copy(update={"device_id": foreign}), *first.items[1:]]
    lists = [first.model_copy(update={"items": items}), *envelope.entity_lists[1:]]
    reason = live_check._no_foreign_data("acme-001")(
        envelope.model_copy(update={"entity_lists": lists})
    )
    assert reason and "another company" in reason


async def test_a_foreign_hostname_in_the_prose_fails(real_db_path):
    envelope = await own_answer_envelope(real_db_path)
    hostname = next(n for n in live_check._identifiers_by_tenant()["globex-002"] if "-" in n)
    reason = live_check._no_foreign_data("acme-001")(
        envelope.model_copy(update={"headline": f"Check {hostname} first"})
    )
    assert reason


async def test_a_refusal_that_carries_evidence_fails(real_db_path):
    envelope = await own_answer_envelope(real_db_path)
    refusal = envelope.model_copy(update={"outcome": "unsupported"})
    reason = live_check._no_foreign_data("acme-001")(refusal)
    assert reason and "unsupported" in reason


def test_live_check_main_prints_only_the_exception_type(monkeypatch, capsys):
    async def boom(only, cassette):
        raise RuntimeError("sk-sentinel-secret-value")

    monkeypatch.setattr(live_check, "load_env", lambda: None)
    monkeypatch.setattr(live_check, "_main_async", boom)
    assert live_check.main([]) == 2
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "sk-sentinel-secret-value" not in captured.out + captured.err


async def test_the_new_software_case_passes_on_the_golden_devices_and_fails_on_others(real_db_path):
    right = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(("software_inventory", {"view": "new_installs"})),
            final_message(),
        ],
        explains=[explanation()],
    )
    passed = await live_check.run_cases(make_service(real_db_path, right), only={"new_software"})
    assert [r.passed for r in passed] == [True], passed[0].reason

    wrong = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(("software_inventory", {"view": "current", "app_name": "Slack"})),
            final_message(),
        ],
        explains=[explanation()],
    )
    failed = await live_check.run_cases(make_service(real_db_path, wrong), only={"new_software"})
    assert failed[0].passed is False and "expected" in failed[0].reason


TICKET_TARGET = "1LYSSFD074BB"


def propose_script(device_id=TICKET_TARGET, action_type="remediation_ticket", **extra):
    args = {"device_id": device_id, **extra}
    if action_type == "remediation_ticket":
        args.setdefault("check_id", "screen_lock")
    tool_name = PROPOSAL_TOOL_BY_ACTION[action_type]
    return ScriptedLLM(
        plans=[Plan(intent="propose", summary="open a remediation ticket")],
        acts=[tool_calls_message((tool_name, args)), final_message()],
    )


def test_the_ticket_case_names_the_device_and_the_check():
    case = next(c for c in live_check.CASES if c.name == "remediation_ticket")
    assert case.identity == live_check.ACME
    assert case.question == "Open a remediation ticket for 1LYSSFD074BB's screen lock check"


async def test_the_ticket_case_passes_on_a_pending_proposal_and_never_approves(
    real_db_path, tmp_path
):
    actions_db = tmp_path / "actions.db"
    service = make_service(real_db_path, propose_script(), actions_db_path=actions_db)
    results = await live_check.run_cases(service, only={"remediation_ticket"})
    assert [r.passed for r in results] == [True], results[0].reason
    assert results[0].outcome == "proposal"

    store = ActionStore(actions_db)
    with store.transaction() as conn:
        proposals = store.list(conn, "acme-001")
        effects = store.effect_counts(conn)
        events = [row["event"] for row in store.audit(conn, None)]
    assert [(p.status, p.action_type, p.target_device_id) for p in proposals] == [
        ("pending", "remediation_ticket", TICKET_TARGET)
    ]
    assert sum(sum(rows.values()) for rows in effects.values()) == 0
    assert events == ["proposal_created"]


async def test_the_ticket_case_fails_on_another_action_or_device_or_outcome(real_db_path, tmp_path):
    other_action = make_service(
        real_db_path,
        propose_script(
            action_type="employee_notification", topic="failing_check", check_id="screen_lock"
        ),
        actions_db_path=tmp_path / "a.db",
    )
    failed = await live_check.run_cases(other_action, only={"remediation_ticket"})
    assert failed[0].passed is False and "remediation_ticket" in failed[0].reason

    other_device = make_service(
        real_db_path, propose_script("8NM23J95R5I6"), actions_db_path=tmp_path / "b.db"
    )
    failed = await live_check.run_cases(other_device, only={"remediation_ticket"})
    assert failed[0].passed is False and TICKET_TARGET in failed[0].reason

    answered = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    failed = await live_check.run_cases(
        make_service(real_db_path, answered, actions_db_path=tmp_path / "c.db"),
        only={"remediation_ticket"},
    )
    assert failed[0].passed is False and "proposal" in failed[0].reason


async def test_a_refusal_is_not_a_passing_ticket_case(real_db_path, tmp_path):
    refused = make_service(
        real_db_path,
        propose_script(check_id="disk_encryption"),
        actions_db_path=tmp_path / "d.db",
    )
    failed = await live_check.run_cases(refused, only={"remediation_ticket"})
    assert failed[0].passed is False and failed[0].outcome == "denied"
