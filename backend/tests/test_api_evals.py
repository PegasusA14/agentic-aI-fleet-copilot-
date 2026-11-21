"""The operator's read of stored evaluation runs: `GET /api/observe/evals` and `/api/observe/evals/{run_id}`."""

import pytest
from api_support import client_for, sign_in

from fleet_copilot.agent.llm import ScriptedLLM
from fleet_copilot.evals.store import EvalStore

NOT_FOUND = {"detail": "not found"}
TOTALS = {
    "suites": {"grounding": {"passed": 1, "total": 2, "rate": 0.5, "not_recorded": 0}},
    "overall": {"passed": 1, "total": 2, "rate": 0.5, "not_recorded": 0},
}


def seed(tmp_path, *run_ids):
    """Write runs where the app will look: `<state>/evals.db` (the runner's path)."""
    store = EvalStore(tmp_path / "evals.db")
    for number, run_id in enumerate(run_ids, 1):
        store.start_run(
            run_id=run_id,
            started_at=f"2026-06-12T11:0{number}:00Z",
            model_mode="eval-replay",
            dataset_sha256="ab" * 32,
            as_of="2026-06-12T11:00:00Z",
            policy={"stale_after_days": 7},
        )
        store.add_case(
            run_id,
            instance_id="grounding.low_disk#1",
            suite="grounding",
            case_id="low_disk",
            phrasing_index=1,
            user="admin-acme",
            questions=["Which devices are low on disk space?"],
            passed=False,
            checks=[{"name": "devices", "passed": False, "detail": "missing D1", "turn": 1}],
            trace_ids=["t1"],
            effects=[],
        )
        store.finish_run(run_id, TOTALS)


async def test_the_operator_lists_runs_newest_first_with_their_totals(real_db_path, tmp_path):
    seed(tmp_path, "r1", "r2")
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "operator", "op-pass")
        response = await client.get("/api/observe/evals")
        limited = await client.get("/api/observe/evals", params={"limit": "1"})
    runs = response.json()
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert runs[0]["totals"] == TOTALS and runs[0]["model_mode"] == "eval-replay"
    assert "cases" not in runs[0]
    assert [r["run_id"] for r in limited.json()] == ["r2"]


async def test_no_runs_yet_is_an_empty_list(real_db_path, tmp_path):
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "operator", "op-pass")
        response = await client.get("/api/observe/evals")
    assert response.status_code == 200 and response.json() == []


async def test_one_run_comes_with_its_cases(real_db_path, tmp_path):
    seed(tmp_path, "r1")
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "operator", "op-pass")
        response = await client.get("/api/observe/evals/r1")
    run = response.json()
    assert response.status_code == 200 and run["run_id"] == "r1"
    case = run["cases"][0]
    assert case["instance_id"] == "grounding.low_disk#1" and case["trace_ids"] == ["t1"]
    assert case["checks"][0]["detail"] == "missing D1" and case["passed"] is False


async def test_an_unknown_run_is_a_uniform_404(real_db_path, tmp_path):
    seed(tmp_path, "r1")
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "operator", "op-pass")
        unknown = await client.get("/api/observe/evals/nope")
        odd = await client.get("/api/observe/evals/" + "x" * 500)
    assert unknown.status_code == odd.status_code == 404
    assert unknown.json() == odd.json() == NOT_FOUND


async def test_evals_are_for_the_operator_only(real_db_path, tmp_path):
    seed(tmp_path, "r1")
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        assert (await client.get("/api/observe/evals")).status_code == 401
        assert (await client.get("/api/observe/evals/r1")).status_code == 401
        await sign_in(client, "admin-acme")
        assert (await client.get("/api/observe/evals")).status_code == 403
        assert (await client.get("/api/observe/evals/r1")).status_code == 403
        # an admin cannot tell a real run from a missing one
        assert (await client.get("/api/observe/evals/nope")).status_code == 403


@pytest.mark.parametrize("limit", ["0", "-3", "abc", "100000"])
async def test_the_limit_is_validated_for_the_operator_only(real_db_path, tmp_path, limit):
    seed(tmp_path, "r1")
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "admin-acme")
        assert (await client.get("/api/observe/evals", params={"limit": limit})).status_code == 403
        await sign_in(client, "operator", "op-pass")
        response = await client.get("/api/observe/evals", params={"limit": limit})
    assert response.status_code in (200, 422)
