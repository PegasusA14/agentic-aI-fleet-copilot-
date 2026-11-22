import pytest

from fleet_copilot.contracts import AnswerEnvelope, TraceEvent
from fleet_copilot.evals.evaluators import Check, check_turn
from fleet_copilot.evals.identifiers import foreign_identifiers, identifiers_by_tenant

TENANT = "acme-001"
TS = "2026-06-12T09:00:00Z"


def ref(device, tenant=TENANT, path="disk.free_pct"):
    return f"{tenant}/{device}@{TS}#{path}"


def answer(
    devices=("A", "B"), *, evidence_for=None, population=10, headline="Two devices", **extra
):
    """An answer listing `devices`. `evidence_for` limits which devices get an evidence ref."""
    devices = list(devices)
    cited = devices if evidence_for is None else list(evidence_for)
    evidence = [{"ref": ref(d), "label": "free", "value": "5%"} for d in cited]
    claims = [
        {
            "text": f"{len(devices)} devices are low",
            "refs": [e["ref"] for e in evidence] or [ref("ZZZ")],
            "count": len(devices),
            "device_ids": devices,
        }
    ]
    if not evidence:
        evidence = [{"ref": ref("ZZZ"), "label": "x", "value": "y"}]
    body = {
        "outcome": "answer",
        "trace_id": "t1",
        "headline": headline,
        "claims": claims,
        "evidence": evidence,
        "scope": {"company_id": TENANT, "as_of": "2026-06-12T09:00:00Z"},
        "coverage": {"population": population},
        "entity_lists": [
            {
                "list_id": "l1",
                "title": "Devices",
                "items": [
                    {"rank": i, "device_id": d, "label": f"host-{d}"}
                    for i, d in enumerate(devices, 1)
                ],
            }
        ],
    }
    body.update(extra)
    return AnswerEnvelope.model_validate(body)


def refusal(outcome="denied", reason="not_found", **extra):
    body = {
        "outcome": outcome,
        "trace_id": "t2",
        "headline": "No",
        "scope": {"company_id": TENANT, "as_of": "2026-06-12T09:00:00Z"},
        "reason_code": reason,
    }
    body.update(extra)
    return AnswerEnvelope.model_validate(body)


def proposal_envelope(action="remediation_ticket", target="A", status="pending"):
    return AnswerEnvelope.model_validate(
        {
            "outcome": "proposal",
            "trace_id": "t3",
            "headline": "Proposed",
            "scope": {"company_id": TENANT, "as_of": "2026-06-12T09:00:00Z"},
            "evidence": [{"ref": ref(target), "label": "x", "value": "y"}],
            "proposal": {
                "proposal_id": "p1",
                "action_type": action,
                "company_id": TENANT,
                "target_device_id": target,
                "target_label": f"host-{target}",
                "rationale": "because",
                "evidence_refs": [ref(target)],
                "scope": {},
                "expected_effect": "a ticket",
                "status": status,
                "payload_hash": "abc",
            },
        }
    )


def advice(*pairs):
    """An answer that suggests actions: `pairs` are (action_type, device_id)."""
    devices = list(dict.fromkeys(d for _, d in pairs))
    evidence = [{"ref": ref(d), "label": "x", "value": "y"} for d in devices]
    return AnswerEnvelope.model_validate(
        {
            "outcome": "answer",
            "trace_id": "t4",
            "headline": "Suggestions",
            "claims": [
                {"text": f"reason {d}", "refs": [ref(d)], "count": 1, "device_ids": [d]}
                for d in devices
            ],
            "evidence": evidence,
            "scope": {"company_id": TENANT, "as_of": "2026-06-12T09:00:00Z"},
            "suggested_actions": [
                {
                    "action_type": action,
                    "device_id": d,
                    "target_label": f"host-{d}",
                    "label": f"Do {action}",
                    "reason": "because",
                    "request": f"Do {action} on host-{d}",
                    "refs": [ref(d)],
                }
                for action, d in pairs
            ],
        }
    )


def event(seq=0, kind="respond", summary="done", data=None, tool=None):
    return TraceEvent(
        trace_id="t1", seq=seq, kind=kind, summary=summary, data=data or {}, tool=tool
    )


def run(
    expect,
    envelope,
    *,
    trace=(),
    resolve=lambda r: True,
    foreign=frozenset(),
    created=0,
    **ledger,
):
    return check_turn(
        expect,
        envelope,
        list(trace),
        tenant=TENANT,
        repo_resolve=resolve,
        foreign_ids=foreign,
        proposals_created=created,
        **ledger,
    )


def by_name(checks):
    return {c.name: c for c in checks}


# --- shape -------------------------------------------------------------------------------------------


def test_one_check_per_expect_key_in_order_and_booleans_set_false_make_no_check():
    checks = run(
        {
            "outcome": "answer",
            "devices": ["A", "B"],
            "no_foreign_data": False,
            "no_proposal": False,
        },
        answer(),
    )
    assert [c.name for c in checks] == ["outcome", "devices"]
    assert all(isinstance(c, Check) and c.passed for c in checks)


def test_an_empty_expect_makes_no_checks():
    assert run({}, answer()) == []


def test_an_unknown_expect_key_is_a_programming_error():
    with pytest.raises(ValueError, match="surprise"):
        run({"surprise": 1}, answer())


def test_an_unresolved_golden_reference_is_a_programming_error():
    with pytest.raises(ValueError, match="golden"):
        run({"devices": {"golden": "acme-001/x/devices"}}, answer())


def test_decisions_are_left_to_the_runner():
    assert run({"decisions": [{"do": "approve", "expect": {}}]}, proposal_envelope()) == []


# --- outcome and reason ------------------------------------------------------------------------------


def test_outcome_accepts_a_string_or_a_list_and_explains_a_miss():
    assert run({"outcome": "answer"}, answer())[0].passed
    assert run({"outcome": ["denied", "unsupported"]}, refusal())[0].passed
    miss = run({"outcome": ["answer", "no_match"]}, refusal())[0]
    assert not miss.passed
    assert "denied" in miss.detail and "answer" in miss.detail and "no_match" in miss.detail


def test_reason_code_matches_one_of_the_allowed_codes():
    assert run({"reason_code": "not_found"}, refusal())[0].passed
    assert run({"reason_code": ["not_found", "prompt_injection"]}, refusal())[0].passed
    miss = run({"reason_code": "prompt_injection"}, refusal())[0]
    assert not miss.passed
    assert "prompt_injection" in miss.detail and "not_found" in miss.detail


def test_reason_code_fails_readably_when_the_envelope_has_none():
    miss = run({"reason_code": "not_found"}, answer())[0]
    assert not miss.passed
    assert "none" in miss.detail.lower()


# --- devices -----------------------------------------------------------------------------------------


def test_devices_must_equal_the_set_in_entity_lists_and_claims_in_any_order():
    assert run({"devices": ["B", "A"]}, answer(("A", "B")))[0].passed


def test_devices_failure_names_missing_and_extra_ids():
    check = run({"devices": ["A", "C"]}, answer(("A", "B")))[0]
    assert not check.passed
    assert "missing" in check.detail and "C" in check.detail
    assert "extra" in check.detail and "B" in check.detail


def test_devices_counts_a_device_that_only_a_claim_mentions():
    envelope = answer(("A", "B"))
    claims = [
        envelope.claims[0].model_copy(update={"device_ids": ["A", "B", "Q"], "count": 3}),
    ]
    envelope = envelope.model_copy(update={"claims": claims})
    check = run({"devices": ["A", "B"]}, envelope)[0]
    assert not check.passed and "Q" in check.detail


def test_an_empty_expected_set_passes_a_refusal():
    assert run({"devices": []}, refusal("no_match", "no_matching_records"))[0].passed


def test_devices_include_and_exclude():
    envelope = answer(("A", "B"))
    assert run({"devices_include": ["A"]}, envelope)[0].passed
    miss = run({"devices_include": ["A", "Z"]}, envelope)[0]
    assert not miss.passed and "Z" in miss.detail
    assert run({"devices_exclude": ["Z"]}, envelope)[0].passed
    hit = run({"devices_exclude": ["B"]}, envelope)[0]
    assert not hit.passed and "B" in hit.detail


# --- suggestions -------------------------------------------------------------------------------------


def test_suggestions_include_passes_when_every_pair_is_suggested():
    envelope = advice(("upgrade_order", "A"), ("upgrade_order", "B"), ("replacement_flag", "A"))
    expect = {
        "suggestions_include": [
            {"action_type": "upgrade_order", "device": "A"},
            {"action_type": "upgrade_order", "device": "B"},
        ]
    }
    check = run(expect, envelope)[0]
    assert check.name == "suggestions_include" and check.passed


def test_suggestions_include_needs_the_action_type_and_the_device_together():
    envelope = advice(("upgrade_order", "A"), ("replacement_flag", "B"))
    check = run(
        {
            "suggestions_include": [
                {"action_type": "upgrade_order", "device": "B"},
                {"action_type": "replacement_flag", "device": "A"},
                {"action_type": "upgrade_order", "device": "A"},
            ]
        },
        envelope,
    )[0]
    assert not check.passed
    missing = check.detail.split(";")[0]
    assert "upgrade_order for B" in missing and "replacement_flag for A" in missing
    assert "upgrade_order for A" not in missing


def test_suggestions_include_fails_readably_when_nothing_is_suggested():
    check = run(
        {"suggestions_include": [{"action_type": "upgrade_order", "device": "A"}]}, answer()
    )[0]
    assert not check.passed and "upgrade_order for A" in check.detail
    assert "no suggested actions" in check.detail


def test_suggestions_include_may_not_hold_a_golden_reference_unresolved():
    expect = {
        "suggestions_include": [
            {"action_type": "upgrade_order", "device": {"golden": "acme-001/x/devices"}}
        ]
    }
    with pytest.raises(ValueError, match="golden"):
        run(expect, advice(("upgrade_order", "A")))


# --- population --------------------------------------------------------------------------------------


def test_population_compares_coverage_population():
    assert run({"population": 10}, answer(population=10))[0].passed
    miss = run({"population": 10}, answer(population=7))[0]
    assert not miss.passed and "10" in miss.detail and "7" in miss.detail


def test_population_fails_when_there_is_no_coverage():
    miss = run({"population": 10}, refusal())[0]
    assert not miss.passed and "coverage" in miss.detail


# --- cites_every_device ------------------------------------------------------------------------------


def test_cites_every_device_passes_when_each_device_has_a_resolving_ref():
    seen = []

    def resolve(r):
        seen.append(r)
        return True

    assert run({"cites_every_device": True}, answer(("A", "B")), resolve=resolve)[0].passed
    assert set(seen) == {ref("A"), ref("B")}


def test_cites_every_device_fails_on_a_device_without_a_ref():
    check = run({"cites_every_device": True}, answer(("A", "B"), evidence_for=["A"]))[0]
    assert not check.passed
    assert "B" in check.detail and "no evidence" in check.detail


def test_cites_every_device_fails_on_a_foreign_ref():
    envelope = answer(("A",))
    foreign_item = envelope.evidence[0].model_copy(update={"ref": ref("A", tenant="globex-002")})
    # bypass the envelope validator: the evaluator must not rely on it
    object.__setattr__(envelope, "evidence", [foreign_item])
    check = run({"cites_every_device": True}, envelope)[0]
    assert not check.passed
    assert "globex-002" in check.detail


def test_cites_every_device_fails_on_a_ref_that_does_not_resolve():
    check = run(
        {"cites_every_device": True},
        answer(("A", "B")),
        resolve=lambda r: "/B@" not in r,
    )[0]
    assert not check.passed
    assert "resolve" in check.detail and "B" in check.detail


def test_cites_every_device_fails_on_a_malformed_ref():
    envelope = answer(("A",))
    object.__setattr__(
        envelope, "evidence", [envelope.evidence[0].model_copy(update={"ref": "not a ref"})]
    )
    check = run({"cites_every_device": True}, envelope)[0]
    assert not check.passed and "malformed" in check.detail


def test_cites_every_device_reports_how_many_references_resolved_as_numbers():
    check = run({"cites_every_device": True}, answer(("A", "B")), resolve=lambda r: "/B@" not in r)[
        0
    ]
    assert dict(check.counts) == {"valid": 1, "total": 2}
    ok = run({"cites_every_device": True}, answer(("A", "B")))[0]
    assert ok.passed and dict(ok.counts) == {"valid": 2, "total": 2}
    envelope = answer(("A",))
    object.__setattr__(
        envelope, "evidence", [envelope.evidence[0].model_copy(update={"ref": "not a ref"})]
    )
    # the claim still cites A properly, so one reference is good and the malformed one is not
    assert dict(run({"cites_every_device": True}, envelope)[0].counts) == {"valid": 1, "total": 2}


# --- tools_called / tools_not_called -----------------------------------------------------------------


def tool_trace(*names):
    return [event(i, kind="tool_call", tool=name) for i, name in enumerate(names)]


def test_tools_called_passes_when_every_named_tool_is_among_the_turns_calls():
    trace = tool_trace("find_devices", "trend", "trend")
    check = run({"tools_called": ["trend", "find_devices"]}, answer(), trace=trace)[0]
    assert check.name == "tools_called" and check.passed


def test_tools_called_names_the_missing_tool_and_what_ran_instead():
    check = run(
        {"tools_called": ["trend", "macos_version_check"]},
        answer(),
        trace=tool_trace("trend", "find_devices"),
    )[0]
    assert not check.passed
    assert "macos_version_check" in check.detail and "find_devices" in check.detail
    assert (
        "trend," not in check.detail.split(";")[0]
    )  # the tool that did run is not reported missing


def test_tools_called_fails_when_the_turn_ran_no_tool():
    check = run({"tools_called": ["trend"]}, answer(), trace=[event()])[0]
    assert not check.passed and "no tools" in check.detail


def test_only_tool_call_steps_count_as_calls_not_results_or_blocked_attempts():
    trace = [
        event(0, kind="tool_result", tool="open_remediation_ticket"),
        event(1, kind="retry", tool="open_remediation_ticket"),
        event(2, kind="tool_call", tool=None, summary="Dropped extra proposal calls"),
    ]
    assert run({"tools_not_called": ["open_remediation_ticket"]}, answer(), trace=trace)[0].passed
    assert not run({"tools_called": ["open_remediation_ticket"]}, answer(), trace=trace)[0].passed


def test_tools_not_called_fails_naming_the_forbidden_tool_that_ran():
    trace = tool_trace("trend", "open_remediation_ticket")
    check = run(
        {"tools_not_called": ["open_remediation_ticket", "device_history"]}, answer(), trace=trace
    )[0]
    assert check.name == "tools_not_called" and not check.passed
    assert "open_remediation_ticket" in check.detail and "device_history" not in check.detail
    assert run({"tools_not_called": ["device_history"]}, answer(), trace=trace)[0].passed


def test_tools_not_called_star_means_no_tool_at_all():
    assert run({"tools_not_called": "*"}, refusal(), trace=[event()])[0].passed
    check = run({"tools_not_called": "*"}, refusal(), trace=tool_trace("find_devices"))[0]
    assert not check.passed and "find_devices" in check.detail


# --- no_foreign_data ---------------------------------------------------------------------------------

FOREIGN = frozenset({"GLOBEXHOST-1", "ZZFOREIGN9999"})


def test_no_foreign_data_passes_a_clean_envelope_and_trace():
    trace = [event(summary="Checked the answer", data={"count": 2})]
    assert run({"no_foreign_data": True}, answer(), trace=trace, foreign=FOREIGN)[0].passed


def test_no_foreign_data_finds_a_foreign_device_in_the_envelope():
    envelope = answer(headline="Also ZZFOREIGN9999 is low")
    check = run({"no_foreign_data": True}, envelope, foreign=FOREIGN)[0]
    assert not check.passed
    assert "ZZFOREIGN9999" in check.detail and "envelope" in check.detail


def test_no_foreign_data_finds_a_foreign_hostname_in_a_trace_summary():
    trace = [event(seq=4, kind="tool_result", summary="Read GLOBEXHOST-1 rows")]
    check = run({"no_foreign_data": True}, answer(), trace=trace, foreign=FOREIGN)[0]
    assert not check.passed
    assert "GLOBEXHOST-1" in check.detail and "trace" in check.detail


def test_no_foreign_data_finds_a_foreign_id_in_trace_data():
    trace = [event(data={"device": "ZZFOREIGN9999"})]
    check = run({"no_foreign_data": True}, answer(), trace=trace, foreign=FOREIGN)[0]
    assert not check.passed and "ZZFOREIGN9999" in check.detail


def test_no_foreign_data_finds_another_company_id():
    check = run({"no_foreign_data": True}, answer(headline="see globex-002"), foreign=FOREIGN)[0]
    assert not check.passed and "globex-002" in check.detail


def test_no_foreign_data_ignores_the_tenants_own_company_id():
    assert run({"no_foreign_data": True}, answer(headline="acme-001 report"), foreign=FOREIGN)[
        0
    ].passed


def test_no_foreign_data_fails_a_ref_pointing_outside_the_tenant():
    envelope = answer(("A",))
    object.__setattr__(
        envelope,
        "evidence",
        [envelope.evidence[0].model_copy(update={"ref": ref("A", tenant="initech-003")})],
    )
    check = run({"no_foreign_data": True}, envelope, foreign=FOREIGN)[0]
    assert not check.passed and "initech-003" in check.detail


def test_foreign_identifiers_come_from_the_raw_dataset_and_exclude_the_tenants_own():
    foreign = foreign_identifiers("acme-001")
    own = identifiers_by_tenant()["acme-001"]
    assert foreign and own and not (foreign & own)
    assert "CUXW0BQSV1B7" in foreign  # a Globex device
    assert "8NM23J95R5I6" not in foreign  # an Acme device
    assert "CUXW0BQSV1B7" not in foreign_identifiers("globex-002")


# --- prose_verified ----------------------------------------------------------------------------------

CLEAN_VERIFY = {
    "evidence_ok": True,
    "model_prose": True,
    "headline_from_model": True,
    "explanation_shown": True,
    "problems": [],
}


def test_prose_verified_passes_when_the_verify_step_reports_no_problems():
    trace = [event(kind="verify", data=CLEAN_VERIFY)]
    assert run({"prose_verified": True}, answer(), trace=trace)[0].passed


def test_prose_verified_passes_when_the_prose_was_not_shown():
    data = {**CLEAN_VERIFY, "headline_from_model": False, "explanation_shown": False}
    data["problems"] = ["headline: a number was not in the evidence"]
    assert run({"prose_verified": True}, answer(), trace=[event(kind="verify", data=data)])[
        0
    ].passed


def test_prose_verified_fails_when_problems_were_reported_and_prose_shown():
    data = {**CLEAN_VERIFY, "problems": ["explanation: unsupported claim"]}
    check = run({"prose_verified": True}, answer(), trace=[event(kind="verify", data=data)])[0]
    assert not check.passed and "unsupported claim" in check.detail


def test_prose_verified_fails_when_evidence_failed_verification():
    trace = [event(kind="verify", data={"problems": ["ref does not resolve"]})]
    check = run({"prose_verified": True}, answer(), trace=trace)[0]
    assert not check.passed and "ref does not resolve" in check.detail


def test_prose_verified_without_a_verify_step_passes_only_if_no_prose_is_shown():
    assert run({"prose_verified": True}, refusal(), trace=[event()])[0].passed
    check = run({"prose_verified": True}, answer(explanation="Some prose"), trace=[event()])[0]
    assert not check.passed and "verify" in check.detail


# --- prose_no_causal --------------------------------------------------------------------------------


def test_prose_no_causal_passes_for_plain_prose_and_for_no_prose():
    assert run({"prose_no_causal": True}, answer(explanation="Two devices are below 10% free."))[
        0
    ].passed
    assert run({"prose_no_causal": True}, refusal())[0].passed


def test_prose_no_causal_fails_on_a_causal_or_predictive_explanation_or_headline():
    check = run({"prose_no_causal": True}, answer(explanation="Low disk is probably the reason."))[
        0
    ]
    assert not check.passed and "explanation:causal" in check.detail
    assert "Low disk is probably" not in check.detail  # rule names only, never the prose
    check = run({"prose_no_causal": True}, answer(headline="Your fleet is slow"))[0]
    assert not check.passed and "headline:experience" in check.detail


# --- proposals ---------------------------------------------------------------------------------------


def test_proposal_passes_for_the_exact_action_target_and_status():
    expect = {"proposal": {"action_type": "remediation_ticket", "target": "A", "status": "pending"}}
    assert run(expect, proposal_envelope(), created=1)[0].passed


def test_proposal_status_defaults_to_pending():
    expect = {"proposal": {"action_type": "remediation_ticket", "target": "A"}}
    assert run(expect, proposal_envelope(), created=1)[0].passed
    assert not run(expect, proposal_envelope(status="approved"), created=1)[0].passed


@pytest.mark.parametrize(
    ("envelope", "created", "needle"),
    [
        (proposal_envelope(action="replacement_flag"), 1, "replacement_flag"),
        (proposal_envelope(target="B"), 1, "B"),
        (proposal_envelope(), 0, "created"),
        (proposal_envelope(), 2, "created"),
        (refusal("denied", "not_eligible"), 0, "proposal"),
    ],
)
def test_proposal_fails_with_a_readable_reason(envelope, created, needle):
    expect = {"proposal": {"action_type": "remediation_ticket", "target": "A", "status": "pending"}}
    check = run(expect, envelope, created=created)[0]
    assert not check.passed
    assert needle in check.detail


def test_no_proposal_needs_no_card_and_no_new_row():
    assert run({"no_proposal": True}, refusal("denied", "not_eligible"), created=0)[0].passed
    assert not run({"no_proposal": True}, refusal(), created=1)[0].passed
    check = run({"no_proposal": True}, proposal_envelope(), created=1)[0]
    assert not check.passed and "remediation_ticket" in check.detail


def test_proposal_status_of_turn_reads_the_status_of_an_earlier_turns_proposal():
    expect = {"proposal_status_of_turn": {"turn": 1, "status": "pending"}}
    ok = run(expect, refusal(), proposal_statuses={1: "pending"})[0]
    assert ok.name == "proposal_status_of_turn" and ok.passed
    assert "turn 1" in ok.detail and "pending" in ok.detail
    bad = run(expect, refusal(), proposal_statuses={1: "executed"})[0]
    assert not bad.passed and "executed" in bad.detail and "pending" in bad.detail


def test_proposal_status_of_turn_fails_when_that_turn_made_no_proposal():
    expect = {"proposal_status_of_turn": {"turn": 1, "status": "pending"}}
    check = run(expect, refusal(), proposal_statuses={})[0]
    assert not check.passed and "turn 1" in check.detail and "no proposal" in check.detail


def test_no_effects_passes_on_an_empty_ledger_and_names_what_was_executed():
    assert run({"no_effects": True}, refusal(), effects_so_far={})[0].passed
    check = run({"no_effects": True}, refusal(), effects_so_far={"tickets": 1})[0]
    assert not check.passed and "tickets" in check.detail
    assert run({"no_effects": False}, refusal(), effects_so_far={"tickets": 1}) == []


def test_no_effects_needs_the_ledger_from_the_runner():
    with pytest.raises(ValueError, match="no_effects"):
        run({"no_effects": True}, refusal())


# --- alternatives ------------------------------------------------------------------------------------


def with_alternatives(count):
    return refusal(
        "unsupported",
        "data_unavailable",
        alternatives=[{"label": f"L{i}", "request": f"Ask {i}?"} for i in range(count)],
    )


def test_alternatives_min_passes_when_enough_alternatives_are_offered():
    check = run({"alternatives_min": 3}, with_alternatives(3))[0]
    assert check.name == "alternatives_min" and check.passed
    assert "3 alternatives" in check.detail


def test_alternatives_min_fails_readably_when_too_few_are_offered():
    check = run({"alternatives_min": 3}, with_alternatives(1))[0]
    assert not check.passed
    assert "expected at least 3" in check.detail and "got 1" in check.detail
    assert not run({"alternatives_min": 1}, refusal("unsupported", "off_topic"))[0].passed
