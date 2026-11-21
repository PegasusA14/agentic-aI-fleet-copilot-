import json
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, ToolMessage

from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.records import (
    FindingRecord,
    ToolRecord,
    record_from_message,
    redact_args,
    redact_tool_calls,
)
from fleet_copilot.fleet.queries import Finding, QueryResult

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)


def query_result():
    findings = tuple(
        Finding(
            f"D{i}",
            f"host-{i}",
            {"trend": "sustained" if i < 2 else "isolated", "n": i},
            (f"c/D{i}@t#memory",),
        )
        for i in range(3)
    )
    return QueryResult(
        findings, 10, AS_OF, "a rule", excluded={"out_of_scope": ("W1", "W2")}, stale=("D1",)
    )


def test_from_query_copies_everything_and_is_json_safe():
    record = ToolRecord.from_query("trend", {"metric": "memory"}, query_result())
    assert record.device_ids == ["D0", "D1", "D2"]
    assert record.population == 10 and record.rule == "a rule"
    assert record.excluded == {"out_of_scope": ["W1", "W2"]}
    assert record.stale == ["D1"] and record.error is None
    json.dumps(record.model_dump(mode="json"))  # must not raise


def test_llm_view_is_compact_and_has_counts_not_raw_values():
    view = json.loads(ToolRecord.from_query("trend", {}, query_result()).llm_view())
    assert view["match_count"] == 3 and view["population"] == 10
    assert view["device_ids"] == ["D0", "D1", "D2"]
    assert view["excluded_counts"] == {"out_of_scope": 2}
    assert view["by_class"] == {"sustained": 2, "isolated": 1}
    assert "host-0" not in json.dumps(view)


def test_llm_view_caps_the_device_list():
    findings = tuple(Finding(f"D{i:03d}", "h", {}, ("r",)) for i in range(50))
    view = json.loads(
        ToolRecord.from_query("x", {}, QueryResult(findings, 50, AS_OF, "r")).llm_view()
    )
    assert len(view["device_ids"]) == 30 and view["match_count"] == 50


def test_error_records_have_a_fixed_uniform_view():
    not_found = ToolRecord(tool="device_history", args={"device_id": "A"}, error="not_found")
    other = ToolRecord(tool="device_history", args={"device_id": "B"}, error="not_found")
    assert json.loads(not_found.llm_view()) == json.loads(other.llm_view())
    assert json.loads(not_found.llm_view())["error"] == "not_found"


def test_records_round_trip_through_the_artifact_form():
    record = ToolRecord.from_query("trend", {"metric": "memory"}, query_result())
    assert ToolRecord.model_validate(record.model_dump(mode="json")) == record
    assert isinstance(record.findings[0], FindingRecord)


def _success_message(record: ToolRecord) -> ToolMessage:
    return ToolMessage(
        content=record.llm_view(),
        artifact=record.model_dump(mode="json"),
        tool_call_id="c1",
        status="success",
    )


def test_a_success_message_with_a_real_artifact_round_trips():
    record = ToolRecord.from_query("trend", {"metric": "memory"}, query_result())
    mapped = record_from_message(_success_message(record), tool="trend", args={"metric": "memory"})
    assert mapped == record


def test_an_error_message_becomes_a_tool_error_record_without_its_content():
    message = ToolMessage(
        content="Invalid tool input: secret-sauce leaked here",
        tool_call_id="c1",
        status="error",
    )
    record = record_from_message(
        message, tool="find_devices", args={"employee_id": "emp-7", "os_name": "macOS"}
    )
    assert record.error == "tool_error" and record.findings == []
    dumped = json.dumps(record.model_dump(mode="json"))
    assert "secret-sauce" not in dumped and "emp-7" not in dumped
    assert record.args == {"employee_id": "applied", "os_name": "macOS"}


def test_a_success_message_with_a_corrupt_artifact_is_a_tool_error():
    for artifact in ({"tool": "trend", "bogus": 1}, "not a mapping", None, {"error": "nope"}):
        message = ToolMessage(content="{}", artifact=artifact, tool_call_id="c1", status="success")
        record = record_from_message(message, tool="trend", args={"metric": "memory"})
        assert record.error == "tool_error" and record.tool == "trend"


def test_an_error_message_that_still_carries_an_artifact_is_a_tool_error():
    good = ToolRecord.from_query("trend", {}, query_result())
    message = ToolMessage(
        content="boom", artifact=good.model_dump(mode="json"), tool_call_id="c1", status="error"
    )
    assert record_from_message(message, tool="trend", args={}).error == "tool_error"


def test_a_failed_message_composes_into_a_tool_error_composition():
    message = ToolMessage(content="The data lookup failed.", tool_call_id="c1", status="error")
    record = record_from_message(message, tool="trend", args={"metric": "memory"})
    comp = compose([record], resolve=lambda ref: None)
    assert (comp.outcome, comp.reason_code) == ("tool_error", "tool_failure")


def test_redact_args_hides_only_the_employee_id_and_copies():
    args = {"employee_id": "emp-1", "os_name": "macOS"}
    assert redact_args(args) == {"employee_id": "applied", "os_name": "macOS"}
    assert args["employee_id"] == "emp-1"
    plain = {"os_name": "macOS", "employee_id": None}
    assert redact_args(plain) == plain


def test_redact_tool_calls_removes_the_employee_id_without_mutating_the_input():
    calls = [
        {"name": "find_devices", "args": {"employee_id": "emp-1"}, "id": "c1", "type": "tool_call"},
        {"name": "low_disk_devices", "args": {}, "id": "c2", "type": "tool_call"},
    ]
    message = AIMessage(content="", tool_calls=calls)
    redacted = redact_tool_calls(message.tool_calls)
    assert redacted[0]["args"] == {"employee_id": "applied"}
    assert redacted[0]["name"] == "find_devices" and redacted[0]["id"] == "c1"
    assert redacted[1] == message.tool_calls[1]
    assert message.tool_calls[0]["args"] == {"employee_id": "emp-1"}
    assert "emp-1" not in json.dumps(redacted)


def test_the_model_view_of_suggestions_is_counts_and_action_types_only():
    suggestion = {
        "action_type": "upgrade_order",
        "device_id": "MT7PJB7N5LRE",
        "target_label": "acme-macbook-4",
        "label": "Order a storage upgrade (512 GiB → 1 TiB)",
        "reason": "acme-macbook-4: sustained low disk",
        "request": "Order a storage upgrade for acme-macbook-4",
        "refs": ["acme-001/MT7PJB7N5LRE/2026-06-12T09:00:00Z#disk"],
    }
    record = ToolRecord(
        tool="recommend_actions",
        findings=[
            FindingRecord(device_id="MT7PJB7N5LRE", hostname="acme-macbook-4", values={}, refs=[])
        ],
        population=1,
        suggestions=[suggestion, {**suggestion, "action_type": "employee_notification"}],
    )
    view = json.loads(record.llm_view())
    assert view == {
        "tool": "recommend_actions",
        "status": "recommended",
        "device_count": 1,
        "suggestion_count": 2,
        "action_types": {"upgrade_order": 1, "employee_notification": 1},
    }
    text = record.llm_view()
    assert "acme-macbook-4" not in text and "MT7PJB7N5LRE" not in text and "disk" not in text


def test_an_empty_suggestion_list_is_still_a_recommendation_view():
    record = ToolRecord(tool="recommend_actions", population=2, suggestions=[])
    assert json.loads(record.llm_view())["suggestion_count"] == 0
