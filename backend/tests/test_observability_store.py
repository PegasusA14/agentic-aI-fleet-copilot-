import sqlite3
from datetime import UTC, datetime

import pytest
from graph_support import (
    ACME_ADMIN,
    answer_plan,
    explanation,
    make_service,
)

from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.service import AgentService
from fleet_copilot.observability.store import TraceStore, redact_data

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def store(tmp_path):
    return TraceStore(tmp_path / "traces.db", model_mode="replay", now=lambda: NOW)


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


def test_redaction_keeps_counts_and_short_words_only():
    data = {
        "matches": 3,
        "ok": True,
        "population": None,
        "args": {"metric": "disk", "device_ids": 2},
        "problems": ["a", "b"],
        "Bad Key": "dropped",
        "long": "x" * 200,
        "multiline": "a\nb",
        "nested": {"deep": {"deeper": 1}},
        "obj": object(),
        "ratio": 0.5,
        "nan": float("nan"),
    }
    out = redact_data(data)
    assert out["matches"] == 3 and out["ok"] is True and out["population"] is None
    assert out["args"] == {"metric": "disk", "device_ids": 2}
    assert out["problems"] == ["a", "b"] and out["ratio"] == 0.5
    assert "Bad Key" not in out and "obj" not in out and "nan" not in out
    assert out["long"] == "redacted" and out["multiline"] == "redacted"
    assert out["nested"] == {}  # a dict nested deeper than one level does not survive


async def test_a_recorded_turn_reads_back_for_its_own_tenant_only(tmp_path, real_db_path):
    traces = store(tmp_path)
    service = make_service(real_db_path, low_disk_llm(), recorder=traces.record)
    result = await service.run_turn(ACME_ADMIN, "c1", "Which devices are low on disk space?")
    trace_id = result.envelope.trace_id

    events = traces.get_trace("acme-001", trace_id)
    assert [e.seq for e in events] == list(range(len(events))) and events[0].kind == "scope"
    assert (
        traces.get_trace("globex-002", trace_id) is None
    )  # a foreign tenant gets what an unknown id gets
    assert traces.get_trace("acme-001", "nope") is None
    assert [e.kind for e in traces.get_trace_any(trace_id)] == [e.kind for e in events]


async def test_the_operator_list_has_outcomes_and_no_question(tmp_path, real_db_path):
    traces = store(tmp_path)
    service = make_service(real_db_path, low_disk_llm(), recorder=traces.record)
    await service.run_turn(ACME_ADMIN, "c1", "Which devices are low on disk space?")
    rows = traces.list_turns()
    assert len(rows) == 1
    row = rows[0]
    assert (row["company_id"], row["actor_id"], row["outcome"], row["model_mode"]) == (
        "acme-001",
        "admin-acme",
        "answer",
        "replay",
    )
    assert row["started_at"] == "2026-11-20T12:00:00Z"
    assert "Which devices" not in str(row)


async def test_an_aborted_turn_is_listed_without_an_outcome(tmp_path, real_db_path):
    traces = store(tmp_path)
    service = make_service(real_db_path, low_disk_llm(), recorder=traces.record)
    stream = service.stream_turn(ACME_ADMIN, "c1", "low disk?")
    await anext(stream)
    await stream.aclose()
    assert traces.list_turns()[0]["outcome"] is None


async def test_list_is_newest_first_and_limited(tmp_path, real_db_path):
    traces = store(tmp_path)
    service = make_service(real_db_path, low_disk_llm(3), recorder=traces.record)
    for n in range(3):
        await service.run_turn(ACME_ADMIN, f"c{n}", "low disk?")
    assert len(traces.list_turns(limit=2)) == 2
    assert traces.list_turns()[0]["trace_id"] == "t3"


async def test_no_dataset_identifier_or_question_reaches_the_database(tmp_path, real_db_path):
    traces = store(tmp_path)
    llm = ScriptedLLM(
        plans=[answer_plan(), answer_plan()],
        acts=[
            tool_calls_message(("low_disk_devices", {})),
            final_message(),
            tool_calls_message(
                ("device_history", {"device_id": "8NM23J95R5I6", "metric": "disk_free_pct"})
            ),
            final_message(),
        ],
        explains=[explanation(), explanation()],
    )
    service = make_service(real_db_path, llm, recorder=traces.record)
    questions = [
        "Which devices are low on disk space?",
        "Show disk history for 8NM23J95R5I6 please",
    ]
    for n, question in enumerate(questions):
        await service.run_turn(ACME_ADMIN, f"c{n}", question)

    with sqlite3.connect(real_db_path) as fleet:
        secrets = {
            value
            for row in fleet.execute("SELECT device_id, hostname, employee_id FROM devices")
            for value in row
            if len(value) >= 4
        }
    with sqlite3.connect(tmp_path / "traces.db") as db:
        blob = "\n".join(
            " ".join(str(v) for v in row)
            for table in ("turns", "trace_events")
            for row in db.execute(f"SELECT * FROM {table}")
        )
    leaked = sorted(s for s in secrets if s in blob)
    assert leaked == [], f"identifiers persisted in traces: {leaked[:5]}"
    for question in questions:
        assert question not in blob


async def test_the_store_works_through_the_service_open_helper(tmp_path, real_db_path):
    traces = store(tmp_path)
    async with AgentService.open(
        db_path=real_db_path,
        gateway=low_disk_llm(),
        checkpoint_path=tmp_path / "cp.db",
        recorder=traces.record,
    ) as service:
        await service.run_turn(ACME_ADMIN, "c1", "low disk?")
    assert len(traces.list_turns()) == 1


def test_a_new_store_creates_its_directory(tmp_path):
    TraceStore(tmp_path / "deep" / "er" / "traces.db", model_mode="replay")
    assert (tmp_path / "deep" / "er" / "traces.db").exists()


def test_unknown_limit_values_are_clamped(tmp_path):
    traces = store(tmp_path)
    assert traces.list_turns(limit=0) == [] and traces.list_turns(limit=10**9) == []
    with pytest.raises(TypeError):
        traces.list_turns(limit="5")  # type: ignore[arg-type]
