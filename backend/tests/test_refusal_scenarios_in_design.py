"""The other-company, typo, ambiguity and insufficient-evidence scenarios hold when the planner decides as its
prompt says: each phrasing is run through the real graph with a scripted planner and judged by the evaluators."""

from __future__ import annotations

import re

import pytest
from graph_support import answer_plan, make_service

from fleet_copilot.agent.identity import DEMO_IDENTITIES
from fleet_copilot.agent.llm import Plan, ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.config import REPO_ROOT
from fleet_copilot.evals.evaluators import check_turn
from fleet_copilot.evals.golden_ref import load_golden, resolve_expect
from fleet_copilot.evals.identifiers import foreign_identifiers
from fleet_copilot.evals.scenarios import expand, load_suites

SUITES = {s.suite: s for s in load_suites(REPO_ROOT / "evals" / "scenarios")}
GOLDEN = load_golden()
COMPANY_WORD = re.compile(r"\bacm\w*", re.IGNORECASE)
CASES = (
    ("guardrails", "other_company_generic"),
    ("guardrails", "company_typo_acmee"),
    ("guardrails", "other_company_by_name_acme_asks_globex"),
    ("guardrails", "other_company_by_name_globex_asks_initech"),
    ("guardrails", "other_company_by_name_initech_asks_acme"),
    ("guardrails", "other_company_device_by_id"),
    ("guardrails", "other_company_device_action"),
    ("actions", "insufficient_evidence_replacement"),
    ("grounding", "ambiguous_macbook_initech"),
)


def instances():
    wanted = {case_id for _, case_id in CASES}
    return [
        i for suite in SUITES.values() for c in suite.cases if c.id in wanted for i in expand(c)
    ]


def planned(case_id, say):
    """What a correct planner decides for each case."""
    if case_id == "company_typo_acmee":
        return [answer_plan(named_company=COMPANY_WORD.search(say).group(0))], []
    if case_id == "insufficient_evidence_replacement":
        plan = Plan(intent="propose", summary="flag", action_type="replacement_flag")
        call = tool_calls_message(("flag_device_for_replacement", {"device_id": "DXUCJ272DCYX"}))
        return [plan], [call, final_message()]
    if case_id == "ambiguous_macbook_initech":
        return [Plan(intent="clarify", summary="which", device_phrase="macbook")], []
    # A specific foreign id or hostname is not a general question: the planner may still call it another
    # company, and code answers "not found" all the same.
    return [Plan(intent="refuse", summary="x", refusal_reason="other_company")], []


@pytest.mark.parametrize("instance", instances(), ids=lambda i: i.id)
async def test_each_scenario_passes_in_design(real_db_path, tmp_path, instance):
    identity = DEMO_IDENTITIES[instance.user]
    turn = instance.turns[0]
    plans, acts = planned(instance.case_id, turn.say)
    llm = ScriptedLLM(plans=plans, acts=acts)
    service = make_service(real_db_path, llm, actions_db_path=tmp_path / "actions.db")
    result = await service.run_turn(identity, "c1", turn.say)
    checks = check_turn(
        resolve_expect(turn.expect, GOLDEN),
        result.envelope,
        result.trace,
        tenant=identity.company_id,
        repo_resolve=lambda ref: True,
        foreign_ids=foreign_identifiers(identity.company_id),
        proposals_created=0,
    )
    failed = [c for c in checks if not c.passed]
    assert checks and not failed, failed
