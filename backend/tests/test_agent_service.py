import asyncio

import pytest
from graph_support import (
    ACME_ADMIN,
    GLOBEX_ADMIN,
    answer_plan,
    explanation,
    follow_up_plan,
    kinds,
    make_service,
)
from langgraph.checkpoint.memory import InMemorySaver

from fleet_copilot import policy
from fleet_copilot.agent import graph as graph_module
from fleet_copilot.agent import texts
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.service import AgentService, InvalidQuestion
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent


def low_disk_script(extra_plans=(), extra_acts=(), extra_explains=()):
    return ScriptedLLM(
        plans=[answer_plan(), *extra_plans],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message(), *extra_acts],
        explains=[explanation(), *extra_explains],
    )


@pytest.mark.parametrize("question", ["", "   \n\t ", "x" * (policy.MAX_QUESTION_CHARS + 1)])
async def test_empty_and_oversized_questions_are_rejected_before_any_model_call(
    real_db_path, question
):
    llm = ScriptedLLM()
    with pytest.raises(InvalidQuestion):
        await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", question)
    assert llm.calls == []


async def test_a_bad_conversation_id_is_rejected(real_db_path):
    with pytest.raises(ValueError):
        await make_service(real_db_path, ScriptedLLM()).run_turn(ACME_ADMIN, "a:b", "hi")


async def test_the_stream_is_trace_events_then_exactly_one_envelope(real_db_path):
    service = make_service(real_db_path, low_disk_script())
    items = [item async for item in service.stream_turn(ACME_ADMIN, "c1", "low disk?")]
    assert all(isinstance(i, TraceEvent) for i in items[:-1])
    assert isinstance(items[-1], AnswerEnvelope)
    assert [i.kind for i in items[:-1]][0] == "scope" and items[-2].kind == "respond"


async def test_questions_are_trimmed_but_not_otherwise_changed(real_db_path):
    llm = low_disk_script()
    await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", "  low disk?  \n")
    assert "<user_question>\nlow disk?\n</user_question>" in llm.calls_of("plan")[0].text


async def test_a_crash_inside_the_graph_becomes_a_tool_error_without_leaking_details(
    real_db_path, monkeypatch
):
    service = make_service(real_db_path, ScriptedLLM())

    async def boom(*args, **kwargs):
        raise RuntimeError("/secret/path exploded")
        yield  # pragma: no cover  (makes this an async generator)

    monkeypatch.setattr(service._graph, "astream", boom)
    result = await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert (result.envelope.outcome, result.envelope.reason_code) == ("tool_error", "tool_failure")
    assert result.trace[-1].kind == "error" and result.trace[-1].data == {"error": "RuntimeError"}
    assert "secret" not in result.envelope.model_dump_json()


class DistinctiveCrash(Exception):
    pass


SECRET = "ZX-SECRET-7731 /var/private/host-4411"


@pytest.mark.parametrize("target", ["verify_prose", "verify_evidence", "next_history"])
async def test_a_crash_in_a_later_node_is_a_fixed_tool_error_with_only_the_type_name(
    real_db_path, monkeypatch, target
):
    # verify_prose / verify_evidence crash inside the verify node, next_history inside respond:
    # none of these is caught by the graph itself, so the service has to fail closed.
    def boom(*args, **kwargs):
        raise DistinctiveCrash(SECRET)

    monkeypatch.setattr(graph_module, target, boom)
    service = make_service(real_db_path, low_disk_script())
    items = [item async for item in service.stream_turn(ACME_ADMIN, "c1", "low disk?")]

    envelopes = [i for i in items if isinstance(i, AnswerEnvelope)]
    events = [i for i in items if isinstance(i, TraceEvent)]
    assert len(envelopes) == 1 and items[-1] is envelopes[0]
    envelope = envelopes[0]
    assert (envelope.outcome, envelope.reason_code) == ("tool_error", "tool_failure")
    assert envelope.headline == texts.TOOL_FAILED
    assert envelope.trace_id == events[-1].trace_id

    error_steps = [e for e in events if e.kind == "error"]
    assert len(error_steps) == 1 and events[-1] is error_steps[0]
    assert error_steps[0].data == {"error": "DistinctiveCrash"}
    assert [e.seq for e in events] == list(range(len(events)))
    for text in [envelope.model_dump_json(), *(e.model_dump_json() for e in events)]:
        assert "ZX-SECRET" not in text and "host-4411" not in text and "/var/private" not in text


THREAD = "acme-001:admin-acme:c1"


async def test_the_next_turn_still_works_after_a_crash(real_db_path, monkeypatch):
    # The same service and the same conversation: a crash must not leave the conversation locked.
    service = make_service(
        real_db_path,
        low_disk_script(
            extra_plans=[answer_plan()],
            extra_acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
            extra_explains=[explanation()],
        ),
    )

    def boom(*args, **kwargs):
        raise DistinctiveCrash(SECRET)

    with monkeypatch.context() as patch:
        patch.setattr(graph_module, "verify_prose", boom)
        crashed = await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert crashed.envelope.reason_code == "tool_failure"
    assert THREAD not in service._locks

    result = await asyncio.wait_for(service.run_turn(ACME_ADMIN, "c1", "low disk?"), timeout=5)
    assert result.envelope.outcome == "answer"


class HangsOnce(ScriptedLLM):
    """The first plan call never returns, like a gateway that stopped answering."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hung = asyncio.Event()

    async def plan(self, messages):
        if not self.hung.is_set():
            self.hung.set()
            await asyncio.Event().wait()
        return await super().plan(messages)


def hanging_service(real_db_path):
    llm = HangsOnce(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    service = make_service(real_db_path, llm)
    service._gateway_hung = llm.hung  # lets a test wait until the gateway call is really hanging
    return service


async def test_a_hung_gateway_times_out_into_one_tool_error_and_frees_the_conversation(
    real_db_path, monkeypatch
):
    monkeypatch.setattr(policy, "TURN_TIMEOUT_S", 0.05)
    service = hanging_service(real_db_path)

    async def collect():
        return [item async for item in service.stream_turn(ACME_ADMIN, "c1", "low disk?")]

    items = await asyncio.wait_for(collect(), timeout=5)
    envelopes = [i for i in items if isinstance(i, AnswerEnvelope)]
    events = [i for i in items if isinstance(i, TraceEvent)]
    assert len(envelopes) == 1 and items[-1] is envelopes[0]
    assert (envelopes[0].outcome, envelopes[0].reason_code) == ("tool_error", "tool_failure")
    assert envelopes[0].headline == texts.TOOL_FAILED
    error_steps = [e for e in events if e.kind == "error"]
    assert len(error_steps) == 1 and error_steps[0].data == {"error": "TimeoutError"}
    assert THREAD not in service._locks
    assert service._lock_users == {}

    # the same conversation on the same service still works
    again = await asyncio.wait_for(service.run_turn(ACME_ADMIN, "c1", "low disk?"), timeout=5)
    assert again.envelope.outcome == "answer"


async def test_closing_the_stream_mid_turn_releases_the_conversation(real_db_path):
    service = make_service(real_db_path, low_disk_script())
    stream = service.stream_turn(ACME_ADMIN, "c1", "low disk?")
    first = await asyncio.wait_for(stream.__anext__(), timeout=5)
    assert isinstance(first, TraceEvent) and first.kind == "scope"
    assert service._locks[THREAD].locked()
    await asyncio.wait_for(stream.aclose(), timeout=5)
    assert THREAD not in service._locks

    again = await asyncio.wait_for(service.run_turn(ACME_ADMIN, "c1", "low disk?"), timeout=5)
    assert again.envelope.outcome == "answer"


async def test_a_reader_that_disconnects_while_the_gateway_hangs_releases_the_conversation(
    real_db_path,
):
    service = hanging_service(real_db_path)

    async def read_everything():
        return [item async for item in service.stream_turn(ACME_ADMIN, "c1", "low disk?")]

    reader = asyncio.create_task(read_everything())
    await asyncio.wait_for(service._gateway_hung.wait(), timeout=5)
    assert service._locks[THREAD].locked()
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    assert THREAD not in service._locks

    again = await asyncio.wait_for(service.run_turn(ACME_ADMIN, "c1", "low disk?"), timeout=5)
    assert again.envelope.outcome == "answer"


async def test_an_envelope_from_another_turn_is_a_tool_error(real_db_path):
    saver = InMemorySaver()
    first = make_service(real_db_path, low_disk_script(), saver)
    await first.run_turn(ACME_ADMIN, "c1", "low disk?")  # trace id t1 is now checkpointed

    # A graph that ends without writing a new envelope leaves the old one in the checkpoint.
    second = AgentService(
        db_path=real_db_path,
        gateway=ScriptedLLM(),
        checkpointer=saver,
        trace_id_factory=lambda: "second-turn",
    )

    async def silent(*args, **kwargs):
        return
        yield  # pragma: no cover

    second._graph.astream = silent  # type: ignore[method-assign]
    result = await second.run_turn(ACME_ADMIN, "c1", "again")
    assert (result.envelope.outcome, result.envelope.reason_code) == ("tool_error", "tool_failure")
    assert result.trace[-1].data == {"error": "RuntimeError"}


async def test_conversations_survive_a_restart(real_db_path, tmp_path):
    checkpoints = tmp_path / "checkpoints.sqlite"
    first_llm = low_disk_script()
    async with AgentService.open(
        db_path=real_db_path, gateway=first_llm, checkpoint_path=checkpoints
    ) as service:
        first = await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert first.envelope.outcome == "answer"

    second_llm = ScriptedLLM(
        plans=[follow_up_plan("ordinal", 2)],
        acts=[tool_calls_message(("trend", {"metric": "disk", "window_days": 7})), final_message()],
        explains=[explanation(headline="Disk trend for that device")],
    )
    async with AgentService.open(
        db_path=real_db_path, gateway=second_llm, checkpoint_path=checkpoints
    ) as service:
        second = await service.run_turn(ACME_ADMIN, "c1", "trend for the second one")
    devices = {d for c in second.envelope.claims for d in c.device_ids}
    assert devices == {"8NM23J95R5I6"}  # the second entry of the list shown before the restart
    assert "Low disk" in second_llm.calls_of("plan")[0].text


async def test_the_same_conversation_id_is_a_different_conversation_per_actor(real_db_path):
    llm = low_disk_script(extra_plans=[follow_up_plan("ordinal", 1)])
    service = make_service(real_db_path, llm)
    await service.run_turn(ACME_ADMIN, "shared", "low disk?")
    other = await service.run_turn(GLOBEX_ADMIN, "shared", "the first one")
    assert other.envelope.clarification.question == texts.NEED_REFERENCE


async def test_turns_in_one_conversation_never_interleave(real_db_path):
    class SlowPlanner(ScriptedLLM):
        async def plan(self, messages):
            await asyncio.sleep(0.01)
            return await super().plan(messages)

    llm = SlowPlanner(
        plans=[answer_plan(), answer_plan()],
        acts=[
            tool_calls_message(("low_disk_devices", {})),
            final_message(),
            tool_calls_message(("memory_pressure_now", {})),  # a different answer: not a repeat
            final_message(),
        ],
        explains=[explanation(), explanation()],
    )
    service = make_service(real_db_path, llm)
    await asyncio.gather(
        service.run_turn(ACME_ADMIN, "c1", "first"), service.run_turn(ACME_ADMIN, "c1", "second")
    )
    assert [c.kind for c in llm.calls] == ["plan", "act", "act", "explain"] * 2
    state = await service._graph.aget_state(
        {"configurable": {"thread_id": "acme-001:admin-acme:c1"}}
    )
    assert [h["question"] for h in state.values["history"]] == ["first", "second"]


async def test_trace_ids_are_unique_per_turn_by_default(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), answer_plan()],
        acts=[
            tool_calls_message(("low_disk_devices", {})),
            final_message(),
            tool_calls_message(("low_disk_devices", {})),
            final_message(),
        ],
        explains=[explanation(), explanation()],
    )
    service = AgentService(db_path=real_db_path, gateway=llm, checkpointer=InMemorySaver())
    first = await service.run_turn(ACME_ADMIN, "c1", "one")
    second = await service.run_turn(ACME_ADMIN, "c1", "two")
    assert first.envelope.trace_id != second.envelope.trace_id
    assert len(first.envelope.trace_id) == 12
    assert kinds(first)[0] == "scope"


async def test_a_foreign_envelope_never_reaches_the_recorder_when_the_reader_leaves(real_db_path):
    saver = InMemorySaver()
    first = make_service(real_db_path, low_disk_script(), saver)
    await first.run_turn(ACME_ADMIN, "c1", "low disk?")  # trace id t1 is now checkpointed

    records = []
    second = AgentService(
        db_path=real_db_path,
        gateway=ScriptedLLM(),
        checkpointer=saver,
        trace_id_factory=lambda: "second-turn",
        recorder=records.append,
    )

    async def silent(*args, **kwargs):
        return
        yield  # pragma: no cover

    second._graph.astream = silent  # type: ignore[method-assign]
    stream = second.stream_turn(ACME_ADMIN, "c1", "again")
    event = await asyncio.wait_for(stream.__anext__(), timeout=5)
    assert event.kind == "error"
    await asyncio.wait_for(stream.aclose(), timeout=5)  # the reader leaves at the error event
    assert len(records) == 1 and records[0].envelope is None
    assert records[0].trace_id == "second-turn"
    assert second._lock_users == {}
