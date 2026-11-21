import pytest
from api_support import client_for, sign_in

from fleet_copilot.agent.llm import ScriptedLLM
from fleet_copilot.config import Settings

INDEX = "<!doctype html><title>Fleet Copilot</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "x.js").write_text("console.log('x')")
    (tmp_path / "secret.txt").write_text("outside the dist directory")
    return root


async def test_root_serves_index(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")


async def test_client_routes_fall_back_to_index(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        approvals = await client.get("/approvals")
        nested = await client.get("/observe/traces")
    assert approvals.status_code == 200 and approvals.text == INDEX
    assert nested.status_code == 200 and nested.text == INDEX


async def test_assets_are_served(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        response = await client.get("/assets/x.js")
        missing = await client.get("/assets/nope.js")
    assert response.status_code == 200 and "console.log" in response.text
    assert missing.status_code == 404


async def test_unknown_api_paths_stay_json_404(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        response = await client.get("/api/nope")
        bare = await client.get("/api")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}
    assert bare.status_code == 404 and bare.headers["content-type"].startswith("application/json")


async def test_api_routes_are_unaffected(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        health = await client.get("/api/health")
        signed_out = await client.get("/api/session")
        signed_in = await sign_in(client)
    assert health.json() == {"status": "ok"}
    assert signed_out.status_code == 401
    assert signed_in.status_code == 200


async def test_nothing_outside_the_dist_directory_is_served(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        escaped = await client.get("/assets/../../secret.txt")
        encoded = await client.get("/assets/..%2f..%2fsecret.txt")
        fallback = await client.get("/..%2fsecret.txt")
    for response in (escaped, encoded):
        assert "outside the dist directory" not in response.text
    assert "outside the dist directory" not in fallback.text


async def test_post_to_a_client_route_is_not_served_the_page(real_db_path, tmp_path, dist):
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=dist) as client:
        response = await client.post("/approvals")
    assert response.status_code in (404, 405)


@pytest.mark.parametrize("make_dist", [False, True])
async def test_without_a_built_app_the_api_still_works(real_db_path, tmp_path, make_dist):
    empty = tmp_path / "empty"
    if make_dist:
        empty.mkdir()  # exists, but has no index.html
    async with client_for(real_db_path, tmp_path, ScriptedLLM(), static_dir=empty) as client:
        root = await client.get("/")
        health = await client.get("/api/health")
    assert root.status_code == 404
    assert health.status_code == 200


def test_settings_read_the_static_dir_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_STATIC_DIR", str(tmp_path / "built"))
    assert Settings.from_env().static_dir == tmp_path / "built"
    monkeypatch.delenv("FLEET_STATIC_DIR")
    default = Settings.from_env().static_dir
    assert default is not None and default.parts[-2:] == ("frontend", "dist")
