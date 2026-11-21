import copy

import pytest
from agent_support import ACME, GLOBEX, INITECH, context_for, repo_for, run

from fleet_copilot.agent import texts
from fleet_copilot.agent.compose import Composition, compose, representative
from fleet_copilot.agent.records import FindingRecord, ToolRecord
from fleet_copilot.agent.texts import NOT_FOUND, PARTIAL_NOT_FOUND, TOOL_FAILED, unresolved_note
from fleet_copilot.agent.tools import execute_recommend
from fleet_copilot.contracts import AnswerEnvelope
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import EvidenceValue, dataset_as_of


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
        ("software_inventory", {"view": "current"}),
        ("software_inventory", {"view": "new_installs"}),
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


# ---- synthetic records for paths the real dataset cannot reach ------------------------------

TS = "2026-06-12T08:39:00Z"


def _ref(device, path):
    return f"{ACME}/{device}@{TS}#{path}"


def _resolve(parsed):
    if parsed.path == "disk":
        value = {"size_bytes": 512 * 1024**3, "available_bytes": 10 * 1024**3}
    else:
        value = {"check_id": "screen_lock", "status": "fail", "severity": "low"}
    return EvidenceValue(ref=str(parsed), collected_at=parsed.collected_at, value=value)


def _low_disk_record(*devices, **overrides):
    findings = [
        FindingRecord(
            device_id=d,
            hostname=f"host-{d}",
            values={"free_pct": 2.0, "available_bytes": 10 * 1024**3, "size_bytes": 512 * 1024**3},
            refs=[_ref(d, "disk")],
        )
        for d in devices
    ]
    base = {
        "tool": "low_disk_devices",
        "findings": findings,
        "population": 10,
        "rule": "free < 10%",
    }
    return ToolRecord(**{**base, **overrides})


def _compliance_record(*rows, population=10):
    """`rows` are (device, severity) pairs that all fail screen_lock."""
    findings = [
        FindingRecord(
            device_id=d,
            hostname=f"host-{d}",
            values={"failing": [{"check_id": "screen_lock", "severity": sev}]},
            refs=[_ref(d, "compliance[screen_lock]")],
        )
        for d, sev in rows
    ]
    return ToolRecord(
        tool="compliance_failures_now",
        findings=findings,
        population=population,
        rule="failing checks",
    )


def _compose(*records):
    return compose(list(records), resolve=_resolve)


def test_facts_expose_only_code_written_text(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("low_disk_devices", {})])
    facts = comp.facts()
    assert set(facts) == {"claims", "notes", "population", "rules"}
    assert facts["claims"] == [c.text for c in comp.claims] and facts["claims"]
    assert facts["notes"] == comp.notes
    assert facts["population"] == comp.coverage.population == 10
    assert facts["rules"] == comp.rules and facts["rules"]


def test_facts_of_a_denial_are_empty():
    denied = compose([ToolRecord(tool="trend", error="not_found")], resolve=_resolve)
    assert denied.facts() == {"claims": [], "notes": [], "population": None, "rules": []}


@pytest.mark.parametrize(
    "comp",
    [
        Composition("denied", "not_found", fallback_headline=NOT_FOUND),
        Composition("tool_error", "tool_failure", fallback_headline=TOOL_FAILED),
        Composition("unsupported", "off_topic", fallback_headline="x"),
    ],
)
def test_an_explanation_is_dropped_when_the_outcome_cannot_carry_one(comp, real_conn):
    envelope = as_envelope(comp, real_conn, explanation="free text")
    assert envelope.explanation is None


def test_an_explanation_is_kept_on_an_answer_and_on_empty_results(real_db_path, real_conn):
    answer = composed(real_db_path, real_conn, [("low_disk_devices", {})])
    assert as_envelope(answer, real_conn, explanation="x").explanation == "x"
    empty = composed(real_db_path, real_conn, [("compliance_failures_now", {"severity": "high"})])
    assert empty.outcome == "no_match"
    assert as_envelope(empty, real_conn, explanation="x").explanation == "x"


def test_stale_devices_reach_coverage_and_a_note():
    comp = _compose(_low_disk_record("D1", "D2", stale=["D1"]))
    assert comp.coverage.stale == ["D1"]
    note = next(n for n in comp.notes if "had readings older than 48 hours" in n)
    assert note.startswith("1 device had readings") and "D1" in note
    many = _compose(_low_disk_record("D1", "D2", stale=["D2", "D1"]))
    assert many.coverage.stale == ["D1", "D2"]
    assert any(n.startswith("2 devices had readings older than 48 hours") for n in many.notes)


def test_stale_devices_merge_across_records():
    comp = _compose(
        _low_disk_record("D1", stale=["D1"]),
        _low_disk_record("D2", stale=["D1", "D2"]),
    )
    assert comp.coverage.stale == ["D1", "D2"]


def test_missing_latest_reaches_coverage_and_a_note_with_agreeing_plurals():
    one = _compose(_low_disk_record("D1", missing_latest=["D1"]))
    assert one.coverage.missing_latest == ["D1"]
    note = next(n for n in one.notes if "latest snapshot" in n)
    assert note.startswith("1 laptop had no battery reading in the latest snapshot")
    assert "D1" in note
    two = _compose(_low_disk_record("D1", "D2", missing_latest=["D2", "D1"]))
    assert two.coverage.missing_latest == ["D1", "D2"]
    assert any(n.startswith("2 laptops had no battery reading") for n in two.notes)


def _thin(population, excluded):
    return ToolRecord(
        tool="trend",
        args={"metric": "memory", "window_days": 1},
        population=population,
        rule="memory trend",
        excluded={"insufficient_history": excluded},
    )


def test_insufficient_data_needs_every_device_excluded_for_thin_history():
    every = _compose(_thin(2, ["A", "B"]))
    assert (every.outcome, every.reason_code) == ("insufficient_data", "insufficient_history")
    more = _compose(_thin(2, ["A", "B", "C"]))
    assert more.outcome == "insufficient_data"
    fewer = _compose(_thin(3, ["A", "B"]))
    assert (fewer.outcome, fewer.reason_code) == ("no_match", "no_matching_records")
    assert fewer.coverage.excluded == {"insufficient_history": ["A", "B"]}


def test_an_empty_population_is_no_match_not_insufficient_data():
    comp = _compose(_thin(0, ["A"]))
    assert (comp.outcome, comp.reason_code) == ("no_match", "no_matching_records")
    assert _compose(_thin(0, [])).outcome == "no_match"


def test_every_usable_record_must_be_thin_for_insufficient_data():
    plain = ToolRecord(tool="trend", args={"metric": "memory"}, population=4, rule="r")
    assert _compose(_thin(2, ["A", "B"]), plain).outcome == "no_match"


def test_one_severity_is_reported_as_is():
    comp = _compose(_compliance_record(("D1", "medium"), ("D2", "medium")))
    assert "Failing screen_lock (medium severity)" in comp.claims[0].text


def test_differing_severities_for_one_check_are_all_reported():
    comp = _compose(_compliance_record(("D1", "medium"), ("D2", "low"), ("D3", "medium")))
    text = comp.claims[0].text
    assert "Failing screen_lock (low/medium severity)" in text
    assert comp.claims[0].device_ids == ["D1", "D2", "D3"]


def test_scope_calculation_counts_distinct_devices_not_claim_counts(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [
            ("low_disk_devices", {"device_ids": ["MT7PJB7N5LRE", "8NM23J95R5I6"]}),
            ("macos_version_check", {"device_ids": ["MT7PJB7N5LRE"]}),
        ],
    )
    assert sum(c.count for c in comp.claims) == 3
    assert as_envelope(comp, real_conn).scope.calculation == (
        "2 distinct devices across 2 claims; at most 2 supporting readings are cited per device"
    )


def test_scope_calculation_uses_singular_forms(real_db_path, real_conn):
    comp = composed(
        real_db_path, real_conn, [("low_disk_devices", {"device_ids": ["MT7PJB7N5LRE"]})]
    )
    assert as_envelope(comp, real_conn).scope.calculation == (
        "1 distinct device across 1 claim; at most 1 supporting reading is cited per device"
    )


def test_scope_calculation_is_absent_without_claims(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("compliance_failures_now", {"severity": "high"})])
    assert as_envelope(comp, real_conn).scope.calculation is None


def test_entity_list_ids_are_namespaced_by_the_trace_id(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("low_disk_devices", {}), ("compliance_failures_now", {})],
    )
    raw = [e.list_id for e in comp.entity_lists]
    first = comp.to_envelope(company_id=ACME, as_of=dataset_as_of(real_conn), trace_id="turn-1")
    second = comp.to_envelope(company_id=ACME, as_of=dataset_as_of(real_conn), trace_id="turn-2")
    ids_1 = [e.list_id for e in first.entity_lists]
    ids_2 = [e.list_id for e in second.entity_lists]
    assert len(ids_1) == len(raw) >= 2
    assert not set(ids_1) & set(ids_2)
    assert all(i.startswith("turn-1/") for i in ids_1)
    assert all(i.startswith("turn-2/") for i in ids_2)
    assert ids_1 == [f"turn-1/{r}" for r in raw]
    assert [e.list_id for e in comp.entity_lists] == raw  # the composition itself is untouched
    assert [e.items for e in first.entity_lists] == [e.items for e in comp.entity_lists]


# ---- Plan 2B: the excluded-reason keys cannot drift between queries, composer and contract ----------


def test_composer_reason_text_covers_exactly_the_contract_reason_keys():
    from typing import get_args

    from fleet_copilot.agent.compose import _REASON_TEXT
    from fleet_copilot.contracts import ExcludedReason

    assert set(_REASON_TEXT) == set(get_args(ExcludedReason))


def test_every_reason_key_a_query_emits_is_allowed_by_the_contract(real_db_path, real_conn):
    from typing import get_args

    from fleet_copilot.contracts import ExcludedReason

    calls = [
        ("low_disk_devices", {}),
        ("macos_version_check", {}),
        ("compliance_failures_now", {}),
        ("memory_pressure_now", {}),
        ("trend", {"metric": "memory", "window_days": 1}),
        ("trend", {"metric": "disk", "window_days": 1}),
        ("trend", {"metric": "compliance", "check_id": "screen_lock", "window_days": 1}),
        ("trend", {"metric": "battery", "window_days": 1}),
        ("find_devices", {}),
        ("device_history", {"device_id": "DLT35MHGTH2D", "metric": "battery_capacity"}),
        ("software_inventory", {"view": "current"}),
        ("software_inventory", {"view": "new_installs", "window_days": 1}),
    ]
    seen: set[str] = set()
    for tenant in (ACME, GLOBEX, INITECH):
        for name, args in calls:
            if name == "device_history" and tenant != ACME:
                continue
            seen |= set(run(real_db_path, real_conn, name, args, tenant).excluded)
    assert seen, "the sweep must exercise at least one exclusion"
    assert seen <= set(get_args(ExcludedReason))


def test_scope_calculation_states_the_real_maximum_of_readings_per_device(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("compliance_failures_now", {"check_id": "screen_lock"})],
    )
    cited: dict[str, set[str]] = {}
    for claim in comp.claims:
        for ref in claim.refs:
            cited.setdefault(EvidenceRef.parse(ref).device_id, set()).add(ref)
    most = max(len(refs) for refs in cited.values())
    calculation = as_envelope(comp, real_conn).scope.calculation
    assert f"at most {most} supporting reading" in calculation


def test_suggestions_compose_into_claims_with_refs_a_list_and_facts(real_db_path, real_conn):
    record = execute_recommend(
        {"device_ids": ["MT7PJB7N5LRE", "8NM23J95R5I6"]}, context_for(real_db_path, real_conn)
    )
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    envelope = as_envelope(comp, real_conn)
    assert envelope.outcome == "answer"
    assert [c.text for c in envelope.claims] == [s.reason for s in envelope.suggested_actions]
    assert all(c.refs for c in envelope.claims)
    assert envelope.entity_lists[0].title == "Suggested actions"
    facts = comp.facts()
    assert facts["suggested_actions"] == [s.label for s in envelope.suggested_actions]


def test_no_suggestions_compose_into_no_match_with_a_note_per_device(real_db_path, real_conn):
    record = ToolRecord(
        tool="recommend_actions",
        findings=[FindingRecord(device_id="D1", hostname="host-1", values={}, refs=[])],
        population=1,
        suggestions=[],
    )
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    assert comp.outcome == "no_match" and comp.fallback_headline == texts.NO_SUGGESTIONS
    assert comp.notes == [texts.no_suggestion_note("host-1")]


# ---- a find_devices lookup that only narrows a later call is scaffolding --------------------


def _find_then(real_db_path, real_conn, find_args, tool, args, tenant=ACME):
    """A find_devices record, then `tool` called with exactly the devices that find returned."""
    found = run(real_db_path, real_conn, "find_devices", find_args, tenant)
    later = run(real_db_path, real_conn, tool, {**args, "device_ids": found.device_ids}, tenant)
    return found, later, compose([found, later], resolve=repo_for(real_conn, tenant).resolve)


def test_a_find_used_only_to_narrow_a_battery_trend_adds_no_claim_or_list(real_db_path, real_conn):
    found, later, comp = _find_then(
        real_db_path, real_conn, {"form_factor": "laptop"}, "trend", {"metric": "battery"}
    )
    assert len(found.device_ids) == 9 and len(later.device_ids) == 3
    assert comp.outcome == "answer"
    assert [c.device_ids for c in comp.claims] == [later.device_ids]
    assert [[i.device_id for i in e.items] for e in comp.entity_lists] == [later.device_ids]
    assert all(e.list_id.startswith("trend") for e in comp.entity_lists)
    shown = {EvidenceRef.parse(e.ref).device_id for e in comp.evidence}
    assert shown == set(later.device_ids)
    assert comp.coverage.population == later.population == 9


def test_a_find_used_only_to_narrow_a_macos_check_keeps_the_macs_as_the_population(
    real_db_path, real_conn
):
    found, later, comp = _find_then(
        real_db_path, real_conn, {"os_name": "macOS", "group_by": "os"}, "macos_version_check",
        {"older_than": "15"},
    )  # fmt: skip
    assert comp.outcome == "answer"
    assert [c.device_ids for c in comp.claims] == [
        ["1LYSSFD074BB", "7VP16KHV88LM", "MT7PJB7N5LRE", "XSU9UUDGU6AC"]
    ]
    assert len(comp.entity_lists) == 1
    assert comp.coverage.population == 7
    assert not any("Populations differ" in n for n in comp.notes)
    assert comp.fallback_headline == "4 of 7 macOS devices run a version older than macOS 15"


def test_a_narrowed_call_with_no_findings_is_no_match_not_a_list_of_the_find(
    real_db_path, real_conn
):
    _, later, comp = _find_then(
        real_db_path, real_conn, {"form_factor": "laptop"}, "trend", {"metric": "battery"}, GLOBEX
    )
    assert later.findings == []
    assert comp.outcome == "no_match" and comp.claims == [] and comp.entity_lists == []
    assert comp.coverage.population == later.population


def test_a_find_next_to_a_call_without_device_ids_is_a_lookup_too(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("find_devices", {"form_factor": "laptop"}), ("low_disk_devices", {})],
    )
    assert len(comp.claims) == 1 and len(comp.entity_lists) == 1
    assert comp.entity_lists[0].list_id == "low_disk_devices-1"


def test_a_find_is_composed_when_the_later_find_used_other_devices(real_db_path, real_conn):
    found = run(real_db_path, real_conn, "find_devices", {"os_name": "macOS"})
    other = [d for d in ("8NM23J95R5I6", "9MXJVZ1I0DKA") if d not in found.device_ids]
    assert other  # Windows devices: not a subset of the Macs
    later = run(real_db_path, real_conn, "find_devices", {"device_ids": other})
    comp = compose([found, later], resolve=repo_for(real_conn).resolve)
    assert comp.claims[0].text.startswith("Matching devices")
    assert len(comp.claims) == 2


def test_a_find_used_to_narrow_a_later_find_adds_no_claim(real_db_path, real_conn):
    found = run(real_db_path, real_conn, "find_devices", {"os_name": "macOS"})
    later = run(real_db_path, real_conn, "find_devices", {"device_ids": found.device_ids[:2]})
    comp = compose([found, later], resolve=repo_for(real_conn).resolve)
    assert len(comp.claims) == 1 and comp.claims[0].device_ids == later.device_ids


def test_a_find_is_composed_when_the_narrowed_call_failed(real_db_path, real_conn):
    found = run(real_db_path, real_conn, "find_devices", {"form_factor": "laptop"})
    failed = ToolRecord(
        tool="trend",
        args={"metric": "battery", "device_ids": found.device_ids},
        error="not_found",
    )
    comp = compose([found, failed], resolve=repo_for(real_conn).resolve)
    assert comp.outcome == "answer" and comp.claims[0].text.startswith("Matching devices")


# ---- a sibling find_devices next to an analysis call is a lookup, whatever the call order --------


def _siblings(real_db_path, real_conn, tenant, find_args, tool, args):
    """find_devices and an analysis call made in the same round: neither used the other's devices."""
    records = [
        run(real_db_path, real_conn, "find_devices", find_args, tenant),
        run(real_db_path, real_conn, tool, args, tenant),
    ]
    return records, compose(records, resolve=repo_for(real_conn, tenant).resolve)


def test_a_sibling_find_next_to_an_empty_battery_trend_is_no_match_not_the_find(
    real_db_path, real_conn
):
    (found, trend), comp = _siblings(
        real_db_path, real_conn, GLOBEX, {"form_factor": "laptop"}, "trend", {"metric": "battery"}
    )
    assert len(found.device_ids) > 0 and trend.findings == []
    assert not set(trend.device_ids) & set(found.device_ids)
    assert comp.outcome == "no_match" and comp.reason_code == "no_matching_records"
    assert comp.claims == [] and comp.evidence == [] and comp.entity_lists == []
    assert comp.coverage.population == trend.population


def test_a_sibling_find_next_to_a_non_empty_trend_claims_only_the_trend_devices(
    real_db_path, real_conn
):
    (found, trend), comp = _siblings(
        real_db_path, real_conn, ACME, {"form_factor": "laptop"}, "trend", {"metric": "battery"}
    )
    assert trend.device_ids and set(trend.device_ids) < set(found.device_ids)
    assert comp.outcome == "answer"
    assert [c.device_ids for c in comp.claims] == [trend.device_ids]
    assert [e.list_id for e in comp.entity_lists] == ["trend-1-battery"]
    assert {EvidenceRef.parse(e.ref).device_id for e in comp.evidence} == set(trend.device_ids)
    assert comp.coverage.population == trend.population


def test_a_sibling_find_listed_after_the_analysis_call_is_a_lookup_too(real_db_path, real_conn):
    trend = run(real_db_path, real_conn, "trend", {"metric": "battery"}, GLOBEX)
    found = run(real_db_path, real_conn, "find_devices", {"form_factor": "laptop"}, GLOBEX)
    comp = compose([trend, found], resolve=repo_for(real_conn, GLOBEX).resolve)
    assert comp.outcome == "no_match" and comp.claims == [] and comp.entity_lists == []


def test_a_find_alone_still_answers_the_inventory_question(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, [("find_devices", {"group_by": "model"})])
    assert comp.outcome == "answer"
    assert comp.claims[0].text.startswith("Devices by model")
    assert [e.list_id for e in comp.entity_lists] == ["find_devices-1"]


def test_two_finds_alone_are_both_composed(real_db_path, real_conn):
    comp = composed(
        real_db_path,
        real_conn,
        [("find_devices", {"os_name": "macOS"}), ("find_devices", {"form_factor": "desktop"})],
    )
    assert len(comp.claims) == 2 and len(comp.entity_lists) == 2


def test_a_find_is_still_composed_when_every_analysis_call_failed(real_db_path, real_conn):
    found = run(real_db_path, real_conn, "find_devices", {"form_factor": "laptop"})
    failed = ToolRecord(tool="trend", args={"metric": "battery"}, error="not_found")
    comp = compose([found, failed], resolve=repo_for(real_conn).resolve)
    assert comp.outcome == "answer" and comp.claims[0].text.startswith("Matching devices")
