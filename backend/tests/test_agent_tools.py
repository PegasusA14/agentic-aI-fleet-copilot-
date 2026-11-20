import json
from typing import Annotated, TypedDict

import pytest
from agent_support import ACME, GLOBEX, context_for, run
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.agent.tools import (
    DEVICE_FILTER_TOOLS,
    TOOLS,
    ToolInputError,
    execute,
    tool_error_text,
)

ACME_LOW_DISK = ["MT7PJB7N5LRE", "8NM23J95R5I6"]  # ordered by free space, lowest first


def test_low_disk_devices_returns_the_golden_devices_in_display_order(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "low_disk_devices")
    assert record.device_ids == ACME_LOW_DISK
    assert record.population == 10 and record.error is None
    assert all(f.refs[0].startswith("acme-001/") for f in record.findings)


def test_threshold_and_device_filter_are_honoured(real_db_path, real_conn):
    wide = run(real_db_path, real_conn, "low_disk_devices", {"threshold_pct": 30.0})
    assert len(wide.findings) > 2
    only = run(
        real_db_path,
        real_conn,
        "low_disk_devices",
        {"device_ids": ["8NM23J95R5I6", "1LYSSFD074BB"]},
    )
    assert only.device_ids == ["8NM23J95R5I6"] and only.population == 2


def test_foreign_device_ids_are_ignored_but_all_foreign_is_not_found(real_db_path, real_conn):
    mixed = run(
        real_db_path,
        real_conn,
        "low_disk_devices",
        {"device_ids": ["MT7PJB7N5LRE", "JRZSGXVMKE6M"]},
    )
    assert mixed.device_ids == ["MT7PJB7N5LRE"] and mixed.population == 1
    foreign = run(real_db_path, real_conn, "low_disk_devices", {"device_ids": ["JRZSGXVMKE6M"]})
    unknown = run(real_db_path, real_conn, "low_disk_devices", {"device_ids": ["NOPE"]})
    assert foreign.error == unknown.error == "not_found"
    assert foreign.llm_view() == unknown.llm_view()


def test_device_history_hides_whether_a_foreign_device_exists(real_db_path, real_conn):
    args = {"metric": "disk_free_pct", "window_days": 7}
    foreign = run(real_db_path, real_conn, "device_history", {"device_id": "JRZSGXVMKE6M", **args})
    unknown = run(real_db_path, real_conn, "device_history", {"device_id": "NOPE", **args})
    assert foreign.error == unknown.error == "not_found"
    assert foreign.model_dump(exclude={"args"}) == unknown.model_dump(exclude={"args"})


def test_every_tool_runs_for_every_tenant(real_db_path, real_conn):
    calls = {
        "low_disk_devices": {},
        "macos_version_check": {"older_than": "15"},
        "compliance_failures_now": {"severity": "medium"},
        "memory_pressure_now": {},
        "trend": {"metric": "memory", "window_days": 30},
        "find_devices": {"os_name": "macOS"},
    }
    for tenant in (ACME, GLOBEX, "initech-003"):
        for name, args in calls.items():
            record = run(real_db_path, real_conn, name, args, tenant)
            assert record.error is None and record.population > 0, (tenant, name)
            assert all(f.refs and f.refs[0].startswith(f"{tenant}/") for f in record.findings), (
                tenant,
                name,
            )


def test_trend_dispatches_every_metric(real_db_path, real_conn):
    mem = run(real_db_path, real_conn, "trend", {"metric": "memory", "window_days": 30})
    disk = run(real_db_path, real_conn, "trend", {"metric": "disk", "window_days": 30})
    comp = run(
        real_db_path,
        real_conn,
        "trend",
        {"metric": "compliance", "check_id": "screen_lock", "window_days": 30},
    )
    batt = run(real_db_path, real_conn, "trend", {"metric": "battery"})
    assert mem.device_ids == ["M4XVHUV1MEPZ"]
    assert {f.values["trend"] for f in disk.findings} == {"onset", "sustained"}
    assert {f.values["kind"] for f in comp.findings} == {"transition"}
    assert batt.device_ids == ["7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"]


def test_compliance_trend_needs_a_check_id(real_db_path, real_conn):
    with pytest.raises(ToolInputError, match="check_id"):
        run(real_db_path, real_conn, "trend", {"metric": "compliance"})


def test_find_devices_filter_and_history_tools(real_db_path, real_conn):
    found = run(real_db_path, real_conn, "find_devices", {"form_factor": "desktop"})
    assert found.device_ids == ["DLT35MHGTH2D"]
    history = run(
        real_db_path,
        real_conn,
        "device_history",
        {"device_id": "MT7PJB7N5LRE", "metric": "memory_used_pct"},
    )
    assert history.findings[0].values["snapshots"] >= 7


def test_the_database_is_opened_read_only(real_db_path, real_conn, monkeypatch):
    from fleet_copilot.agent import tools
    from fleet_copilot.data.loader import connect as real_connect

    calls = []

    def spy(path, **kwargs):
        calls.append(kwargs)
        return real_connect(path, **kwargs)

    monkeypatch.setattr(tools, "connect", spy)
    assert run(real_db_path, real_conn, "low_disk_devices").error is None
    assert calls == [{"read_only": True}]


# ---- model-facing schemas -------------------------------------------------------------


def test_tools_expose_enumerated_bounded_inputs_and_no_tenant_arguments():
    by_name = {t.name: t for t in TOOLS}
    assert set(by_name) == {
        "low_disk_devices",
        "macos_version_check",
        "compliance_failures_now",
        "memory_pressure_now",
        "trend",
        "find_devices",
        "device_history",
    }
    assert set(DEVICE_FILTER_TOOLS) == set(by_name) - {"device_history"}
    for tool in TOOLS:
        schema = tool.tool_call_schema.model_json_schema()
        text = json.dumps(schema).lower()
        assert "runtime" not in text and "company" not in text and "tenant" not in text, tool.name
    trend = by_name["trend"].tool_call_schema.model_json_schema()["properties"]
    assert trend["metric"]["enum"] == ["memory", "disk", "compliance", "battery"]
    window = trend["window_days"]["anyOf"][0]
    assert (window["minimum"], window["maximum"]) == (1, 60)
    checks = by_name["compliance_failures_now"].tool_call_schema.model_json_schema()["properties"][
        "check_id"
    ]
    assert checks["anyOf"][0]["enum"] == ["disk_encryption", "screen_lock", "os_up_to_date"]


# ---- through a real ToolNode ----------------------------------------------------------


class _State(TypedDict):
    messages: Annotated[list, add_messages]


def _through_toolnode(call, ctx: AgentContext) -> ToolMessage:
    def agent(state):
        if any(isinstance(m, ToolMessage) for m in state["messages"]):
            return {"messages": [AIMessage(content="done")]}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    graph = StateGraph(_State, context_schema=AgentContext)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(TOOLS, handle_tool_errors=tool_error_text))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        lambda s: "tools" if getattr(s["messages"][-1], "tool_calls", None) else END,
        ["tools", END],
    )
    graph.add_edge("tools", "agent")
    out = graph.compile().invoke({"messages": [HumanMessage("q")]}, context=ctx)
    return next(m for m in out["messages"] if isinstance(m, ToolMessage))


def test_toolnode_injects_the_server_side_tenant_and_returns_a_typed_artifact(
    real_db_path, real_conn
):
    ctx = context_for(real_db_path, real_conn, ACME)
    message = _through_toolnode(
        {"name": "low_disk_devices", "args": {"threshold_pct": 10.0}, "id": "c1"}, ctx
    )
    assert message.status == "success"
    record = ToolRecord.model_validate(message.artifact)
    assert record.device_ids == ACME_LOW_DISK
    assert json.loads(message.content)["match_count"] == 2


def test_a_tenant_argument_smuggled_into_a_tool_call_changes_nothing(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn, ACME)
    call = {
        "name": "low_disk_devices",
        "args": {"company_id": GLOBEX, "threshold_pct": 10.0},
        "id": "c1",
    }
    message = _through_toolnode(call, ctx)
    if message.status == "success":
        assert ToolRecord.model_validate(message.artifact).device_ids == ACME_LOW_DISK
    else:
        assert str(message.content).startswith("Invalid tool input")


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [({"metric": "memory", "window_days": 999}, "window_days"), ({"metric": "nope"}, "metric")],
)
def test_invalid_input_gets_a_fixed_format_error_without_leaking_values(
    real_db_path, real_conn, args, expected_fragment
):
    ctx = context_for(real_db_path, real_conn)
    message = _through_toolnode({"name": "trend", "args": args, "id": "c1"}, ctx)
    assert message.status == "error" and message.artifact is None
    content = str(message.content)
    assert content.startswith("Invalid tool input") and expected_fragment in content
    assert "999" not in content and "nope" not in content and "Traceback" not in content
    assert "ToolInvocationError" not in content


def test_an_empty_string_filter_is_rejected_rather_than_widening_the_query(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    message = _through_toolnode({"name": "find_devices", "args": {"hostname": ""}, "id": "c1"}, ctx)
    assert message.status == "error" and message.artifact is None
    assert str(message.content).startswith("Invalid tool input")


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("macos_version_check", {"older_than": "SECRET-xyz"}),
        ("low_disk_devices", {"threshold_pct": "SECRETSTR"}),
        ("low_disk_devices", {"device_ids": ["SECRETID"] * 101}),
        ("low_disk_devices", {"device_ids": "SECRETSCALAR"}),
    ],
)
def test_other_validation_errors_never_echo_the_supplied_value(
    real_db_path, real_conn, tool_name, args
):
    ctx = context_for(real_db_path, real_conn)
    message = _through_toolnode({"name": tool_name, "args": args, "id": "c1"}, ctx)
    assert message.status == "error" and message.artifact is None
    content = str(message.content)
    assert content.startswith("Invalid tool input")
    assert "SECRET" not in content


ACME_EMPLOYEE = "emp-acme-1003"
ACME_EMPLOYEE_DEVICES = ["9MXJVZ1I0DKA", "DLT35MHGTH2D", "MT7PJB7N5LRE"]


def test_employee_id_narrows_the_query_but_is_redacted_from_the_record(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    record = execute("find_devices", {"employee_id": ACME_EMPLOYEE}, ctx)
    assert record.device_ids == ACME_EMPLOYEE_DEVICES
    assert ACME_EMPLOYEE not in json.dumps(record.model_dump(mode="json"))
    assert record.args["employee_id"] == "applied"


def test_employee_id_is_redacted_on_the_not_found_branch_too(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    args = {"employee_id": ACME_EMPLOYEE, "device_ids": ["JRZSGXVMKE6M"]}
    record = execute("find_devices", args, ctx)
    assert record.error == "not_found"
    assert ACME_EMPLOYEE not in json.dumps(record.model_dump(mode="json"))
    assert record.args["employee_id"] == "applied"


def test_employee_id_does_not_reach_the_model_or_the_artifact_through_the_toolnode(
    real_db_path, real_conn
):
    ctx = context_for(real_db_path, real_conn)
    call = {"name": "find_devices", "args": {"employee_id": ACME_EMPLOYEE}, "id": "c1"}
    message = _through_toolnode(call, ctx)
    assert message.status == "success"
    assert ACME_EMPLOYEE not in str(message.content)
    assert ACME_EMPLOYEE not in json.dumps(message.artifact)
    assert ToolRecord.model_validate(message.artifact).device_ids == ACME_EMPLOYEE_DEVICES


def test_group_by_is_recorded_for_the_composer(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    assert execute("find_devices", {"group_by": "os"}, ctx).args["group_by"] == "os"


def test_unexpected_failures_surface_as_a_generic_message():
    text = tool_error_text(RuntimeError("secret detail: /tmp/db"))
    assert text == "The data lookup failed. No result is available for this call."
    assert "secret" not in text


def test_execute_never_raises_not_found_and_validates_its_runner_name(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    assert (
        execute("device_history", {"device_id": "NOPE", "metric": "memory_used_pct"}, ctx).error
        == "not_found"
    )
    with pytest.raises(KeyError):
        execute("no_such_tool", {}, ctx)
