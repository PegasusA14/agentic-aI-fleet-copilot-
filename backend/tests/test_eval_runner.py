"""The eval runner end to end on a tiny scenario set, with a scripted model (no network, no cassette)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from graph_support import answer_plan, explanation

from fleet_copilot.actions.store import ActionStore, ProposalRow
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.llm import Plan, Reference, ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.config import Settings
from fleet_copilot.evals import runner
from fleet_copilot.evals.scenarios import load_suites
from fleet_copilot.evals.store import EvalStore
from fleet_copilot.observability.store import TraceStore

LOW_DISK = {"golden": "acme-001/low_disk_lt10/devices"}
TICKET = (
    "propose_action",
    {"action_type": "remediation_ticket", "device_id": "X", "check_id": "screen_lock"},
)
STARTED = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def suite_file(directory, name, cases):
    (directory / f"{name}.json").write_text(json.dumps({"suite": name, "cases": cases}))


def low_disk_case(case_id, expect, *, say="Which devices are low on disk space?"):
    return {
        "id": case_id,
        "user": "admin-acme",
        "turns": [{"say": say, "expect": expect}],
    }


def ticket_case(decisions, case_id="ticket"):
    return {
        "id": case_id,
        "user": "admin-acme",
        "turns": [
            {
                "say": "Open a ticket for 1LYSSFD074BB about its screen lock",
                "expect": {
                    "outcome": "proposal",
                    "proposal": {
                        "action_type": "remediation_ticket",
                        "target": "1LYSSFD074BB",
                        "status": "pending",
                    },
                    "decisions": decisions,
                },
            }
        ],
    }


def low_disk_script(copies=1):
    return dict(
        plans=[answer_plan() for _ in range(copies)],
        acts=[
            m
            for _ in range(copies)
            for m in (tool_calls_message(("low_disk_devices", {})), final_message())
        ],
        explains=[explanation() for _ in range(copies)],
    )


def ticket_script(copies=1):
    plan = Plan(
        intent="propose",
        summary="ticket",
        reference=Reference(kind="named", device_ref="1LYSSFD074BB"),
    )
    return dict(
        plans=[plan for _ in range(copies)],
        acts=[m for _ in range(copies) for m in (tool_calls_message(TICKET), final_message())],
        explains=[],
    )


@pytest.fixture
def settings(real_db_path, tmp_path):
    return Settings(
        data_path=real_db_path, db_path=real_db_path, state_dir=tmp_path / "state", static_dir=None
    )


@pytest.fixture
def stores(tmp_path):
    return (
        EvalStore(tmp_path / "state" / "evals.db"),
        TraceStore(tmp_path / "state" / "traces.db", model_mode="eval-replay"),
    )


async def go(tmp_path, settings, stores, gateway, **kw):
    suites = load_suites(tmp_path / "scenarios")
    store, traces = stores
    return await runner.run(
        suites,
        gateway=gateway,
        settings=settings,
        store=store,
        traces=traces,
        run_id=kw.pop("run_id", "run1"),
        now=STARTED,
        **kw,
    )


def by_id(result):
    return {c.instance_id: c for c in result.cases}


@pytest.fixture
def scenarios(tmp_path):
    folder = tmp_path / "scenarios"
    folder.mkdir()
    return folder


async def test_a_passing_case_and_a_failing_case_with_a_readable_reason(
    tmp_path, scenarios, settings, stores
):
    suite_file(
        scenarios,
        "grounding",
        [
            low_disk_case("right", {"outcome": "answer", "devices": LOW_DISK}),
            low_disk_case("wrong", {"outcome": "answer", "devices": ["NOSUCHDEVICE"]}),
        ],
    )
    llm = ScriptedLLM(**low_disk_script(2))
    result = await go(tmp_path, settings, stores, llm)
    cases = by_id(result)
    assert cases["grounding.right#1"].passed
    wrong = cases["grounding.wrong#1"]
    assert not wrong.passed and not wrong.not_recorded
    failing = [c for c in wrong.checks if not c["passed"]]
    assert [c["name"] for c in failing] == ["devices"]
    assert "NOSUCHDEVICE" in failing[0]["detail"] and "missing" in failing[0]["detail"]
    assert result.totals["suites"]["grounding"]["passed"] == 1
    assert result.totals["suites"]["grounding"]["total"] == 2
    assert result.totals["overall"] == {"passed": 1, "total": 2, "rate": 0.5, "not_recorded": 0}
    assert runner.exit_code(result) == 1


async def test_a_cassette_miss_is_reported_as_not_recorded_not_a_crash(
    tmp_path, scenarios, settings, stores
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    result = await go(tmp_path, settings, stores, CassetteGateway(tmp_path / "missing.jsonl"))
    case = by_id(result)["grounding.right#1"]
    assert case.not_recorded and not case.passed
    assert [(c["name"], c["detail"]) for c in case.checks] == [("recorded", "not recorded")]
    assert result.totals["suites"]["grounding"]["not_recorded"] == 1
    assert result.totals["overall"]["not_recorded"] == 1
    assert runner.exit_code(result) == 1
    assert "not recorded" in runner.format_summary(result)


async def test_an_action_case_approves_once_and_the_duplicate_adds_nothing(
    tmp_path, scenarios, settings, stores
):
    steps = [
        {
            "do": "approve",
            "expect": {"status": "executed", "duplicate": False, "effects_delta": {"tickets": 1}},
        },
        {
            "do": "approve_again",
            "expect": {"status": "executed", "duplicate": True, "effects_delta": {}},
        },
    ]
    suite_file(scenarios, "actions", [ticket_case(steps)])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**ticket_script()))
    case = by_id(result)["actions.ticket#1"]
    assert case.passed, [c for c in case.checks if not c["passed"]]
    assert [e["do"] for e in case.effects] == ["approve", "approve_again"]
    assert case.effects[0]["delta"] == {"tickets": 1}
    assert case.effects[0]["after"]["tickets"] == case.effects[0]["before"]["tickets"] + 1
    assert case.effects[1]["delta"] == {} and case.effects[1]["duplicate"] is True


async def test_wrong_hash_reject_and_a_foreign_admin_are_mapped_to_their_messages(
    tmp_path, scenarios, settings, stores
):
    steps = [
        {"do": "approve_wrong_hash", "expect": {"error": "proposal changed", "effects_delta": {}}},
        {"do": "approve_as", "user": "admin-globex", "expect": {"error": "not_found"}},
        {"do": "reject", "expect": {"status": "rejected", "effects_delta": {}}},
        {"do": "approve_again", "expect": {"error": "proposal is rejected"}},
    ]
    suite_file(scenarios, "actions", [ticket_case(steps)])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**ticket_script()))
    case = by_id(result)["actions.ticket#1"]
    assert case.passed, [c for c in case.checks if not c["passed"]]
    assert [e["error"] for e in case.effects] == [
        "proposal changed",
        "not_found",
        None,
        "proposal is rejected",
    ]


async def test_an_unexpected_effect_delta_fails_with_the_table_named(
    tmp_path, scenarios, settings, stores
):
    steps = [{"do": "approve", "expect": {"status": "executed", "effects_delta": {}}}]
    suite_file(scenarios, "actions", [ticket_case(steps)])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**ticket_script()))
    case = by_id(result)["actions.ticket#1"]
    assert not case.passed
    detail = next(c["detail"] for c in case.checks if not c["passed"])
    assert "tickets" in detail and "expected" in detail


async def test_every_instance_gets_its_own_state_and_traces_go_to_the_shared_store(
    tmp_path, scenarios, settings, stores
):
    suite_file(
        scenarios,
        "grounding",
        [low_disk_case("one", {"outcome": "answer"}), low_disk_case("two", {"outcome": "answer"})],
    )
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script(2)))
    folders = sorted(p.name for p in (tmp_path / "state" / "evals" / "run1").iterdir())
    assert len(folders) == 2
    for name in folders:
        assert (tmp_path / "state" / "evals" / "run1" / name / "actions.db").exists()
        assert (tmp_path / "state" / "evals" / "run1" / name / "checkpoints.db").exists()
    _, traces = stores
    turns = {t["trace_id"]: t for t in traces.list_turns()}
    for case in result.cases:
        assert len(case.trace_ids) == 1
        assert case.trace_ids[0] in turns
        assert turns[case.trace_ids[0]]["model_mode"] == "eval-replay"


async def test_the_run_and_every_case_are_stored(tmp_path, scenarios, settings, stores):
    suite_file(
        scenarios,
        "grounding",
        [
            low_disk_case("right", {"outcome": "answer", "devices": LOW_DISK}),
            low_disk_case("wrong", {"outcome": "answer", "devices": ["NOSUCHDEVICE"]}),
        ],
    )
    store, _ = stores
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script(2)))
    runs = store.list_runs(10)
    assert [r["run_id"] for r in runs] == ["run1"]
    assert runs[0]["model_mode"] == "eval-replay"
    assert runs[0]["dataset_sha256"] and runs[0]["as_of"] == "2026-06-12T11:00:00Z"
    assert runs[0]["totals"]["overall"]["total"] == 2
    assert "low_disk_free_pct" in runs[0]["policy"]
    stored = store.get_run("run1")
    assert [c["instance_id"] for c in stored["cases"]] == ["grounding.right#1", "grounding.wrong#1"]
    right, wrong = stored["cases"]
    assert right["passed"] is True and wrong["passed"] is False
    assert right["questions"] == ["Which devices are low on disk space?"]
    assert right["user"] == "admin-acme" and right["trace_ids"] == list(
        by_id(result)["grounding.right#1"].trace_ids
    )
    assert any(not c["passed"] for c in wrong["checks"])
    assert store.get_run("nope") is None


async def test_the_report_is_written_as_json(tmp_path, scenarios, settings, stores):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script()))
    path = runner.write_report(result, tmp_path / "reports")
    assert path == tmp_path / "reports" / "run1.json"
    report = json.loads(path.read_text())
    assert report["run_id"] == "run1" and report["model_mode"] == "eval-replay"
    assert report["totals"]["overall"]["passed"] == 1
    assert report["cases"][0]["instance_id"] == "grounding.right#1"


async def test_exit_code_is_zero_only_when_everything_passed_and_was_recorded(
    tmp_path, scenarios, settings, stores
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script()))
    assert runner.exit_code(result) == 0
    summary = runner.format_summary(result)
    assert "grounding" in summary and "1/1" in summary and "100.0%" in summary
    empty = runner.RunResult(
        run_id="x", started_at="", model_mode="m", dataset_sha256="", as_of="", policy={}, cases=[]
    )
    assert runner.exit_code(empty) == 1  # nothing ran, nothing proven


async def test_only_one_suite_runs_when_asked(tmp_path, scenarios, settings, stores):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    suite_file(scenarios, "trends", [low_disk_case("other", {"outcome": "answer"})])
    result = await go(
        tmp_path, settings, stores, ScriptedLLM(**low_disk_script()), only_suite="trends"
    )
    assert [c.instance_id for c in result.cases] == ["trends.other#1"]
    with pytest.raises(ValueError, match="no such suite"):
        await go(tmp_path, settings, stores, ScriptedLLM(), only_suite="nonsense", run_id="r2")


async def test_a_paraphrased_case_runs_each_phrasing_in_its_own_conversation(
    tmp_path, scenarios, settings, stores
):
    case = low_disk_case(
        "multi",
        {"outcome": "answer", "devices": LOW_DISK},
        say=["Which devices are low on disk?", "low disk?"],
    )
    suite_file(scenarios, "grounding", [case])
    llm = ScriptedLLM(**low_disk_script(2))
    result = await go(tmp_path, settings, stores, llm)
    assert [c.instance_id for c in result.cases] == ["grounding.multi#1", "grounding.multi#2"]
    assert all(c.passed for c in result.cases)
    assert [c.phrasing_index for c in result.cases] == [1, 2]


# ---- audit completeness ----------------------------------------------------------------------------


async def test_audit_completeness_is_one_extra_guardrails_case(
    tmp_path, scenarios, settings, stores
):
    steps = [{"do": "approve", "expect": {"status": "executed", "effects_delta": {"tickets": 1}}}]
    suite_file(scenarios, "guardrails", [ticket_case(steps)])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**ticket_script()))
    audit = by_id(result)["guardrails.audit_completeness"]
    assert audit.passed and audit.suite == "guardrails"
    assert "1 proposal" in audit.checks[0]["detail"]
    assert result.totals["suites"]["guardrails"]["total"] == 2


async def test_audit_completeness_is_not_added_when_guardrails_did_not_run(
    tmp_path, scenarios, settings, stores
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script()))
    assert "guardrails.audit_completeness" not in by_id(result)


async def test_audit_completeness_is_not_recorded_when_actions_ran_but_made_no_proposals(
    tmp_path, scenarios, settings, stores
):
    suite_file(scenarios, "actions", [low_disk_case("nothing", {"outcome": "answer"})])
    suite_file(scenarios, "guardrails", [low_disk_case("plain", {"outcome": "answer"})])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script(2)))
    audit = by_id(result)["guardrails.audit_completeness"]
    assert audit.not_recorded and not audit.passed
    assert audit.checks[0]["detail"] == runner.NOT_RECORDED
    stored = stores[0].get_run("run1")
    assert stored is not None
    assert not [c for c in stored["cases"] if c["case_id"] == "audit_completeness"][0]["passed"]
    assert result.totals["overall"]["not_recorded"] == 1


async def test_audit_completeness_still_passes_with_no_proposals_when_actions_did_not_run(
    tmp_path, scenarios, settings, stores
):
    suite_file(scenarios, "guardrails", [low_disk_case("plain", {"outcome": "answer"})])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script()))
    assert by_id(result)["guardrails.audit_completeness"].passed


def test_audit_completeness_names_a_proposal_without_its_audit_entries(tmp_path):
    store = ActionStore(tmp_path / "actions.db")
    with store.transaction() as conn:
        for pid, status in (("prp_a", "pending"), ("prp_b", "executed")):
            store.insert_proposal(
                conn,
                ProposalRow(
                    proposal_id=pid,
                    company_id="acme-001",
                    actor_id="admin-acme",
                    action_type="remediation_ticket",
                    target_device_id="D1",
                    params={},
                    payload_hash="h" + pid,
                    evidence_refs=[],
                    card={},
                    status=status,
                ),
            )
        store.append_audit(
            conn, "proposal_created", company_id="acme-001", actor_id="a", proposal_id="prp_a"
        )
    check = runner.audit_completeness([tmp_path / "actions.db"])
    assert not check.passed
    assert "prp_b" in check.detail and "proposal_created" in check.detail
    assert "proposal_executed" in check.detail
    assert "prp_a" not in check.detail


# ---- the command line --------------------------------------------------------------------------------


def cli_setup(monkeypatch, tmp_path, real_db_path, scenarios):
    from fleet_copilot.evals import __main__ as cli

    monkeypatch.setenv("FLEET_DB_PATH", str(real_db_path))
    monkeypatch.setenv("FLEET_STATE_DIR", str(tmp_path / "cli-state"))
    monkeypatch.setattr(cli, "SCENARIOS", scenarios)
    monkeypatch.setattr(cli, "CASSETTE", tmp_path / "cassettes" / "eval.jsonl")
    monkeypatch.setattr(cli, "REPORTS", tmp_path / "reports")
    return cli


def test_replay_with_no_cassette_reports_every_case_as_not_recorded(
    tmp_path, scenarios, real_db_path, monkeypatch, capsys
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    assert cli.main(["run"]) == 1
    out = capsys.readouterr().out
    assert "grounding" in out and "0/1" in out and "overall" in out and "not recorded" in out
    reports = list((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["totals"]["overall"]["not_recorded"] == 1
    assert EvalStore(tmp_path / "cli-state" / "evals.db").list_runs(5)[0]["model_mode"] == (
        "eval-replay"
    )
    assert not (tmp_path / "cassettes").exists()  # replay never writes the cassette


def test_refresh_without_live_is_refused_and_an_unknown_suite_is_a_clean_error(
    tmp_path, scenarios, real_db_path, monkeypatch, capsys
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    with pytest.raises(SystemExit):
        cli.main(["run", "--refresh"])
    assert cli.main(["run", "--suite", "nonsense"]) == 2
    assert "no such suite" in capsys.readouterr().err


async def test_a_stale_dataset_stops_the_run_before_any_case(tmp_path, scenarios, stores):
    stale = tmp_path / "old.db"
    import sqlite3

    sqlite3.connect(stale).close()
    settings = Settings(
        data_path=stale, db_path=stale, state_dir=tmp_path / "state", static_dir=None
    )
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    with pytest.raises(ValueError, match="make load-data"):
        await go(tmp_path, settings, stores, ScriptedLLM())
