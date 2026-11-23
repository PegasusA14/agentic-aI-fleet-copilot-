import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from fleet_copilot.agent.cassette import CassetteGateway, CassetteMiss, call_key
from fleet_copilot.agent.llm import (
    Plan,
    Reply,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)

MESSAGES = [SystemMessage("rules"), HumanMessage("which devices are low on disk?")]
PLAN = Plan(intent="answer", summary="look it up", window_days=7)
EXPLANATION = Reply(text="Two devices are low")


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


async def test_a_content_filter_refusal_is_recorded_and_replayed(tmp_path):
    from fleet_copilot.agent.llm import ContentFiltered

    path = tmp_path / "c.jsonl"
    recorder = CassetteGateway(path, inner=ScriptedLLM(plans=[ContentFiltered("jailbreak")]))
    with pytest.raises(ContentFiltered):
        await recorder.plan(MESSAGES)
    with pytest.raises(ContentFiltered) as err:
        await CassetteGateway(path).plan(MESSAGES)  # offline replay matches what the provider did
    assert err.value.category == "jailbreak"


# ---- token usage -------------------------------------------------------------------------------------


class Metered:
    """An inner gateway that reports token usage the way the live gateway does (a context variable)."""

    def __init__(self, inner, usage):
        self._inner, self._usage = inner, usage

    async def _call(self, kind, messages):
        from fleet_copilot.agent import usage

        result = await getattr(self._inner, kind)(messages)
        usage.CURRENT.set(self._usage)
        return result

    async def plan(self, messages):
        return await self._call("plan", messages)

    async def act(self, messages):
        return await self._call("act", messages)

    async def explain(self, messages):
        return await self._call("explain", messages)


def test_the_call_key_does_not_depend_on_usage_and_is_pinned():
    # Changing the key would orphan every committed cassette line, so it is pinned here.
    assert call_key("plan", MESSAGES) == (
        "2a4556b5f533c12cdff60716cac67354bcaad27e828e0fadb0f23c508f01b129"
    )


async def test_recorded_usage_is_stored_beside_the_response_and_summed_on_replay(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = Metered(
        ScriptedLLM(plans=[PLAN], explains=[EXPLANATION]), {"prompt": 120, "completion": 30}
    )
    recorder = CassetteGateway(path, inner=inner)
    await recorder.plan(MESSAGES)
    await recorder.explain(MESSAGES)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert all(line["usage"] == {"prompt": 120, "completion": 30} for line in lines)
    assert all(set(line) == {"key", "kind", "response", "usage"} for line in lines)
    both = {"calls": 2, "calls_with_usage": 2, "prompt": 240, "completion": 60}
    assert recorder.usage_total() == both

    replay = CassetteGateway(path)
    assert replay.usage_total()["calls"] == 0
    assert await replay.plan(MESSAGES) == PLAN
    assert await replay.explain(MESSAGES) == EXPLANATION
    assert replay.usage_total() == both


async def test_a_cassette_line_without_usage_still_replays_and_counts_no_tokens(tmp_path):
    path = tmp_path / "old.jsonl"
    await CassetteGateway(path, inner=ScriptedLLM(plans=[PLAN])).plan(MESSAGES)  # records no usage
    (line,) = path.read_text().splitlines()
    assert "usage" not in json.loads(line)
    replay = CassetteGateway(path)
    assert await replay.plan(MESSAGES) == PLAN
    assert replay.usage_total() == {
        "calls": 1,
        "calls_with_usage": 0,
        "prompt": 0,
        "completion": 0,
    }


async def test_a_mixed_cassette_counts_usage_only_for_the_lines_that_have_it(tmp_path):
    path = tmp_path / "mixed.jsonl"
    await CassetteGateway(path, inner=ScriptedLLM(plans=[PLAN])).plan(MESSAGES)
    metered = Metered(ScriptedLLM(explains=[EXPLANATION]), {"prompt": 7, "completion": 3})
    await CassetteGateway(path, inner=metered).explain(MESSAGES)
    replay = CassetteGateway(path)
    await replay.plan(MESSAGES)
    await replay.explain(MESSAGES)
    assert replay.usage_total() == {
        "calls": 2,
        "calls_with_usage": 1,
        "prompt": 7,
        "completion": 3,
    }


@pytest.mark.parametrize(
    "bad",
    [{"prompt": "9", "completion": 1}, {"prompt": 1}, {"prompt": -1, "completion": 0}, [1], 5],
)
async def test_a_malformed_usage_field_is_ignored_not_fatal(tmp_path, bad):
    path = tmp_path / "c.jsonl"
    await CassetteGateway(path, inner=ScriptedLLM(plans=[PLAN])).plan(MESSAGES)
    entry = json.loads(path.read_text())
    entry["usage"] = bad
    path.write_text(json.dumps(entry) + "\n")
    replay = CassetteGateway(path)
    assert await replay.plan(MESSAGES) == PLAN
    assert replay.usage_total()["calls_with_usage"] == 0


async def test_a_stale_usage_is_not_recorded_for_a_call_that_reported_none(tmp_path):
    from fleet_copilot.agent import usage

    usage.CURRENT.set({"prompt": 999, "completion": 999})  # left over from some earlier call
    path = tmp_path / "c.jsonl"
    await CassetteGateway(path, inner=ScriptedLLM(plans=[PLAN])).plan(MESSAGES)
    assert "usage" not in json.loads(path.read_text())


async def test_the_tool_limit_of_an_act_call_reaches_the_recording_model_but_not_the_key(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = ScriptedLLM(acts=[final_message()])
    await CassetteGateway(path, inner=inner).act(MESSAGES, tools=["trend", "find_devices"])
    assert inner.calls_of("act")[0].tools == ("trend", "find_devices")
    # replayed without the limit: the same recorded line answers it
    assert (await CassetteGateway(path).act(MESSAGES)).tool_calls == []


async def test_an_old_cassette_entry_with_an_explanation_replays_as_a_reply(tmp_path):
    import json

    path = tmp_path / "old.jsonl"
    old = {"headline": "Two devices are low", "explanation": "Both are laptops."}
    line = {"key": call_key("explain", MESSAGES), "kind": "explain", "response": old}
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    replay = CassetteGateway(path)
    assert await replay.reply(MESSAGES) == Reply(text="Both are laptops.")
    assert await replay.explain(MESSAGES) == Reply(text="Both are laptops.")


async def test_a_new_reply_is_recorded_as_text_and_replays(tmp_path):
    import json

    path = tmp_path / "new.jsonl"
    inner = ScriptedLLM(explains=[Reply(text="Hello there.")])
    assert (await CassetteGateway(path, inner=inner).reply(MESSAGES)).text == "Hello there."
    assert json.loads(path.read_text().splitlines()[0])["response"] == {"text": "Hello there."}
    assert (await CassetteGateway(path).reply(MESSAGES)).text == "Hello there."


async def test_a_question_nobody_recorded_says_so_instead_of_blaming_a_data_lookup(
    real_db_path, tmp_path
):
    from graph_support import make_service

    from fleet_copilot.agent import texts
    from fleet_copilot.agent.identity import DEMO_IDENTITIES

    service = make_service(real_db_path, CassetteGateway(tmp_path / "empty.jsonl"))
    outcome = await service.run_turn(DEMO_IDENTITIES["admin-acme"], "c1", "anything at all?")
    assert outcome.envelope.outcome == "tool_error"
    assert outcome.envelope.headline == texts.NOT_RECORDED
    assert outcome.envelope.headline != texts.TOOL_FAILED
    errors = [e.data.get("error") for e in outcome.trace if e.kind == "error"]
    assert errors == ["CassetteMiss"]  # what the evaluation counts as "not recorded"
