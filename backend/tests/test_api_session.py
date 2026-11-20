from api_support import client_for, sign_in

from fleet_copilot.agent.llm import ScriptedLLM


async def test_health_needs_no_session(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_demo_users_are_listed_without_the_operator(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        users = (await client.get("/api/demo-users")).json()
    assert {u["user"] for u in users} == {"admin-acme", "admin-globex", "admin-initech"}
    assert all(set(u) == {"user", "display_name", "company_id"} for u in users)


async def test_sign_in_sets_a_signed_http_only_strict_cookie(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        response = await sign_in(client, "admin-acme")
        cookie = response.headers["set-cookie"].lower()
        me = await client.get("/api/session")
    assert response.status_code == 200
    assert response.json() == {
        "company_id": "acme-001",
        "display_name": "Acme admin (demo)",
        "role": "admin",
    }
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert me.json()["company_id"] == "acme-001"


async def test_no_session_is_401_and_sign_out_clears_it(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await client.get("/api/session")).status_code == 401
        await sign_in(client)
        assert (await client.get("/api/session")).status_code == 200
        assert (await client.delete("/api/session")).status_code == 204
        assert (await client.get("/api/session")).status_code == 401


async def test_unknown_user_and_wrong_passcode_fail_identically(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        unknown = await sign_in(client, "someone-else")
        wrong = await sign_in(client, "operator", "nope")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": "sign-in failed"}


async def test_the_operator_signs_in_with_the_passcode(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        response = await sign_in(client, "operator", "op-pass")
    assert response.status_code == 200 and response.json()["role"] == "operator"


async def test_operator_sign_in_is_disabled_without_a_passcode(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), operator_passcode=None) as client:
        response = await sign_in(client, "operator", "anything")
    assert response.status_code == 403


async def test_demo_admins_cannot_sign_in_as_the_operator_by_passing_a_passcode(
    real_db_path, tmp_path
):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "admin-acme", "op-pass")
        me = (await client.get("/api/session")).json()
    assert me["role"] == "admin" and me["company_id"] == "acme-001"


async def test_a_forged_cookie_is_not_a_session(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        client.cookies.set("fleet_session", "eyJ1c2VyIjogIm9wZXJhdG9yIn0=.abc.def")
        assert (await client.get("/api/session")).status_code == 401


async def test_a_foreign_origin_cannot_change_state(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        bad = await client.post(
            "/api/session", json={"user": "admin-acme"}, headers={"origin": "http://evil.test"}
        )
        good = await client.post(
            "/api/session", json={"user": "admin-acme"}, headers={"origin": "http://127.0.0.1:8000"}
        )
    assert bad.status_code == 403 and good.status_code == 200


async def test_there_are_no_cors_headers_and_no_docs(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        response = await client.get("/api/health", headers={"origin": "http://evil.test"})
        docs = await client.get("/docs")
    assert not any(h.lower().startswith("access-control-") for h in response.headers)
    assert docs.status_code == 404


async def test_the_server_entry_binds_to_localhost_only(monkeypatch):
    from fleet_copilot.api import __main__ as entry

    seen = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(entry, "create_app_from_env", lambda: object())
    monkeypatch.setattr(entry, "load_env", lambda: None)
    entry.main()
    assert seen["host"] == "127.0.0.1" and seen["access_log"] is False
