import json
import logging
import sqlite3

from api_support import client_for, parse_sse, sign_in
from graph_support import answer_plan, explanation, follow_up_plan

from fleet_copilot.agent.identity import Identity
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.api import security
from fleet_copilot.api.app import fan_out
from fleet_copilot.contracts import AnswerEnvelope
from fleet_copilot.observability.store import TraceStore

NOT_FOUND = {"detail": "not found"}


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


async def ask(client, question="Which devices are low on disk space?", conversation_id="c1"):
    return await client.post(
        "/api/chat", json={"conversation_id": conversation_id, "question": question}
    )


def answer_of(response) -> dict:
    return next(data for name, data in parse_sse(response.text) if name == "answer")


async def test_the_list_is_newest_first_with_titles_and_turn_counts(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm(3)) as client:
        await sign_in(client, "admin-acme")
        await ask(client, "first topic", conversation_id="a")
        await ask(client, "second topic", conversation_id="b")
        await ask(client, "first topic again", conversation_id="a")
        response = await client.get("/api/conversations")
        limited = await client.get("/api/conversations", params={"limit": 1})
    assert response.status_code == 200
    rows = response.json()
    assert [(r["conversation_id"], r["title"], r["turns"]) for r in rows] == [
        ("a", "first topic", 2),
        ("b", "second topic", 1),
    ]
    assert set(rows[0]) == {"conversation_id", "title", "updated_at", "turns"}
    assert [r["conversation_id"] for r in limited.json()] == ["a"]


async def test_a_bad_limit_is_422(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client)
        assert (await client.get("/api/conversations?limit=abc")).status_code == 422
        assert (await client.get("/api/conversations?limit=0")).status_code == 422
        assert (await client.get("/api/conversations?limit=100000")).status_code == 422


async def test_get_returns_the_turns_with_their_envelopes_as_objects(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client)
        streamed = answer_of(await ask(client))
        response = await client.get("/api/conversations/c1")
    assert response.status_code == 200
    body = response.json()
    assert (
        body["conversation_id"] == "c1" and body["title"] == "Which devices are low on disk space?"
    )
    (turn,) = body["turns"]
    assert set(turn) == {"seq", "question", "envelope", "trace_id", "created_at"}
    assert turn["seq"] == 0 and turn["question"] == "Which devices are low on disk space?"
    assert isinstance(turn["envelope"], dict) and turn["envelope"] == streamed
    assert turn["trace_id"] == streamed["trace_id"]
    assert AnswerEnvelope.model_validate(turn["envelope"]).outcome == "answer"


async def test_history_needs_a_session_and_an_admin(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await client.get("/api/conversations")).status_code == 401
        assert (await client.get("/api/conversations/c1")).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await client.get("/api/conversations")).status_code == 403
        assert (await client.get("/api/conversations/c1")).status_code == 403


async def test_another_tenant_another_admin_and_an_unknown_id_get_the_same_404(
    real_db_path, tmp_path, monkeypatch
):
    second = Identity("acme-001", "admin-acme-2", "Second Acme admin (demo)", company_name="Acme")
    monkeypatch.setitem(security.ALL_USERS, "admin-acme-2", second)
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client, "admin-acme")
        await ask(client)
        own = await client.get("/api/conversations/c1")
        unknown = await client.get("/api/conversations/nope")
        malformed = await client.get("/api/conversations/not.valid")
        await sign_in(client, "admin-globex")
        other_tenant = await client.get("/api/conversations/c1")
        other_tenant_list = await client.get("/api/conversations")
        await sign_in(client, "admin-acme-2")
        other_admin = await client.get("/api/conversations/c1")
        other_admin_list = await client.get("/api/conversations")
    assert own.status_code == 200
    for response in (unknown, malformed, other_tenant, other_admin):
        assert response.status_code == 404 and response.json() == NOT_FOUND
        assert response.text == unknown.text and response.headers == unknown.headers
    assert other_tenant_list.json() == [] and other_admin_list.json() == []


async def test_delete_clears_the_stored_conversation(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm(2)) as client:
        await sign_in(client)
        await ask(client, conversation_id="keep")
        await ask(client, conversation_id="gone")
        assert (await client.delete("/api/conversations/gone")).status_code == 204
        after = await client.get("/api/conversations/gone")
        listing = await client.get("/api/conversations")
        kept = await client.get("/api/conversations/keep")
        again = await client.delete("/api/conversations/gone")
    assert after.status_code == 404
    assert [r["conversation_id"] for r in listing.json()] == ["keep"]
    assert kept.status_code == 200 and again.status_code == 204


async def test_another_tenants_delete_does_not_clear_the_stored_conversation(
    real_db_path, tmp_path
):
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client, "admin-acme")
        await ask(client)
        await sign_in(client, "admin-globex")
        assert (await client.delete("/api/conversations/c1")).status_code == 204
        await sign_in(client, "admin-acme")
        assert (await client.get("/api/conversations/c1")).status_code == 200


async def test_a_turn_the_reader_left_is_not_stored(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client)
        service = client.fleet_app.state.service
        stream = service.stream_turn(
            security.ALL_USERS["admin-acme"], "c1", "Which devices are low on disk space?"
        )
        await anext(stream)  # the reader takes one event and goes away
        await stream.aclose()
        listing = await client.get("/api/conversations")
        reopened = await client.get("/api/conversations/c1")
    assert listing.json() == [] and reopened.status_code == 404


async def test_reopening_continues_the_same_conversation_and_its_checkpoint(real_db_path, tmp_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("ordinal", 1)],
        acts=[
            tool_calls_message(("low_disk_devices", {})),
            final_message(),
            tool_calls_message(("trend", {"metric": "disk", "device_ids": ["x"]})),
            final_message(),
        ],
        explains=[explanation(), explanation(headline="Disk trend for that device")],
    )
    async with client_for(real_db_path, tmp_path, llm) as client:
        await sign_in(client)
        first = answer_of(await ask(client))
        await client.get("/api/conversations")  # the sidebar
        reopened = (await client.get("/api/conversations/c1")).json()
        assert reopened["turns"][0]["envelope"] == first
        # the user types in the reopened conversation: "the first one" resolves against its checkpoint
        second = answer_of(await ask(client, "show the disk trend for the first one"))
        final = (await client.get("/api/conversations/c1")).json()
    assert second["outcome"] == "answer"  # a clarification would mean the checkpoint was lost
    assert [t["seq"] for t in final["turns"]] == [0, 1]
    assert final["title"] == reopened["title"]  # the title stays the first question
    assert final["turns"][1]["envelope"] == second


async def test_a_failing_trace_store_never_blocks_history_or_the_turn(
    real_db_path, tmp_path, monkeypatch
):
    def broken(self, turn):
        raise OSError("disk full")

    monkeypatch.setattr(TraceStore, "record", broken)
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client)
        response = await ask(client)
        stored = await client.get("/api/conversations/c1")
    assert response.status_code == 200 and answer_of(response)["outcome"] == "answer"
    assert stored.status_code == 200 and len(stored.json()["turns"]) == 1


async def test_a_failing_conversation_store_never_blocks_traces_or_the_turn(
    real_db_path, tmp_path, monkeypatch
):
    from fleet_copilot.observability.conversations import ConversationStore

    def broken(self, turn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ConversationStore, "record", broken)
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client)
        response = await ask(client)
        traces = client.fleet_app.state.store.list_turns()
    assert response.status_code == 200 and answer_of(response)["outcome"] == "answer"
    assert len(traces) == 1 and traces[0]["outcome"] == "answer"


def test_fan_out_calls_every_sink_even_when_one_raises(caplog):
    seen = []

    def bad(turn):
        raise RuntimeError("boom with a secret question")

    with caplog.at_level(logging.WARNING):
        fan_out(bad, seen.append, bad, seen.append)("turn")
    assert seen == ["turn", "turn"]
    assert "secret question" not in caplog.text  # only the exception type is logged
    assert json.dumps(caplog.messages).count("RuntimeError") >= 1


def test_fan_out_with_no_failures_calls_each_sink_once_in_order():
    order = []
    fan_out(lambda t: order.append(("a", t)), lambda t: order.append(("b", t)))(1)
    assert order == [("a", 1), ("b", 1)]
