"""Approval and audit over HTTP: a real scripted chat turn proposes, a human approves with the card's hash."""

from api_support import ORIGIN, client_for, parse_sse, sign_in

from fleet_copilot.agent.llm import (
    Plan,
    Reference,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.contracts import AnswerEnvelope

ACME_FLAG_DEVICE = "8NM23J95R5I6"  # battery "Service Recommended" run ends at the latest reading
GLOBEX_LOW_DISK_DEVICE = "E4V0SB5I0MYU"


def _named_plan(device_ref: str) -> Plan:
    return Plan(
        intent="propose",
        summary="propose an action",
        reference=Reference(kind="named", device_ref=device_ref),
    )


def flag_turn():
    """Plan and model messages for one chat turn that proposes a replacement flag."""
    return (
        _named_plan(ACME_FLAG_DEVICE),
        [
            tool_calls_message(
                ("propose_action", {"action_type": "replacement_flag", "device_id": "x"})
            ),
            final_message(),
        ],
    )


def notify_turn():
    return (
        _named_plan(GLOBEX_LOW_DISK_DEVICE),
        [
            tool_calls_message(
                (
                    "propose_action",
                    {"action_type": "employee_notification", "device_id": "x", "topic": "low_disk"},
                )
            ),
            final_message(),
        ],
    )


def llm_for(*turns):
    return ScriptedLLM(
        plans=[plan for plan, _ in turns], acts=[m for _, msgs in turns for m in msgs]
    )


async def propose(client, user, conversation_id="c1"):
    """A real chat turn as `user`; returns the proposal card from the SSE answer."""
    await sign_in(client, user)
    response = await client.post(
        "/api/chat", json={"conversation_id": conversation_id, "question": "Please do that"}
    )
    assert response.status_code == 200
    answer = next(data for name, data in parse_sse(response.text) if name == "answer")
    envelope = AnswerEnvelope.model_validate(answer)
    assert envelope.outcome == "proposal" and envelope.proposal.status == "pending"
    return answer["proposal"]


def approve_url(card):
    return f"/api/proposals/{card['proposal_id']}/approve"


async def approve(client, card, payload_hash=None):
    return await client.post(
        approve_url(card), json={"payload_hash": payload_hash or card["payload_hash"]}
    )


async def test_the_full_flow_chat_proposal_then_approval_executes_one_simulated_effect(
    real_db_path, tmp_path
):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        listed = (await client.get("/api/proposals", params={"status": "pending"})).json()
        assert [c["proposal_id"] for c in listed] == [card["proposal_id"]]
        assert (await client.get(f"/api/proposals/{card['proposal_id']}")).json() == card

        response = await approve(client, card)
        body = response.json()
        assert response.status_code == 200
        assert body["proposal"]["status"] == "executed" and body["duplicate"] is False
        assert body["effect"]["simulated"] is True
        assert (await client.get("/api/proposals", params={"status": "pending"})).json() == []

        audit = (await client.get("/api/audit", params={"proposal_id": card["proposal_id"]})).json()
        assert [a["event"] for a in audit] == [  # newest first
            "proposal_executed",
            "proposal_approved",
            "proposal_created",
        ]
        by_target = (await client.get("/api/audit", params={"target": ACME_FLAG_DEVICE})).json()
        assert [a["event"] for a in by_target] == [a["event"] for a in audit]

        await sign_in(client, "operator", "op-pass")
        effects = (await client.get("/api/observe/effects")).json()
    assert effects["replacement_flags"] == {"acme-001": 1}
    assert sum(sum(rows.values()) for rows in effects.values()) == 1


async def test_a_second_approval_is_a_duplicate_and_adds_no_effect(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        await approve(client, card)
        again = await approve(client, card)
        await sign_in(client, "operator", "op-pass")
        effects = (await client.get("/api/observe/effects")).json()
        audit = (await client.get("/api/observe/audit")).json()
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["proposal"]["status"] == "executed"
    assert effects["replacement_flags"] == {"acme-001": 1}
    assert [a["event"] for a in audit].count("duplicate_approval") == 1


async def test_a_wrong_hash_is_409_proposal_changed_and_the_proposal_stays_pending(
    real_db_path, tmp_path
):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        response = await approve(client, card, "0" * 64)
        after = (await client.get(f"/api/proposals/{card['proposal_id']}")).json()
        await sign_in(client, "operator", "op-pass")
        effects = (await client.get("/api/observe/effects")).json()
    assert response.status_code == 409 and response.json() == {"detail": "proposal changed"}
    assert after["status"] == "pending"
    assert sum(sum(r.values()) for r in effects.values()) == 0


async def test_reject_then_approve_is_409_with_the_status(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        rejected = await client.post(f"/api/proposals/{card['proposal_id']}/reject")
        approved = await approve(client, card)
        rejected_again = await client.post(f"/api/proposals/{card['proposal_id']}/reject")
        await sign_in(client, "operator", "op-pass")
        effects = (await client.get("/api/observe/effects")).json()
    assert rejected.status_code == 200 and rejected.json()["proposal"]["status"] == "rejected"
    assert approved.status_code == 409 and approved.json() == {"detail": "proposal is rejected"}
    assert rejected_again.status_code == 409
    assert rejected_again.json() == {"detail": "proposal is rejected"}
    assert sum(sum(r.values()) for r in effects.values()) == 0


async def test_another_tenants_admin_gets_the_same_404_as_for_an_unknown_id(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        await sign_in(client, "admin-globex")
        unknown = "/api/proposals/prp_0000000000000000"
        foreign = f"/api/proposals/{card['proposal_id']}"
        results = {
            "get": (await client.get(foreign), await client.get(unknown)),
            "approve": (
                await client.post(
                    f"{foreign}/approve", json={"payload_hash": card["payload_hash"]}
                ),
                await client.post(
                    f"{unknown}/approve", json={"payload_hash": card["payload_hash"]}
                ),
            ),
            "reject": (
                await client.post(f"{foreign}/reject"),
                await client.post(f"{unknown}/reject"),
            ),
        }
        listed = (await client.get("/api/proposals")).json()
        await sign_in(client, "admin-acme")
        still = (await client.get(foreign)).json()
    for name, (a, b) in results.items():
        assert a.status_code == b.status_code == 404, name
        assert a.json() == b.json() == {"detail": "not found"}, name
        assert a.headers["content-type"] == b.headers["content-type"], name
    assert listed == []
    assert still["status"] == "pending"


async def test_an_operator_cannot_approve_reject_or_read_tenant_routes(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        await sign_in(client, "operator", "op-pass")
        assert (await approve(client, card)).status_code == 403
        assert (
            await client.post(f"/api/proposals/{card['proposal_id']}/reject")
        ).status_code == 403
        assert (await client.get("/api/proposals")).status_code == 403
        assert (await client.get(f"/api/proposals/{card['proposal_id']}")).status_code == 403
        assert (await client.get("/api/audit")).status_code == 403
        await sign_in(client, "admin-acme")
        assert (await client.get(f"/api/proposals/{card['proposal_id']}")).json()["status"] == (
            "pending"
        )


async def test_a_tenant_admin_cannot_use_the_observe_routes(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "admin-acme")
        assert (await client.get("/api/observe/audit")).status_code == 403
        assert (await client.get("/api/observe/effects")).status_code == 403


async def test_the_operator_sees_every_tenants_audit_and_can_filter(real_db_path, tmp_path):
    llm = llm_for(flag_turn(), notify_turn())
    async with client_for(real_db_path, tmp_path, llm) as client:
        acme = await propose(client, "admin-acme", "c1")
        globex = await propose(client, "admin-globex", "c2")
        mine = (await client.get("/api/audit")).json()  # signed in as Globex
        await sign_in(client, "operator", "op-pass")
        everything = (await client.get("/api/observe/audit")).json()
        only_acme = (await client.get("/api/observe/audit", params={"company": "acme-001"})).json()
        by_target = (
            await client.get("/api/observe/audit", params={"target": GLOBEX_LOW_DISK_DEVICE})
        ).json()
        by_actor = (
            await client.get("/api/observe/audit", params={"actor": everything[0]["actor_id"]})
        ).json()
    assert {a["company_id"] for a in everything} == {"acme-001", "globex-002"}
    assert {a["proposal_id"] for a in everything} == {acme["proposal_id"], globex["proposal_id"]}
    assert {a["company_id"] for a in only_acme} == {"acme-001"}
    assert {a["company_id"] for a in mine} == {"globex-002"}
    assert {a["proposal_id"] for a in by_target} == {globex["proposal_id"]}
    assert by_actor and {a["actor_id"] for a in by_actor} == {everything[0]["actor_id"]}


async def test_auth_runs_before_body_validation(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        url = "/api/proposals/prp_0000000000000000/approve"
        assert (await client.post(url, json={"nonsense": 1})).status_code == 401
        assert (await client.post(url, json={})).status_code == 401
        assert (await client.post("/api/proposals/prp_0000000000000000/reject")).status_code == 401
        assert (await client.get("/api/proposals")).status_code == 401
        assert (await client.get("/api/audit")).status_code == 401
        assert (await client.get("/api/observe/audit")).status_code == 401
        assert (await client.get("/api/observe/effects")).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await client.post(url, json={"nonsense": 1})).status_code == 403
        await sign_in(client, "admin-acme")
        assert (await client.post(url, json={"nonsense": 1})).status_code == 422
        assert (await client.post(url, json={})).status_code == 422
        assert (await client.get("/api/proposals", params={"status": "bogus"})).status_code == 422


async def test_a_foreign_origin_is_refused_on_decisions(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, llm_for(flag_turn())) as client:
        card = await propose(client, "admin-acme")
        bad = await client.post(
            approve_url(card),
            json={"payload_hash": card["payload_hash"]},
            headers={"Origin": "http://evil.example"},
        )
        rejected = await client.post(
            f"/api/proposals/{card['proposal_id']}/reject",
            headers={"Origin": "http://evil.example"},
        )
        ok = await client.post(
            approve_url(card),
            json={"payload_hash": card["payload_hash"]},
            headers={"Origin": ORIGIN},
        )
    assert bad.status_code == rejected.status_code == 403
    assert bad.json() == {"detail": "origin not allowed"}
    assert ok.status_code == 200 and ok.json()["proposal"]["status"] == "executed"
