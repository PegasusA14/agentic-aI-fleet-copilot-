import json
from datetime import UTC, datetime

from fleet_copilot.agent.records import FindingRecord, ToolRecord
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
