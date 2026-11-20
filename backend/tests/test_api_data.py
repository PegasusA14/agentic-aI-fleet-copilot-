from urllib.parse import quote

import pytest
from api_support import client_for, parse_sse, sign_in
from graph_support import answer_plan, explanation

from fleet_copilot import policy
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.config import EXPECTED_DATASET_SHA256

NOT_FOUND = {"detail": "not found"}


def llm(turns=1):
    return ScriptedLLM(
        plans=[answer_plan() for _ in range(turns)],
        acts=[
            m
            for _ in range(turns)
            for m in (tool_calls_message(("low_disk_devices", {})), final_message())
        ],
        explains=[explanation() for _ in range(turns)],
    )


async def ask(client, conversation_id="c1"):
    response = await client.post(
        "/api/chat",
        json={
            "conversation_id": conversation_id,
            "question": "Which devices are low on disk space?",
        },
    )
    frames = parse_sse(response.text)
    return next(d for n, d in frames if n == "answer")


async def evidence(client, ref):
    return await client.get("/api/evidence", params={"ref": ref})


async def test_evidence_from_an_answer_resolves_for_its_own_tenant(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        answer = await ask(client)
        ref = answer["evidence"][0]["ref"]
        response = await evidence(client, ref)
    assert response.status_code == 200
    body = response.json()
    assert body["ref"] == ref and set(body) == {"ref", "collected_at", "value"}


async def test_evidence_refs_from_every_answer_resolve(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        answer = await ask(client)
        statuses = {(await evidence(client, e["ref"])).status_code for e in answer["evidence"]}
    assert statuses == {200}


async def test_foreign_unknown_and_malformed_refs_are_indistinguishable(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        answer = await ask(client)
        ours = answer["evidence"][0]["ref"]
        foreign = ours.replace("acme-001", "globex-002", 1)
        unknown_device = ours.replace(ours.split("/")[1].split("@")[0], "NOSUCHDEVICE")
        unknown_time = ours.replace("@2026", "@2020", 1)
        results = [
            await evidence(client, ref)
            for ref in (
                foreign,
                unknown_device,
                unknown_time,
                "garbage",
                "",
                "../../etc/passwd",
                ours + "x[",
            )
        ]
    assert {r.status_code for r in results} == {404}
    assert all(r.json() == NOT_FOUND for r in results)


async def test_evidence_needs_a_tenant_admin(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        assert (await evidence(client, "x")).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await evidence(client, "x")).status_code == 403


async def test_a_trace_reads_back_for_its_tenant_only(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        trace_id = (await ask(client))["trace_id"]
        mine = await client.get(f"/api/traces/{trace_id}")
        await sign_in(client, "admin-globex")
        theirs = await client.get(f"/api/traces/{trace_id}")
        unknown = await client.get("/api/traces/doesnotexist")
    assert mine.status_code == 200
    events = mine.json()["events"]
    assert events[0]["kind"] == "scope" and events[-1]["kind"] == "respond"
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json() == NOT_FOUND


async def test_policy_reports_the_pinned_as_of_and_defaults(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await client.get("/api/policy")).status_code == 401
        await sign_in(client, "admin-initech")
        body = (await client.get("/api/policy")).json()
    assert body == {
        "as_of": "2026-06-12T11:00:00Z",
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "defaults": policy.disclosure(),
    }


async def test_observe_is_for_the_operator_only(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm(2)) as client:
        await sign_in(client, "admin-acme")
        trace_id = (await ask(client))["trace_id"]
        assert (await client.get("/api/observe/turns")).status_code == 403
        assert (await client.get(f"/api/observe/traces/{trace_id}")).status_code == 403
        await sign_in(client, "admin-globex")
        await ask(client, "c2")

        await sign_in(client, "operator", "op-pass")
        turns = (await client.get("/api/observe/turns")).json()
        trace = await client.get(f"/api/observe/traces/{trace_id}")
        missing = await client.get("/api/observe/traces/nope")
    assert {t["company_id"] for t in turns} == {"acme-001", "globex-002"}
    assert all("question" not in t for t in turns)
    assert trace.status_code == 200 and missing.status_code == 404


@pytest.mark.parametrize("limit", ["0", "-3", "abc", "100000"])
async def test_observe_limit_is_validated_or_clamped(real_db_path, tmp_path, limit):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "operator", "op-pass")
        response = await client.get("/api/observe/turns", params={"limit": limit})
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json() == []


async def test_evidence_query_encoding_round_trips(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        ref = (await ask(client))["evidence"][0]["ref"]
        response = await client.get(f"/api/evidence?ref={quote(ref, safe='')}")
    assert response.status_code == 200
