import json
from urllib.parse import quote

import pytest
from api_support import client_for, parse_sse, sign_in
from graph_support import answer_plan, explanation

from fleet_copilot import policy
from fleet_copilot.actions.recommend import recommend
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.config import EXPECTED_DATASET_SHA256, REPO_ROOT
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

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


async def test_a_ref_with_a_trailing_newline_is_the_uniform_404(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        ours = (await ask(client))["evidence"][0]["ref"]
        assert (await evidence(client, ours)).status_code == 200
        response = await evidence(client, ours + "\n")
    assert response.status_code == 404 and response.json() == NOT_FOUND


async def test_a_missing_or_overlong_ref_is_the_uniform_404_after_auth(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        await sign_in(client, "admin-acme")
        missing = await client.get("/api/evidence")
        overlong = await evidence(client, "a" * 301)
    assert missing.status_code == overlong.status_code == 404
    assert missing.json() == overlong.json() == NOT_FOUND


async def test_evidence_auth_comes_before_input_validation(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm()) as client:
        assert (await client.get("/api/evidence")).status_code == 401
        assert (await evidence(client, "a" * 301)).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await client.get("/api/evidence")).status_code == 403


async def test_observe_auth_comes_before_input_validation(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        bad = {"limit": "abc"}
        assert (await client.get("/api/observe/turns", params=bad)).status_code == 401
        await sign_in(client, "admin-acme")
        assert (await client.get("/api/observe/turns", params=bad)).status_code == 403
        assert (await client.get("/api/observe/turns", params={"limit": "-1"})).status_code == 403


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


GOLDEN = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
SUMMARY_USERS = {
    "acme-001": "admin-acme",
    "globex-002": "admin-globex",
    "initech-003": "admin-initech",
}


@pytest.mark.parametrize("tenant", sorted(SUMMARY_USERS))
async def test_fleet_summary_counts_the_tenants_devices(real_db_path, tmp_path, tenant):
    want = GOLDEN["tenants"][tenant]["devices"]
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, SUMMARY_USERS[tenant])
        response = await client.get("/api/fleet/summary")
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"as_of", "devices", "flagged"}
    assert body["as_of"] == "2026-06-12T11:00:00Z"
    assert body["devices"] == len(want)
    assert body["flagged"] == sorted(body["flagged"])
    assert set(body["flagged"]) <= set(want)


async def test_fleet_summary_flags_what_recommend_suggests(real_db_path, real_conn, tmp_path):
    repo = FleetRepository(real_conn, TenantContext("acme-001", "test"))
    as_of = dataset_as_of(real_conn)
    want = sorted(d.device_id for d in repo.list_devices() if recommend(repo, as_of, [d.device_id]))
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "admin-acme")
        body = (await client.get("/api/fleet/summary")).json()
    assert want and body["flagged"] == want
    assert set(body["flagged"]) <= set(GOLDEN["tenants"]["acme-001"]["devices"])


async def test_fleet_summary_never_shows_another_tenants_ids(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "admin-acme")
        acme = (await client.get("/api/fleet/summary")).json()
        await sign_in(client, "admin-globex")
        globex = (await client.get("/api/fleet/summary")).json()
    assert acme["flagged"]
    assert not set(acme["flagged"]) & set(globex["flagged"])
    assert set(globex["flagged"]) <= set(GOLDEN["tenants"]["globex-002"]["devices"])


async def test_fleet_summary_needs_a_tenant_admin(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await client.get("/api/fleet/summary")).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await client.get("/api/fleet/summary")).status_code == 403
