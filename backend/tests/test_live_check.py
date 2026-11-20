import pytest
from graph_support import answer_plan, explanation, make_service

from fleet_copilot import live_check
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message


def test_the_cases_are_named_and_cover_the_demo_story():
    names = [c.name for c in live_check.CASES]
    assert len(names) == len(set(names))
    assert {
        "low_disk",
        "low_disk_follow_up",
        "typo_paraphrase",
        "cross_tenant",
        "injection",
        "off_topic",
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
