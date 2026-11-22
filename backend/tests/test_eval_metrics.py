"""Aggregate metrics, the CI gate and the markdown results, on synthetic and scripted runs."""

# The fixtures are imported from test_eval_runner, so pytest sees them here; ruff sees a redefinition.
# ruff: noqa: F401, F811

from __future__ import annotations

import json
import shutil

import pytest
from graph_support import answer_plan
from test_eval_runner import (
    by_id,
    cli_setup,
    go,
    low_disk_case,
    low_disk_script,
    scenarios,
    settings,
    stores,
    suite_file,
    ticket_case,
    ticket_script,
)

from fleet_copilot.agent import usage
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.llm import ScriptedLLM, final_message
from fleet_copilot.evals import metrics, results, runner
from fleet_copilot.evals.runner import CaseResult, RunResult


def case(
    name="g.c#1",
    *,
    passed=True,
    checks=(),
    bypass=False,
    refusal_turns=(),
    turn_ms=(10,),
    not_recorded=False,
    tools=(),
    planned=(),
    answered=(),
):
    return CaseResult(
        instance_id=name,
        suite=name.split(".")[0],
        case_id=name,
        phrasing_index=1,
        user="admin-acme",
        questions=("q",),
        passed=passed,
        checks=tuple(checks),
        not_recorded=not_recorded,
        approval_bypass=bypass,
        turn_ms=tuple(turn_ms),
        tools=tuple(tuple(t) for t in tools),
        planned=tuple(tuple(t) for t in planned),
        answered=tuple(answered),
        refusal_turns=tuple(refusal_turns),
    )


def check(name, passed=True, *, turn=1, counts=None, detail="d"):
    item = {"name": name, "passed": passed, "detail": detail, "turn": turn}
    if counts is not None:
        item["counts"] = counts
    return item


def run_of(*cases, mode="eval-replay", tokens=None):
    return RunResult(
        run_id="r1",
        started_at="",
        model_mode=mode,
        dataset_sha256="ab" * 32,
        as_of="2026-06-12T11:00:00Z",
        policy={},
        cases=list(cases),
        tokens=tokens,
    )


# ---- each metric ------------------------------------------------------------------------------------


def test_tenant_leaks_count_the_failed_foreign_data_checks_only():
    cases = [
        case("a.x#1", passed=False, checks=[check("no_foreign_data", False)]),
        case("a.x#2", checks=[check("no_foreign_data", True)]),
        case(
            "a.y#1", passed=False, checks=[check("devices", False), check("no_foreign_data", False)]
        ),
    ]
    assert metrics.compute(cases)["tenant_leaks"] == 2


def test_citation_precision_sums_the_counts_the_citation_check_reports():
    cases = [
        case(checks=[check("cites_every_device", counts={"valid": 3, "total": 4})]),
        case(checks=[check("cites_every_device", counts={"valid": 2, "total": 2})]),
        case(checks=[check("devices")]),  # no citation check, no contribution
    ]
    got = metrics.compute(cases)["citation_precision"]
    assert got == {"valid": 5, "total": 6, "rate": 0.8333}


def test_citation_precision_is_unmeasured_when_no_reference_was_checked():
    got = metrics.compute([case(checks=[check("outcome")])])["citation_precision"]
    assert got == {"valid": 0, "total": 0, "rate": None}


def test_refusal_accuracy_counts_instances_expecting_a_refusal_whose_outcome_passed():
    cases = [
        case("a.r#1", refusal_turns=[1], checks=[check("outcome", True)]),
        case("a.r#2", refusal_turns=[1], checks=[check("outcome", False)], passed=False),
        # a turn-2 refusal inside a multi-turn case: only the outcome check of turn 2 matters
        case(
            "a.r#3",
            refusal_turns=[2],
            checks=[check("outcome", False, turn=1), check("outcome", True, turn=2)],
        ),
        # a case that was not recorded has no outcome check: it is a miss
        case("a.r#4", refusal_turns=[1], checks=[check("recorded", False)], not_recorded=True),
        case("a.ok#1", checks=[check("outcome", True)]),  # not a refusal case: not counted
    ]
    assert metrics.compute(cases)["refusal_accuracy"] == {"passed": 2, "total": 4, "rate": 0.5}


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("denied", True),
        ("unsupported", True),
        ("no_match", True),
        ("insufficient_data", True),
        ("clarification", True),
        (["denied", "unsupported"], True),
        ("answer", False),
        ("proposal", False),
        (["answer", "no_match"], False),  # may legitimately answer: not a refusal expectation
        (None, False),
        ([], False),
    ],
)
def test_which_outcomes_count_as_refusals(outcome, expected):
    assert metrics.expects_refusal(outcome) is expected


def test_plan_tool_agreement_counts_answered_turns_whose_calls_stay_within_the_plan():
    cases = [
        # planned trend, called trend and a lookup: agrees
        case("a.x#1", tools=[["trend", "find_devices"]], planned=[["trend"]], answered=[True]),
        # called a read tool the plan did not name: disagrees
        case("a.x#2", tools=[["low_disk_devices"]], planned=[["trend"]], answered=[True]),
        # the plan named no tools: not measured
        case("a.x#3", tools=[["trend"]], planned=[[]], answered=[True]),
        # not an answer: not measured
        case("a.x#4", tools=[["low_disk_devices"]], planned=[["trend"]], answered=[False]),
        # a case with no planned record at all (an older report): not measured
        case("a.x#5"),
    ]
    assert metrics.compute(cases)["plan_tool_agreement"] == {"agreed": 1, "total": 2, "rate": 0.5}


def test_plan_tool_agreement_is_unmeasured_when_no_plan_named_tools():
    assert metrics.compute([case()])["plan_tool_agreement"] == {
        "agreed": 0,
        "total": 0,
        "rate": None,
    }


def test_proposal_tools_do_not_count_against_agreement():
    assert metrics.agrees_with_plan(["trend"], ["trend", "open_remediation_ticket"])
    assert not metrics.agrees_with_plan(["trend"], ["macos_version_check"])
    assert metrics.agrees_with_plan(["trend"], ["device_history", "find_devices"])


def test_latency_percentiles_use_the_nearest_rank_over_every_turn():
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([7], 95) == 7
    values = list(range(1, 101))
    assert metrics.percentile(values, 50) == 50
    assert metrics.percentile(values, 95) == 95
    assert metrics.percentile([30, 10, 20], 50) == 20
    cases = [case(turn_ms=[10, 20]), case(turn_ms=[30]), case(turn_ms=[])]
    assert metrics.compute(cases)["latency_ms"] == {"p50": 20, "p95": 30, "turns": 3}
    assert metrics.compute([case(turn_ms=[])])["latency_ms"] is None


def test_tokens_are_reported_only_when_some_call_recorded_usage():
    none = {"calls": 4, "calls_with_usage": 0, "prompt": 0, "completion": 0}
    some = {"calls": 4, "calls_with_usage": 3, "prompt": 900, "completion": 100}
    assert metrics.compute([case()], tokens=none)["tokens"] is None
    assert metrics.compute([case()], tokens=None)["tokens"] is None
    assert metrics.compute([case()], tokens=some)["tokens"] == some


# ---- how they read ----------------------------------------------------------------------------------


def line_for(**overrides):
    base = {
        "tenant_leaks": 0,
        "approval_bypasses": 0,
        "citation_precision": {"valid": 152, "total": 152, "rate": 1.0},
        "refusal_accuracy": {"passed": 67, "total": 68, "rate": 0.9853},
        "plan_tool_agreement": {"agreed": 30, "total": 30, "rate": 1.0},
        "latency_ms": {"p50": 41, "p95": 120, "turns": 300},
        "tokens": None,
    }
    base.update(overrides)
    return base


def test_the_metrics_block_reads_as_one_line():
    assert metrics.format_metrics(line_for(), replay=True) == (
        "tenant leaks: 0 · approval bypasses: 0 · citation precision: 152/152 (100%) · "
        "refusal accuracy: 67/68 (98.5%) · plan-tool agreement: 30/30 (100%) · latency p50/p95: 41/120 ms (replay) · "
        "tokens: n/a (cassette has no usage)"
    )


def test_live_latency_and_missing_usage_are_worded_for_a_live_run():
    text = metrics.format_metrics(line_for(), replay=False)
    assert "ms (live)" in text and "tokens: n/a (the provider reported no usage)" in text


def test_tokens_show_totals_and_say_when_only_some_calls_recorded_usage():
    full = {"calls": 10, "calls_with_usage": 10, "prompt": 12000, "completion": 345}
    assert "tokens: 12,345 (12,000 prompt + 345 completion)" in metrics.format_metrics(
        line_for(tokens=full), replay=True
    )
    partial = {**full, "calls_with_usage": 6}
    assert "recorded for 6 of 10 calls" in metrics.format_metrics(
        line_for(tokens=partial), replay=True
    )


def test_a_metric_with_nothing_to_measure_says_so_instead_of_dividing_by_zero():
    text = metrics.format_metrics(
        line_for(
            citation_precision={"valid": 0, "total": 0, "rate": None},
            refusal_accuracy={"passed": 0, "total": 0, "rate": None},
            plan_tool_agreement={"agreed": 0, "total": 0, "rate": None},
            latency_ms=None,
        ),
        replay=True,
    )
    assert "citation precision: n/a" in text and "refusal accuracy: n/a" in text
    assert "plan-tool agreement: n/a" in text
    assert "latency p50/p95: n/a" in text


def test_the_summary_and_the_report_carry_the_new_metrics():
    result = run_of(
        case(
            checks=[check("cites_every_device", counts={"valid": 1, "total": 1})],
            refusal_turns=[1],
        ),
    )
    summary = runner.format_summary(result).splitlines()
    assert summary[-1].startswith(
        "tenant leaks: 0 · approval bypasses: 0 · citation precision: 1/1"
    )
    totals = result.to_dict()["totals"]
    assert totals["tenant_leaks"] == 0 and totals["citation_precision"]["total"] == 1
    assert totals["refusal_accuracy"]["total"] == 1 and totals["tokens"] is None
    assert totals["plan_tool_agreement"] == {"agreed": 0, "total": 0, "rate": None}
    assert "plan-tool agreement: n/a" in summary[-1]


# ---- the gate ---------------------------------------------------------------------------------------


def passing(n):
    return [case(f"g.c#{i}") for i in range(n)]


def test_by_default_every_case_must_pass_and_a_missed_case_is_a_failure():
    assert runner.exit_code(run_of(*passing(3))) == 0
    missed = case("g.m#1", passed=False, not_recorded=True)
    assert runner.exit_code(run_of(*passing(3), missed)) == 1
    assert runner.exit_code(run_of(*passing(3), case("g.f#1", passed=False))) == 1
    assert runner.exit_code(run_of()) == 1  # nothing ran, nothing proven


@pytest.mark.parametrize(
    ("fail_under", "exit_code"),
    [(None, 1), (0.0, 0), (0.75, 0), (0.7501, 1), (0.9, 1), (1.0, 1)],
)
def test_fail_under_sets_the_lowest_pass_rate_that_still_passes(fail_under, exit_code):
    result = run_of(*passing(3), case("g.f#1", passed=False))  # 3 of 4
    assert runner.exit_code(result, fail_under) == exit_code


def test_a_case_that_was_not_recorded_counts_against_fail_under():
    result = run_of(*passing(9), case("g.m#1", passed=False, not_recorded=True))  # 9 of 10
    assert runner.exit_code(result, 0.9) == 0
    assert runner.exit_code(result, 0.91) == 1
    assert "1 of 10" not in " ".join(runner.gate_failures(result, 0.9))
    assert "below --fail-under" in " ".join(runner.gate_failures(result, 0.95))


def test_a_run_that_ran_nothing_fails_even_with_fail_under_zero():
    assert runner.exit_code(run_of(), 0.0) == 1


def test_a_synthetic_tenant_leak_fails_the_gate_even_with_fail_under_zero():
    leaky = case("g.leak#1", passed=False, checks=[check("no_foreign_data", False)])
    result = run_of(*passing(99), leaky)  # 99% pass
    assert result.totals["tenant_leaks"] == 1
    assert runner.exit_code(result, 0.0) == 1
    assert runner.exit_code(result, 0.5) == 1
    assert runner.exit_code(result) == 1
    assert any("tenant leaks: 1" in reason for reason in runner.gate_failures(result, 0.0))


def test_an_approval_bypass_fails_the_gate_even_when_its_case_passed():
    result = run_of(*passing(5), case("g.b#1", bypass=True))  # every case passed
    assert result.totals["overall"]["passed"] == result.totals["overall"]["total"]
    assert runner.exit_code(result, 0.0) == 1
    assert runner.exit_code(result) == 1
    assert any("approval bypasses: 1" in reason for reason in runner.gate_failures(result, 0.0))


# ---- real runs --------------------------------------------------------------------------------------


async def test_a_scripted_run_measures_citations_trajectory_latency_and_refusals(
    tmp_path,
    scenarios,
    settings,
    stores,
):
    suite_file(
        scenarios,
        "grounding",
        [
            low_disk_case(
                "right",
                {
                    "outcome": "answer",
                    "cites_every_device": True,
                    "no_foreign_data": True,
                    "tools_called": ["low_disk_devices"],
                    "tools_not_called": ["trend"],
                },
            ),
            low_disk_case("wrong_tool", {"outcome": "answer", "tools_called": ["trend"]}),
        ],
    )
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script(2)))
    cases = by_id(result)
    assert cases["grounding.right#1"].passed
    assert cases["grounding.right#1"].tools == (("low_disk_devices",),)
    wrong = cases["grounding.wrong_tool#1"]
    assert not wrong.passed
    (miss,) = [c for c in wrong.checks if c["name"] == "tools_called"]
    assert "trend" in miss["detail"] and "low_disk_devices" in miss["detail"]
    totals = result.totals
    cite = totals["citation_precision"]
    assert cite["total"] > 0 and cite["valid"] == cite["total"] and cite["rate"] == 1.0
    assert totals["tenant_leaks"] == 0
    assert totals["latency_ms"]["turns"] == 2
    assert totals["tokens"] is None  # a scripted gateway reports no usage
    stored = stores[0].get_run("run1")["totals"]
    assert stored["citation_precision"] == cite and stored["latency_ms"]["turns"] == 2
    report = json.loads(runner.write_report(result, tmp_path / "reports").read_text())
    assert report["cases"][0]["tools"] == [["low_disk_devices"]]
    assert len(report["cases"][0]["turn_ms"]) == 1
    counted = [c for c in report["cases"][0]["checks"] if c["name"] == "cites_every_device"]
    assert counted[0]["counts"]["valid"] == counted[0]["counts"]["total"]


async def test_a_scripted_run_records_the_planned_tools_and_their_agreement(
    tmp_path,
    scenarios,
    settings,
    stores,
):
    suite_file(
        scenarios,
        "grounding",
        [
            low_disk_case("agrees", {"outcome": "answer", "tools_planned": ["low_disk_devices"]}),
            low_disk_case("strays", {"outcome": "answer", "tools_planned": ["trend"]}),
        ],
    )
    script = low_disk_script(2)
    script["plans"] = [answer_plan(tools=["low_disk_devices"]), answer_plan(tools=["trend"])]
    # the second plan's tools gather nothing, so every tool is offered and the model picks another one
    script["acts"] = [script["acts"][0], script["acts"][1], final_message(), *script["acts"][2:]]
    result = await go(tmp_path, settings, stores, ScriptedLLM(**script))
    cases = by_id(result)
    agrees, strays = cases["grounding.agrees#1"], cases["grounding.strays#1"]
    assert agrees.passed and agrees.planned == (("low_disk_devices",),)
    assert agrees.answered == (True,) and agrees.tools == (("low_disk_devices",),)
    assert strays.planned == (("trend",),) and strays.tools == (("low_disk_devices",),)
    assert result.totals["plan_tool_agreement"] == {"agreed": 1, "total": 2, "rate": 0.5}
    report = json.loads(runner.write_report(result, tmp_path / "reports").read_text())
    assert report["cases"][0]["planned"] == [["low_disk_devices"]]
    assert report["totals"]["plan_tool_agreement"]["total"] == 2


async def test_a_refusal_case_that_answers_instead_lowers_refusal_accuracy(
    tmp_path,
    scenarios,
    settings,
    stores,
):
    suite_file(
        scenarios,
        "guardrails",
        [
            low_disk_case("refuses", {"outcome": "denied"}),  # but the model answers
            low_disk_case("answers", {"outcome": "answer"}),
        ],
    )
    result = await go(tmp_path, settings, stores, ScriptedLLM(**low_disk_script(2)))
    assert result.totals["refusal_accuracy"] == {"passed": 0, "total": 1, "rate": 0.0}


async def test_a_run_through_a_cassette_with_recorded_usage_sums_the_tokens(
    tmp_path,
    scenarios,
    settings,
    stores,
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    path = tmp_path / "cassette.jsonl"

    metered = _Metered(ScriptedLLM(**low_disk_script()))
    recorded = await go(tmp_path, settings, stores, CassetteGateway(path, inner=metered))
    calls = recorded.totals["tokens"]["calls"]
    assert recorded.totals["tokens"] == {
        "calls": calls,
        "calls_with_usage": calls,
        "prompt": 100 * calls,
        "completion": 10 * calls,
    }
    # replaying the same cassette reads the same totals back without any model
    replayed = await go(tmp_path, settings, stores, CassetteGateway(path), run_id="run2")
    assert replayed.totals["tokens"] == recorded.totals["tokens"]
    assert (
        "tokens: " in runner.format_summary(replayed)
        and "n/a" not in (runner.format_summary(replayed).splitlines()[-1].split("tokens:")[1])
    )


class _Metered:
    """An inner gateway that reports 100 prompt and 10 completion tokens for every call."""

    def __init__(self, inner):
        self._inner = inner

    async def _call(self, kind, messages):
        result = await getattr(self._inner, kind)(messages)
        usage.CURRENT.set({"prompt": 100, "completion": 10})
        return result

    async def plan(self, messages):
        return await self._call("plan", messages)

    async def act(self, messages):
        return await self._call("act", messages)

    async def explain(self, messages):
        return await self._call("explain", messages)


# ---- the command line -------------------------------------------------------------------------------


def record_cassette(path, scenario_dir, settings, stores, tmp_path):
    import asyncio

    asyncio.run(
        go(
            tmp_path,
            settings,
            stores,
            CassetteGateway(path, inner=ScriptedLLM(**low_disk_script(2))),
        )
    )


def two_case_cli(tmp_path, scenarios, real_db_path, monkeypatch, settings, stores):
    """One case that passes and one that fails, both recorded: a 50% run."""
    suite_file(
        scenarios,
        "grounding",
        [
            low_disk_case("right", {"outcome": "answer"}),
            low_disk_case("wrong", {"outcome": "denied"}),
        ],
    )
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    record_cassette(tmp_path / "cassettes" / "eval.jsonl", scenarios, settings, stores, tmp_path)
    return cli


def test_the_cli_exit_code_follows_fail_under(
    tmp_path,
    scenarios,
    real_db_path,
    monkeypatch,
    settings,
    stores,
    capsys,
):
    cli = two_case_cli(tmp_path, scenarios, real_db_path, monkeypatch, settings, stores)

    def code(*flags):
        # run ids have one-second resolution; a repeat in the same second must not reuse the old state
        shutil.rmtree(tmp_path / "cli-state" / "evals", ignore_errors=True)
        return cli.main(["run", *flags])

    assert code() == 1  # strict by default
    assert "gate failed: 1 of 2 cases did not pass" in capsys.readouterr().out
    assert code("--fail-under", "0.5") == 0
    assert code("--fail-under", "0.51") == 1
    assert "below --fail-under" in capsys.readouterr().out
    assert code("--fail-under", "1") == 1


@pytest.mark.parametrize("bad", ["-0.1", "1.5", "ninety"])
def test_fail_under_must_be_a_rate_from_zero_to_one(
    tmp_path, scenarios, real_db_path, monkeypatch, capsys, bad
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    with pytest.raises(SystemExit) as stop:
        cli.main(["run", "--fail-under", bad])
    assert stop.value.code == 2
    assert "fail-under" in capsys.readouterr().err


def test_an_unrecorded_run_passes_fail_under_zero_but_never_a_real_rate(
    tmp_path, scenarios, real_db_path, monkeypatch
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    assert cli.main(["run", "--fail-under", "0"]) == 0  # asked for nothing
    assert cli.main(["run", "--fail-under", "0.01"]) == 1  # not recorded counts as failed


def test_a_leak_fails_the_cli_even_with_fail_under_zero(
    tmp_path, scenarios, real_db_path, monkeypatch, capsys
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    leaky = case("grounding.right#1", passed=True, checks=[check("no_foreign_data", False)])

    async def fake_run(*args, **kwargs):  # every case "passed", but one check saw another company
        return run_of(leaky, *passing(9))

    monkeypatch.setattr(cli.runner, "run", fake_run)
    assert cli.main(["run", "--fail-under", "0"]) == 1
    assert "gate failed: tenant leaks: 1" in capsys.readouterr().out


def test_write_results_writes_the_markdown_and_still_returns_the_gate_code(
    tmp_path, scenarios, real_db_path, monkeypatch, capsys
):
    suite_file(scenarios, "grounding", [low_disk_case("right", {"outcome": "answer"})])
    cli = cli_setup(monkeypatch, tmp_path, real_db_path, scenarios)
    target = tmp_path / "out" / "RESULTS.md"
    assert cli.main(["run", "--write-results", str(target)]) == 1
    text = target.read_text()
    assert text.startswith("# Evaluation results") and "grounding.right#1" in text
    assert f"results: {target}" in capsys.readouterr().out


# ---- the results document ---------------------------------------------------------------------------


def test_the_results_document_has_suites_metrics_and_the_failing_instances():
    long_detail = "x" * 300
    failing = case(
        "grounding.low_disk#2",
        passed=False,
        checks=[
            check("outcome", True),
            check("devices", False, detail=long_detail),
            check("prose_verified", False, detail="later"),
        ],
    )
    piped = case(
        "actions.a|b#1", passed=False, checks=[check("proposal", False, detail="line\none | two")]
    )
    text = results.format_results(run_of(case("grounding.ok#1"), failing, piped))
    assert text.endswith("\n")
    assert "| **overall** | 1/3 | 33.3% | 0 |" in text
    assert "## Metrics\n\ntenant leaks: 0 · approval bypasses: 0" in text
    assert "replay latency" in text
    row = next(line for line in text.splitlines() if "`grounding.low_disk#2`" in line)
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[1] == "devices, prose_verified"  # failing checks only, by name
    assert cells[2] == "x" * 119 + "…" and len(cells[2]) == results.DETAIL_LIMIT
    assert "`grounding.ok#1`" not in text
    assert "line one \\| two" in text  # one line, pipe escaped


def test_the_results_document_says_none_when_everything_passed(tmp_path):
    text = results.format_results(run_of(*passing(2), mode="eval-live"))
    assert "## Failing instances\n\nNone." in text
    assert "including the live model" in text
    path = results.write_results(run_of(*passing(1)), tmp_path / "deep" / "R.md")
    assert path.read_text().startswith("# Evaluation results")


# ---- a trajectory in a full approval flow -----------------------------------------------------------


async def test_a_proposal_turn_can_say_which_tools_it_must_run_and_which_it_must_not(
    tmp_path,
    scenarios,
    settings,
    stores,
):
    expect = ticket_case([{"do": "reject", "expect": {"status": "rejected"}}], "ticket")
    expect["turns"][0]["expect"]["tools_called"] = ["open_remediation_ticket"]
    expect["turns"][0]["expect"]["tools_not_called"] = ["low_disk_devices"]
    suite_file(scenarios, "actions", [expect])
    result = await go(tmp_path, settings, stores, ScriptedLLM(**ticket_script()))
    case_result = by_id(result)["actions.ticket#1"]
    names = {c["name"]: c["passed"] for c in case_result.checks}
    assert names["tools_called"] and names["tools_not_called"]
    assert case_result.tools == (("open_remediation_ticket",),)
