import json

from api_support import client_for, parse_sse, sign_in
from graph_support import answer_plan, explanation, follow_up_plan, golden, listed_ids

from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.contracts import AnswerEnvelope


def low_disk_llm():
    return ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )


async def ask(client, question="Which devices are low on disk space?", conversation_id="c1"):
    return await client.post(
        "/api/chat", json={"conversation_id": conversation_id, "question": question}
    )


async def test_chat_streams_trace_events_then_one_answer_then_done(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client, "admin-acme")
        response = await ask(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    frames = parse_sse(response.text)
    names = [name for name, _ in frames]
    assert names[-1] == "done" and names[-2] == "answer" and set(names[:-2]) == {"trace"}
    assert [data["seq"] for name, data in frames if name == "trace"] == list(
        range(names.count("trace"))
    )
    envelope = AnswerEnvelope.model_validate(frames[-2][1])
    assert envelope.outcome == "answer" and envelope.scope.company_id == "acme-001"
    assert sorted(listed_ids(envelope)) == sorted(golden("acme-001")["low_disk_lt10"]["devices"])


async def test_the_chat_needs_a_session_and_an_admin(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await ask(client)).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await ask(client)).status_code == 403
        assert (await client.post("/api/conversations")).status_code == 403


async def test_bad_questions_are_422_with_fixed_text_and_never_reach_the_model(
    real_db_path, tmp_path
):
    llm = ScriptedLLM()
    async with client_for(real_db_path, tmp_path, llm) as client:
        await sign_in(client)
        empty = await ask(client, "   ")
        long = await ask(client, "x" * 2500)
    assert empty.status_code == long.status_code == 422
    assert empty.json() == {"detail": "Ask a question about your devices."}
    assert long.json() == {"detail": "Questions are limited to 2000 characters."}
    assert llm.calls == []


async def test_a_bad_conversation_id_is_422(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client)
        response = await ask(client, conversation_id="../../etc/passwd")
    assert response.status_code == 422 and response.json() == {"detail": "invalid conversation id"}


async def test_unknown_body_fields_are_refused(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client)
        response = await client.post(
            "/api/chat",
            json={"conversation_id": "c1", "question": "hi", "company_id": "globex-002"},
        )
    assert response.status_code == 422


async def test_a_missing_dataset_is_a_json_503_before_any_stream(real_db_path, tmp_path):
    async with client_for(tmp_path / "missing.db", tmp_path, ScriptedLLM()) as client:
        await sign_in(client)
        response = await ask(client)
    assert response.status_code == 503 and "make load-data" in response.json()["detail"]
    assert not response.headers["content-type"].startswith("text/event-stream")


async def test_new_conversation_ids_are_usable_and_unique(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client)
        one = (await client.post("/api/conversations")).json()["conversation_id"]
        two = (await client.post("/api/conversations")).json()["conversation_id"]
        response = await ask(client, conversation_id=one)
    assert one != two and response.status_code == 200


async def test_a_conversation_cannot_be_continued_by_another_tenant(real_db_path, tmp_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("ordinal", 1)],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    async with client_for(real_db_path, tmp_path, llm) as client:
        await sign_in(client, "admin-acme")
        await ask(client, conversation_id="shared")
        await sign_in(client, "admin-globex")  # the same conversation id, a different tenant
        response = await ask(client, "the first one", conversation_id="shared")
    answer = next(data for name, data in parse_sse(response.text) if name == "answer")
    assert answer["outcome"] == "clarification"  # globex has no list to point at
    assert "acme" not in json.dumps(answer)


async def test_deleting_a_conversation_is_204_and_idempotent(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, low_disk_llm()) as client:
        await sign_in(client)
        await ask(client)
        first = await client.delete("/api/conversations/c1")
        second = await client.delete("/api/conversations/c1")
        bad = await client.delete("/api/conversations/..%2Fx")
    assert first.status_code == second.status_code == 204
    assert bad.status_code == 404
