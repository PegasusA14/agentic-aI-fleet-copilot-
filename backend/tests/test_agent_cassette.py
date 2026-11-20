import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from fleet_copilot.agent.cassette import CassetteGateway, CassetteMiss, call_key
from fleet_copilot.agent.llm import (
    Explanation,
    Plan,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)

MESSAGES = [SystemMessage("rules"), HumanMessage("which devices are low on disk?")]
PLAN = Plan(intent="answer", summary="look it up", window_days=7)
EXPLANATION = Explanation(headline="Two devices are low", explanation=None)


def test_the_key_depends_on_kind_and_content_only():
    a = call_key("plan", MESSAGES)
    assert a == call_key(
        "plan", [SystemMessage("rules"), HumanMessage("which devices are low on disk?")]
    )
    assert a != call_key("explain", MESSAGES)
    assert a != call_key("plan", [SystemMessage("rules"), HumanMessage("another question")])


def test_tool_call_and_message_ids_do_not_change_the_key():
    one = [
        AIMessage(
            content="",
            tool_calls=[{"name": "t", "args": {"a": 1}, "id": "x1", "type": "tool_call"}],
        ),
        ToolMessage(content="r", tool_call_id="x1"),
    ]
    two = [
        AIMessage(
            content="",
            tool_calls=[{"name": "t", "args": {"a": 1}, "id": "y9", "type": "tool_call"}],
        ),
        ToolMessage(content="r", tool_call_id="y9"),
    ]
    assert call_key("act", one) == call_key("act", two)


async def test_record_then_replay_round_trips_all_three_kinds(tmp_path):
    path = tmp_path / "c.jsonl"
    act = tool_calls_message(("low_disk_devices", {}))
    inner = ScriptedLLM(plans=[PLAN], acts=[act], explains=[EXPLANATION])
    recorder = CassetteGateway(path, inner=inner)
    assert await recorder.plan(MESSAGES) == PLAN
    assert (await recorder.act(MESSAGES)).tool_calls[0]["name"] == "low_disk_devices"
    assert await recorder.explain(MESSAGES) == EXPLANATION
    assert len(path.read_text().splitlines()) == 3

    replay = CassetteGateway(path)  # no inner: offline
    assert await replay.plan(MESSAGES) == PLAN
    assert (await replay.act(MESSAGES)).tool_calls[0]["args"] == {}
    assert await replay.explain(MESSAGES) == EXPLANATION


async def test_a_hit_does_not_call_the_inner_gateway_again(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = ScriptedLLM(plans=[PLAN])
    gateway = CassetteGateway(path, inner=inner)
    await gateway.plan(MESSAGES)
    await gateway.plan(MESSAGES)  # would raise ScriptExhausted if it reached the inner gateway
    assert len(path.read_text().splitlines()) == 1


async def test_replay_only_miss_is_a_fixed_error(tmp_path):
    with pytest.raises(CassetteMiss) as err:
        await CassetteGateway(tmp_path / "missing.jsonl").plan(MESSAGES)
    assert "which devices" not in str(err.value)


async def test_a_final_message_without_tool_calls_round_trips(tmp_path):
    path = tmp_path / "c.jsonl"
    await CassetteGateway(path, inner=ScriptedLLM(acts=[final_message()])).act(MESSAGES)
    assert (await CassetteGateway(path).act(MESSAGES)).tool_calls == []


def test_a_corrupt_cassette_names_the_line_not_the_content(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text('{"key": "k", "kind": "plan", "response": {}}\nnot json at all\n')
    with pytest.raises(ValueError) as err:
        CassetteGateway(path)
    assert "line 2" in str(err.value) and "not json" not in str(err.value)


async def test_the_file_holds_keys_and_responses_only(tmp_path):
    path = tmp_path / "c.jsonl"
    await CassetteGateway(path, inner=ScriptedLLM(plans=[PLAN])).plan(MESSAGES)
    line = json.loads(path.read_text().splitlines()[0])
    assert set(line) == {"key", "kind", "response"}
    assert "which devices" not in path.read_text()
