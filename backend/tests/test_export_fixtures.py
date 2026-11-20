import json

import pytest

from fleet_copilot.agent.export_fixtures import SCENARIOS, TENANTS, export_all
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures")
    export_all(out)
    return out


def load(out, tenant, name):
    return json.loads((out / tenant / f"{name}.json").read_text(encoding="utf-8"))


def test_every_scenario_is_exported_for_every_tenant(exported):
    for tenant in TENANTS:
        names = sorted(p.stem for p in (exported / tenant).glob("*.json"))
        assert names == sorted(s.name for s in SCENARIOS)


def test_every_envelope_and_trace_event_validates_against_the_contract(exported):
    for tenant in TENANTS:
        for scenario in SCENARIOS:
            fixture = load(exported, tenant, scenario.name)
            envelope = AnswerEnvelope.model_validate(fixture["envelope"])
            assert envelope.scope.company_id == tenant
            assert envelope.trace_id == f"replay-{tenant}-{scenario.name}"
            assert fixture["trace"], (tenant, scenario.name)
            for seq, event in enumerate(fixture["trace"], start=1):
                parsed = TraceEvent.model_validate(event)
                assert parsed.seq == seq


def test_outcomes_cover_every_state_the_ui_renders(exported):
    outcomes = {load(exported, "acme-001", s.name)["envelope"]["outcome"] for s in SCENARIOS}
    assert outcomes == {
        "answer",
        "no_match",
        "insufficient_data",
        "clarification",
        "proposal",
        "denied",
        "unsupported",
        "tool_error",
    }


def test_meta_marks_synthetic_fixtures_and_carries_router_keywords(exported):
    synthetic = {s.name for s in SCENARIOS if s.synthetic}
    assert synthetic == {"clarification", "unsupported", "proposal", "tool_error"}
    for scenario in SCENARIOS:
        meta = load(exported, "acme-001", scenario.name)["meta"]
        assert meta["synthetic"] is scenario.synthetic
        assert meta["scenario"] == scenario.name and meta["tenant"] == "acme-001"
        assert meta["keywords"] and meta["question"]
        assert (meta["note"] != "") == scenario.synthetic


def test_real_answers_match_the_golden_facts(exported):
    low_disk = load(exported, "acme-001", "low_disk")["envelope"]
    assert low_disk["claims"][0]["device_ids"] == ["MT7PJB7N5LRE", "8NM23J95R5I6"]
    assert low_disk["coverage"]["population"] == 10
    macos = load(exported, "acme-001", "macos_older_than_15")["envelope"]
    assert macos["claims"][0]["count"] == 4
    no_match = load(exported, "acme-001", "compliance_high_no_match")["envelope"]
    assert (no_match["outcome"], no_match["reason_code"]) == ("no_match", "no_matching_records")


def test_a_foreign_device_is_denied_with_the_same_wording_as_any_unknown_one(exported):
    for tenant in TENANTS:
        denied = load(exported, tenant, "device_not_found")["envelope"]
        assert (denied["outcome"], denied["reason_code"]) == ("denied", "not_found")
        assert denied["claims"] == [] and denied["evidence"] == []
    headlines = {load(exported, t, "device_not_found")["envelope"]["headline"] for t in TENANTS}
    assert headlines == {"I can't find that in your fleet."}


def test_no_employee_ids_or_other_tenants_leak_into_a_tenant_directory(exported):
    for tenant in TENANTS:
        others = [t for t in TENANTS if t != tenant]
        for path in (exported / tenant).glob("*.json"):
            text = path.read_text(encoding="utf-8")
            assert "emp-" not in text, path
            assert not any(f"{other}/" in text for other in others), path


def test_the_index_lists_every_fixture_with_its_question_and_keywords(exported):
    index = json.loads((exported / "index.json").read_text(encoding="utf-8"))
    assert sorted(index) == sorted(TENANTS)
    for tenant in TENANTS:
        assert [m["scenario"] for m in index[tenant]] == [s.name for s in SCENARIOS]
        assert all(m["question"] and m["keywords"] for m in index[tenant])


def test_the_exporter_is_deterministic(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    export_all(first)
    export_all(second)
    names = sorted(p.relative_to(first).as_posix() for p in first.rglob("*.json"))
    assert names == sorted(p.relative_to(second).as_posix() for p in second.rglob("*.json"))
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
