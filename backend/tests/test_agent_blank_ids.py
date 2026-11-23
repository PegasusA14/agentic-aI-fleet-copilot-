"""A blank device id is no device. The model sometimes sends `device_ids: [""]` for a fleet-wide question; read as
an id, that turned "Any laptops with battery problems?" into "I can't find that in your fleet". Real ids, including
another tenant's, keep the uniform not-found."""

from __future__ import annotations

import pytest
from graph_support import ACME_ADMIN, GLOBEX_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent.graph import without_empty_ids
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message


def _llm(args: dict) -> ScriptedLLM:
    return ScriptedLLM(
        plans=[answer_plan(tools=["trend"])],
        acts=[tool_calls_message(("trend", args)), final_message()],
        explains=[explanation()],
    )


@pytest.mark.parametrize("blank", [[""], ["  "], ["", " "]])
async def test_a_blank_device_filter_checks_the_whole_fleet(real_db_path, blank):
    llm = _llm({"metric": "battery", "device_ids": blank})
    result = await make_service(real_db_path, llm).run_turn(
        GLOBEX_ADMIN, "c1", "Any laptops with battery problems?"
    )
    # Globex has no battery wear in the data: the honest answer is "nothing matched", not "not found".
    assert (result.envelope.outcome, result.envelope.reason_code) == (
        "no_match",
        "no_matching_records",
    )
    calls = [e.data["args"] for e in result.trace if e.kind == "tool_call"]
    assert calls == [{"metric": "battery"}]


def test_blank_ids_go_and_real_ones_stay():
    assert without_empty_ids({"device_ids": ["MT7PJB7N5LRE", "", " "]}) == {
        "device_ids": ["MT7PJB7N5LRE"]
    }
    assert without_empty_ids({"metric": "disk", "device_ids": [""]}) == {"metric": "disk"}
    assert without_empty_ids({"metric": "disk", "device_ids": []}) == {"metric": "disk"}


async def test_an_unknown_real_looking_id_keeps_the_uniform_not_found(real_db_path):
    foreign = "E4V0SB5I0MYU"  # a Globex device, asked about from Acme
    llm = _llm({"metric": "disk", "device_ids": [foreign]})
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN, "c1", "How is disk on that laptop this week?"
    )
    assert (result.envelope.outcome, result.envelope.reason_code) == ("denied", "not_found")
