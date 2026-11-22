"""The notes a conversation remembers over HTTP: scoped exactly like the conversation routes."""

from api_support import client_for, sign_in
from graph_support import answer_plan, explanation
from test_api_conversations import NOT_FOUND, answer_of, ask

from fleet_copilot.agent.identity import Identity
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.api import security

NOTE = "I only care about laptops"
OTHER_NOTE = "acme-macbook-4 belongs to the CFO"


def noting_llm(*notes):
    """One low-disk turn per note (None for a turn that states none)."""
    return ScriptedLLM(
        plans=[answer_plan(remember=note) for note in notes],
        acts=[
            m
            for _ in notes
            for m in (tool_calls_message(("low_disk_devices", {})), final_message())
        ],
        explains=[explanation() for _ in notes],
    )


async def test_the_notes_are_listed_oldest_first_with_an_index_and_the_turn_they_were_said_in(
    real_db_path, tmp_path
):
    async with client_for(real_db_path, tmp_path, noting_llm(NOTE, OTHER_NOTE)) as client:
        await sign_in(client)
        first = answer_of(await ask(client))
        second = answer_of(await ask(client))
        response = await client.get("/api/conversations/c1/notes")
    assert response.status_code == 200
    assert response.json() == [
        {"index": 0, "text": NOTE, "turn": 1},
        {"index": 1, "text": OTHER_NOTE, "turn": 2},
    ]
    assert (first["notes_count"], second["notes_count"]) == (1, 2)


async def test_a_note_can_be_removed_by_its_index(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, noting_llm(NOTE, OTHER_NOTE)) as client:
        await sign_in(client)
        await ask(client)
        await ask(client)
        deleted = await client.delete("/api/conversations/c1/notes/0")
        listed = await client.get("/api/conversations/c1/notes")
        missing = await client.delete("/api/conversations/c1/notes/5")
        negative = await client.delete("/api/conversations/c1/notes/-1")
    assert deleted.status_code == 204 and deleted.content == b""
    assert listed.json() == [{"index": 0, "text": OTHER_NOTE, "turn": 2}]
    assert missing.status_code == 404 and missing.json() == NOT_FOUND
    assert negative.status_code == 404


async def test_a_removed_note_stays_removed_for_the_next_turn(real_db_path, tmp_path):
    llm = noting_llm(NOTE, None)
    async with client_for(real_db_path, tmp_path, llm) as client:
        await sign_in(client)
        await ask(client)
        await client.delete("/api/conversations/c1/notes/0")
        later = answer_of(await ask(client))
    assert later["notes_count"] == 0
    second_plan = llm.calls_of("plan")[1].messages[1].content
    assert NOTE not in second_plan and "You said" not in second_plan


async def test_notes_need_a_session_and_an_admin(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await client.get("/api/conversations/c1/notes")).status_code == 401
        assert (await client.delete("/api/conversations/c1/notes/0")).status_code == 401
        await sign_in(client, "operator", "op-pass")
        assert (await client.get("/api/conversations/c1/notes")).status_code == 403
        assert (await client.delete("/api/conversations/c1/notes/0")).status_code == 403


async def test_another_tenant_another_admin_and_an_unknown_id_get_the_same_404_for_notes(
    real_db_path, tmp_path, monkeypatch
):
    second = Identity("acme-001", "admin-acme-2", "Second Acme admin (demo)", company_name="Acme")
    monkeypatch.setitem(security.ALL_USERS, "admin-acme-2", second)
    async with client_for(real_db_path, tmp_path, noting_llm(NOTE)) as client:
        await sign_in(client, "admin-acme")
        await ask(client)
        own = await client.get("/api/conversations/c1/notes")
        unknown = await client.get("/api/conversations/nope/notes")
        malformed = await client.get("/api/conversations/not.valid/notes")
        unknown_delete = await client.delete("/api/conversations/nope/notes/0")
        others = []
        for user in ("admin-globex", "admin-acme-2"):
            await sign_in(client, user)
            others.append(await client.get("/api/conversations/c1/notes"))
            others.append(await client.delete("/api/conversations/c1/notes/0"))
        await sign_in(client, "admin-acme")
        still_there = await client.get("/api/conversations/c1/notes")
    assert own.status_code == 200 and own.json()[0]["text"] == NOTE
    for response in (unknown, malformed, unknown_delete, *others):
        assert response.status_code == 404 and response.json() == NOT_FOUND
        assert response.text == unknown.text and response.headers == unknown.headers
    assert still_there.json() == own.json()  # nothing was removed by the others' attempts


async def test_the_same_conversation_id_holds_separate_notes_for_each_tenant(
    real_db_path, tmp_path
):
    async with client_for(real_db_path, tmp_path, noting_llm(NOTE, OTHER_NOTE)) as client:
        await sign_in(client, "admin-acme")
        await ask(client)
        await sign_in(client, "admin-globex")
        await ask(client)
        globex = (await client.get("/api/conversations/c1/notes")).json()
        await sign_in(client, "admin-acme")
        acme = (await client.get("/api/conversations/c1/notes")).json()
    assert [n["text"] for n in acme] == [NOTE]
    assert [n["text"] for n in globex] == [OTHER_NOTE]


async def test_deleting_the_conversation_forgets_its_notes(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, noting_llm(NOTE)) as client:
        await sign_in(client)
        await ask(client)
        await client.delete("/api/conversations/c1")
        after = await client.get("/api/conversations/c1/notes")
    assert after.status_code == 404
