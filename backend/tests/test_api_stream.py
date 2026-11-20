import asyncio

import pytest
from graph_support import ACME_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.service import TurnResult
from fleet_copilot.api.stream import pump


async def numbers(count, state):
    try:
        for n in range(count):
            await asyncio.sleep(0)
            yield n
    finally:
        state["closed"] = True


async def test_pump_passes_items_through_in_order():
    state = {}
    assert [n async for n in pump(numbers(4, state))] == [0, 1, 2, 3]
    assert state["closed"]


async def test_pump_cancels_the_source_when_the_reader_leaves():
    state = {}
    stream = pump(numbers(10_000, state))
    await anext(stream)
    await stream.aclose()
    await asyncio.sleep(0.05)
    assert state["closed"]


async def test_pump_re_raises_a_source_failure_to_the_reader():
    async def broken():
        yield 1
        raise RuntimeError("boom")

    stream = pump(broken())
    assert await anext(stream) == 1
    with pytest.raises(RuntimeError):
        await anext(stream)


async def test_a_stalled_reader_does_not_hold_the_conversation_lock(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), answer_plan()],
        acts=[
            m
            for _ in range(2)
            for m in (tool_calls_message(("low_disk_devices", {})), final_message())
        ],
        explains=[explanation(), explanation()],
    )
    service = make_service(real_db_path, llm)
    stalled = pump(service.stream_turn(ACME_ADMIN, "c1", "low disk?"))
    await anext(stalled)  # the reader takes one event and then does nothing

    second = await asyncio.wait_for(
        service.run_turn(ACME_ADMIN, "c1", "low disk again?"), timeout=5
    )
    assert isinstance(second, TurnResult) and second.envelope.outcome == "answer"
    await stalled.aclose()
