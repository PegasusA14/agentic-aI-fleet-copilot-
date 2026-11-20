import asyncio

import pytest
from graph_support import (
    ACME_ADMIN,
    GLOBEX_ADMIN,
    answer_plan,
    explanation,
    follow_up_plan,
    make_service,
)
from langgraph.checkpoint.memory import InMemorySaver

from fleet_copilot.agent.identity import thread_id
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.service import AgentService, DatasetUnavailable, TurnRecord


def low_disk_llm(turns=1):
    return ScriptedLLM(
        plans=[answer_plan() for _ in range(turns)],
        acts=[
            m
            for _ in range(turns)
            for m in (tool_calls_message(("low_disk_devices", {})), final_message())
        ],
        explains=[explanation() for _ in range(turns)],
    )


async def test_finished_conversation_locks_are_removed(real_db_path):
    service = make_service(real_db_path, low_disk_llm(2))
    await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    await service.run_turn(ACME_ADMIN, "c2", "low disk?")
    assert service._locks == {} and service._lock_users == {}


async def test_overlapping_turns_share_one_lock_and_clean_up_once(real_db_path):
    service = make_service(real_db_path, low_disk_llm(2))
    await asyncio.gather(
        service.run_turn(ACME_ADMIN, "c1", "low disk?"),
        service.run_turn(ACME_ADMIN, "c1", "low disk?"),
    )
    assert service._locks == {} and service._lock_users == {}


async def test_a_missing_database_is_dataset_unavailable(tmp_path):
    service = make_service(tmp_path / "nope.db", ScriptedLLM())
    with pytest.raises(DatasetUnavailable) as err:
        service.check_ready()
    assert str(tmp_path) not in str(err.value)
    with pytest.raises(DatasetUnavailable):
        await service.run_turn(ACME_ADMIN, "c1", "low disk?")


async def test_check_ready_returns_the_pinned_as_of(real_db_path):
    assert (
        make_service(real_db_path, ScriptedLLM())
        .check_ready()
        .isoformat()
        .startswith("2026-06-12T11:00:00")
    )


async def test_the_recorder_gets_the_whole_turn(real_db_path):
    records: list[TurnRecord] = []
    service = make_service(real_db_path, low_disk_llm(), recorder=records.append)
    result = await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert len(records) == 1
    record = records[0]
    assert record.identity is ACME_ADMIN and record.conversation_id == "c1"
    assert record.envelope == result.envelope and list(record.events) == result.trace
    assert record.trace_id == result.envelope.trace_id and record.elapsed_ms >= 0


async def test_a_closed_stream_is_recorded_without_an_envelope(real_db_path):
    records: list[TurnRecord] = []
    service = make_service(real_db_path, low_disk_llm(), recorder=records.append)
    stream = service.stream_turn(ACME_ADMIN, "c1", "low disk?")
    await anext(stream)
    await stream.aclose()
    assert len(records) == 1 and records[0].envelope is None and len(records[0].events) == 1


async def test_a_failing_recorder_never_breaks_the_turn(real_db_path):
    def boom(record):
        raise RuntimeError("disk full")

    service = make_service(real_db_path, low_disk_llm(), recorder=boom)
    result = await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert result.envelope.outcome == "answer"


async def test_deleting_a_conversation_forgets_its_lists(real_db_path):
    saver = InMemorySaver()
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("ordinal", 1)],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    service = make_service(real_db_path, llm, saver=saver)
    await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    key = {"configurable": {"thread_id": thread_id(ACME_ADMIN, "c1")}}
    assert await saver.aget_tuple(key) is not None
    await service.delete_conversation(ACME_ADMIN, "c1")
    assert await saver.aget_tuple(key) is None
    assert service._locks == {}


async def test_deleting_another_tenants_conversation_touches_nothing(real_db_path):
    saver = InMemorySaver()
    service = make_service(real_db_path, low_disk_llm(), saver=saver)
    await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    await service.delete_conversation(GLOBEX_ADMIN, "c1")  # a different thread key
    key = {"configurable": {"thread_id": thread_id(ACME_ADMIN, "c1")}}
    assert await saver.aget_tuple(key) is not None


async def test_open_passes_the_recorder_through(real_db_path, tmp_path):
    records: list[TurnRecord] = []
    async with AgentService.open(
        db_path=real_db_path,
        gateway=low_disk_llm(),
        checkpoint_path=tmp_path / "cp.db",
        recorder=records.append,
    ) as service:
        await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert len(records) == 1
