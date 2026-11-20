import copy

import pytest
from agent_support import ACME, GLOBEX, INITECH, repo_for, run

from fleet_copilot.agent.compose import Composition, compose, representative
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.agent.texts import NOT_FOUND, PARTIAL_NOT_FOUND, TOOL_FAILED, unresolved_note
from fleet_copilot.contracts import AnswerEnvelope
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import dataset_as_of


def composed(real_db_path, real_conn, calls, tenant=ACME) -> Composition:
    records = [run(real_db_path, real_conn, name, args, tenant) for name, args in calls]
    return compose(records, resolve=repo_for(real_conn, tenant).resolve)


def as_envelope(comp: Composition, real_conn, tenant=ACME, **overrides) -> AnswerEnvelope:
    return comp.to_envelope(
        company_id=tenant, as_of=dataset_as_of(real_conn), trace_id="t-1", **overrides
    )


def test_representative_keeps_first_last_and_order():
    refs = [f"r{i}" for i in range(10)]
    picked = representative(refs, 6)
    assert len(picked) == 6 and picked[0] == "r0" and picked[-1] == "r9"
    assert picked == sorted(picked, key=refs.index)
    assert representative(refs[:4], 6) == refs[:4]
    assert representative(["a", "a", "b"], 6) == ["a", "b"]


def test_low_disk_answer_is_fully_composed_by_code(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {})])
    assert comp.outcome == "answer" and comp.reason_code is None
    claim = comp.claims[0]
    assert claim.device_ids == ["MT7PJB7N5LRE", "8NM23J95R5I6"] and claim.count == 2
    assert "2 of 10 devices" in claim.text
    assert "2.0% free" in claim.text and "2.6% free" in claim.text
    assert "acme-macbook-4" in claim.text and "acme-thinkpad-8" in claim.text
    assert comp.coverage.population == 10
    assert comp.entity_lists[0].items[0].device_id == "MT7PJB7N5LRE"
    assert comp.entity_lists[0].items[0].label == "acme-macbook-4"
    assert comp.fallback_headline == "2 of 10 devices have under 10% disk space free"


def test_every_claim_ref_has_resolved_evidence_rendered_by_code(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {})])
    by_ref = {e.ref: e for e in comp.evidence}
    assert set(comp.claims[0].refs) <= set(by_ref)
    disk = by_ref[next(r for r in by_ref if "MT7PJB7N5LRE" in r)]
    assert "free of 512.0 GiB" in disk.value and "(2.0% free)" in disk.value
    assert "acme-macbook-4" in disk.label and "Disk" in disk.label
    assert all(EvidenceRef.parse(ref).company_id == ACME for ref in by_ref)


def test_the_composition_validates_as_an_answer_envelope(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {})])
    envelope = as_envelope(comp, real_conn)
    assert envelope.outcome == "answer" and envelope.trace_id == "t-1"
    assert envelope.headline == comp.fallback_headline
    assert envelope.scope.population == 10 and "free" in envelope.scope.rule
    with_text = as_envelope(comp, real_conn, headline="Custom", explanation="Why it matters.")
    assert (with_text.headline, with_text.explanation) == ("Custom", "Why it matters.")


def test_macos_check_scopes_to_macos_and_discloses_the_rest(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("macos_version_check", {"older_than": "15"})])
    claim = comp.claims[0]
    assert claim.device_ids == ["1LYSSFD074BB", "7VP16KHV88LM", "MT7PJB7N5LRE", "XSU9UUDGU6AC"]
    assert "4 of 7 macOS devices" in claim.text and "macOS 13.6" in claim.text
    assert comp.coverage.excluded["out_of_scope"] == [
        "8NM23J95R5I6",
        "9MXJVZ1I0DKA",
        "M4XVHUV1MEPZ",
    ]
    assert any("3 devices are out of scope" in n for n in comp.notes)
    assert comp.fallback_headline == "4 of 7 macOS devices run a version older than macOS 15"


def test_high_severity_has_no_match_and_says_what_was_evaluated(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("compliance_failures_now", {"severity": "high", "form_factor": "laptop"})],
    )
    assert (comp.outcome, comp.reason_code) == ("no_match", "no_matching_records")
    assert comp.claims == [] and comp.evidence == []
    assert comp.coverage.population == 9
    assert comp.fallback_headline == "No devices matched (9 evaluated)"
    envelope = as_envelope(comp, real_conn)
    assert envelope.outcome == "no_match" and envelope.reason_code == "no_matching_records"


def test_compliance_failures_group_by_check_with_one_entity_list_each(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("compliance_failures_now", {"severity": "medium", "form_factor": "laptop"})],
    )
    by_check = {c.text.split(" ")[1]: c for c in comp.claims}
    assert by_check["screen_lock"].device_ids == [
        "1LYSSFD074BB",
        "8NM23J95R5I6",
        "DSYPSES7SKBB",
        "MT7PJB7N5LRE",
    ]
    assert len(by_check["os_up_to_date"].device_ids) == 9
    assert len(comp.entity_lists) == 2
    assert {e.title for e in comp.entity_lists} == {
        "screen_lock failures",
        "os_up_to_date failures",
    }
    assert all(r.endswith("]") for c in comp.claims for r in c.refs)  # compliance[...] refs only


def test_memory_trend_reports_classes_with_counts_from_the_data(real_db_path, real_conn):
    comp = composed(
        real_db_path, real_conn, [("trend", {"metric": "memory", "window_days": 30})], tenant=GLOBEX
    )
    texts = " ".join(c.text for c in comp.claims)
    assert "Intermittent" in texts and "CUXW0BQSV1B7" in texts and "JRZSGXVMKE6M" in texts
    assert "13 of 30 snapshots" in texts  # CUXW0BQSV1B7 breaches at 85% in 13 of 30 snapshots
    strict = composed(
        real_db_path,
        real_conn,
        [("trend", {"metric": "memory", "window_days": 30, "threshold_pct": 90.0})],
        tenant=GLOBEX,
    )
    assert any("Isolated" in c.text and "CUXW0BQSV1B7" in c.text for c in strict.claims)


def test_disk_trend_separates_sustained_from_onset(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("trend", {"metric": "disk", "window_days": 30})])
    sustained = next(c for c in comp.claims if c.text.startswith("Sustained"))
    onset = next(c for c in comp.claims if c.text.startswith("Onset"))
    assert sustained.device_ids == ["MT7PJB7N5LRE"] and onset.device_ids == ["8NM23J95R5I6"]
    assert all(len(set(c.refs)) <= 6 for c in comp.claims)  # at most 6 cited readings per device


def test_compliance_trend_names_the_first_failing_snapshot(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("trend", {"metric": "compliance", "check_id": "screen_lock", "window_days": 30})],
    )
    claim = comp.claims[0]
    assert "changed from pass to fail" in claim.text
    assert "1LYSSFD074BB" in claim.text and "2026-05-22 09:41Z" in claim.text
    assert "failing since 2026-05-22 09:41Z" in claim.text
    assert claim.count == 5  # four laptops plus the Mac mini


def test_battery_note_states_the_missing_design_capacity(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("trend", {"metric": "battery"})])
    claim = comp.claims[0]
    assert claim.device_ids == ["7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"]
    assert "-4.2%" in claim.text and "Service Recommended" in claim.text
    assert any("design capacity" in n for n in comp.notes)
    assert any(
        "MT7PJB7N5LRE" in n and "latest snapshot" in n for n in comp.notes
    )  # missing_latest disclosed


def test_inventory_grouping_counts_come_from_code(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("find_devices", {"group_by": "os"})])
    text = comp.claims[0].text
    for fragment in (
        "macOS 14.5: 3",
        "macOS 15.3: 2",
        "macOS 13.6: 1",
        "macOS 15.4: 1",
        "Windows 10 22H2: 3",
    ):
        assert fragment in text
    assert comp.claims[0].count == 10


def test_device_history_summarises_first_and_latest(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [
            (
                "device_history",
                {"device_id": "8NM23J95R5I6", "metric": "battery_capacity", "window_days": 30},
            )
        ],
    )
    text = comp.claims[0].text
    assert "first 4043" in text and "latest 3872" in text and "30 snapshots" in text
    assert len(comp.claims[0].refs) <= 6


def test_uniform_denial_for_unknown_devices(real_db_path, real_conn):
    foreign = composed(
        real_db_path,
        real_conn,
        [("device_history", {"device_id": "JRZSGXVMKE6M", "metric": "memory_used_pct"})],
    )
    unknown = composed(
        real_db_path,
        real_conn,
        [("device_history", {"device_id": "NOPE", "metric": "memory_used_pct"})],
    )
    for comp in (foreign, unknown):
        assert (comp.outcome, comp.reason_code) == ("denied", "not_found")
        assert comp.claims == [] and comp.evidence == [] and comp.coverage is None
        assert comp.fallback_headline == NOT_FOUND
    assert foreign.fallback_headline == unknown.fallback_headline
    assert as_envelope(foreign, real_conn).outcome == "denied"


def test_a_failed_tool_yields_tool_error_with_no_partial_claims(real_db_path, real_conn):
    good = run(real_db_path, real_conn, "low_disk_devices")
    broken = ToolRecord(tool="trend", args={}, error="tool_error")
    comp = compose([good, broken], resolve=repo_for(real_conn).resolve)
    assert (comp.outcome, comp.reason_code) == ("tool_error", "tool_failure")
    assert comp.claims == [] and comp.fallback_headline == TOOL_FAILED


def test_all_trend_devices_excluded_for_thin_history_is_insufficient_data(real_conn):
    record = ToolRecord(
        tool="trend",
        args={"metric": "memory", "window_days": 1},
        population=2,
        rule="memory trend",
        excluded={"insufficient_history": ["A", "B"]},
    )
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    assert (comp.outcome, comp.reason_code) == ("insufficient_data", "insufficient_history")
    assert comp.coverage.population == 2 and comp.coverage.excluded["insufficient_history"] == [
        "A",
        "B",
    ]


def test_multiple_tools_combine_into_one_composition(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("low_disk_devices", {}), ("macos_version_check", {"older_than": "15"})],
    )
    assert len(comp.claims) == 2 and len(comp.entity_lists) == 2
    assert {e.list_id for e in comp.entity_lists} == {"low_disk_devices-1", "macos_version_check-2"}
    assert as_envelope(comp, real_conn).outcome == "answer"


@pytest.mark.parametrize("tenant", [ACME, GLOBEX, INITECH])
def test_every_tool_composes_into_a_valid_envelope_for_every_tenant(
    real_db_path, real_conn, tenant
):
    calls = [
        ("low_disk_devices", {}),
        ("macos_version_check", {}),
        ("compliance_failures_now", {"severity": "medium"}),
        ("memory_pressure_now", {}),
        ("trend", {"metric": "memory", "window_days": 30}),
        ("trend", {"metric": "disk", "window_days": 30}),
        ("trend", {"metric": "compliance", "check_id": "screen_lock", "window_days": 30}),
        ("trend", {"metric": "battery"}),
        ("find_devices", {"group_by": "model"}),
    ]
    for name, args in calls:
        comp = composed(real_db_path, real_conn, [(name, args)], tenant=tenant)
        envelope = as_envelope(comp, real_conn, tenant)  # contract validation is the assertion
        assert all(EvidenceRef.parse(e.ref).company_id == tenant for e in envelope.evidence), (
            tenant,
            name,
        )


def test_battery_note_does_not_claim_an_earlier_reading_for_laptops_with_no_reading(
    real_db_path, real_conn
):
    comp = composed(real_db_path, real_conn, [("trend", {"metric": "battery", "window_days": 1})])
    assert any("no battery reading in the window" in n and "MT7PJB7N5LRE" in n for n in comp.notes)
    assert not any("newest earlier reading was used" in n for n in comp.notes)
    assert not any("MT7PJB7N5LRE" in n and "latest snapshot" in n for n in comp.notes)
    assert "MT7PJB7N5LRE" in comp.coverage.missing_latest


def test_differing_populations_are_disclosed_and_headlines_are_joined(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("low_disk_devices", {}), ("macos_version_check", {"older_than": "15"})],
    )
    assert comp.coverage.population == 10
    assert (
        "Populations differ per check: 10 (low_disk_devices), 7 (macos_version_check)."
        in comp.notes
    )
    assert comp.fallback_headline == (
        "2 of 10 devices have under 10% disk space free; 4 of 7 macOS devices run a version older than macOS 15"
    )
    assert as_envelope(comp, real_conn).scope.population == 10


def test_a_single_record_has_no_population_note(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {})])
    assert not any("Populations differ" in n for n in comp.notes)


def test_battery_with_no_findings_still_discloses_the_missing_design_capacity(
    real_db_path, real_conn
):
    comp = composed(real_db_path, real_conn, [("trend", {"metric": "battery"})], tenant=GLOBEX)
    assert comp.outcome == "no_match"
    assert any("design capacity" in n for n in comp.notes)


def test_design_capacity_note_appears_once_for_two_battery_records(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("trend", {"metric": "battery"}), ("trend", {"metric": "battery", "window_days": 60})],
    )
    assert sum("design capacity" in n for n in comp.notes) == 1


def test_recovered_compliance_reads_passing_since(real_db_path, real_conn):
    record = run(
        real_db_path,
        real_conn,
        "trend",
        {"metric": "compliance", "check_id": "screen_lock", "window_days": 30},
    )
    altered = copy.deepcopy(record)
    altered.findings[0].values["kind"] = "recovered"
    comp = compose([altered], resolve=repo_for(real_conn).resolve)
    recovered = next(c for c in comp.claims if "changed from fail back to pass" in c.text)
    assert "passing since 2026-05-22 09:41Z" in recovered.text
    assert "failing since" not in recovered.text and "first seen" not in recovered.text


MIXED_VALID = ["MT7PJB7N5LRE", "8NM23J95R5I6"]


def test_a_partly_unknown_device_list_is_disclosed_in_the_notes(real_db_path, real_conn):
    ids = [*MIXED_VALID, "JRZSGXVMKE6M", "NOPE"]
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {"device_ids": ids})])
    assert comp.outcome == "answer" and comp.coverage.population == 2
    assert comp.notes.count(unresolved_note(2)) == 1
    assert "2 requested devices" in unresolved_note(2) and "were not included" in unresolved_note(2)
    assert as_envelope(comp, real_conn).notes == comp.notes


def test_the_unresolved_note_reads_the_same_for_foreign_and_nonexistent_ids(
    real_db_path, real_conn
):
    foreign = composed(
        real_db_path,
        real_conn,
        [("low_disk_devices", {"device_ids": [*MIXED_VALID, "JRZSGXVMKE6M"]})],
    )
    unknown = composed(
        real_db_path, real_conn, [("low_disk_devices", {"device_ids": [*MIXED_VALID, "NOPE"]})]
    )
    assert foreign.notes == unknown.notes
    assert unresolved_note(1) in foreign.notes
    assert "1 requested device could not be found" in unresolved_note(1)
    assert "was not included" in unresolved_note(1)


def test_unresolved_counts_are_summed_into_one_note(real_db_path, real_conn):
    calls = [
        ("low_disk_devices", {"device_ids": [*MIXED_VALID, "NOPE"]}),
        ("macos_version_check", {"device_ids": ["MT7PJB7N5LRE", "NOPE", "ALSO-NOPE"]}),
    ]
    comp = composed(real_db_path, real_conn, calls)
    assert [n for n in comp.notes if "requested" in n] == [unresolved_note(3)]


def test_an_all_valid_device_list_adds_no_unresolved_note(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {"device_ids": MIXED_VALID})])
    assert not any("requested" in n for n in comp.notes)


def test_a_not_found_record_next_to_usable_ones_is_disclosed(real_db_path, real_conn):
    calls = [
        ("low_disk_devices", {}),
        ("device_history", {"device_id": "NOPE", "metric": "memory_used_pct"}),
    ]
    comp = composed(real_db_path, real_conn, calls)
    assert comp.outcome == "answer" and comp.claims
    assert comp.notes.count(PARTIAL_NOT_FOUND) == 1
    assert PARTIAL_NOT_FOUND == (
        "Part of the request referred to a device that could not be found, so that part was not answered."
    )
    foreign = composed(
        real_db_path,
        real_conn,
        [
            ("low_disk_devices", {}),
            ("device_history", {"device_id": "JRZSGXVMKE6M", "metric": "memory_used_pct"}),
        ],
    )
    assert foreign.notes == comp.notes
    assert as_envelope(comp, real_conn).outcome == "answer"


def test_an_all_not_found_request_is_still_a_denial_without_the_partial_note(
    real_db_path, real_conn
):
    comp = composed(
        real_db_path,
        real_conn,
        [("device_history", {"device_id": "NOPE", "metric": "memory_used_pct"})],
    )
    assert (comp.outcome, comp.notes) == ("denied", [])
