import json
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from fleet_copilot.config import REPO_ROOT
from fleet_copilot.contracts import (
    ActionType,
    Alternative,
    AnswerEnvelope,
    Claim,
    Clarification,
    Coverage,
    EntityList,
    EntityRef,
    EvidenceItem,
    ExcludedReason,
    ProposalCard,
    ScopeInfo,
    SuggestedAction,
    TraceEvent,
    TraceKind,
)
from fleet_copilot.contracts_export import SCHEMA_DIR, build_schema

SCOPE = ScopeInfo(
    company_id="acme-001",
    as_of=datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
    rule="free < 10%",
    population=10,
)
REF = "acme-001/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk"
FOREIGN_REF = "globex-002/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk"


def envelope(**overrides):
    base = {
        "outcome": "answer",
        "trace_id": "t-1",
        "headline": "2 of 10 devices are low on disk",
        "claims": [Claim(text="MT7PJB7N5LRE has 2.0% free", refs=[REF])],
        "evidence": [EvidenceItem(ref=REF, label="disk", value="11 GB of 512 GiB free")],
        "scope": SCOPE,
    }
    return AnswerEnvelope(**{**base, **overrides})


def test_a_grounded_answer_validates():
    assert envelope().outcome == "answer"


def test_a_claim_citing_unlisted_evidence_is_rejected():
    with pytest.raises(ValidationError, match="without evidence"):
        envelope(evidence=[])


def test_an_answer_needs_at_least_one_claim():
    with pytest.raises(ValidationError, match="at least one claim"):
        envelope(claims=[], evidence=[])


def test_no_match_may_have_zero_claims():
    ok = envelope(outcome="no_match", claims=[], evidence=[], reason_code="no_matching_records")
    assert ok.outcome == "no_match"


def test_clarification_requires_its_payload_and_only_it():
    with pytest.raises(ValidationError, match="requires a clarification"):
        envelope(outcome="clarification", claims=[], evidence=[])
    ok = envelope(
        outcome="clarification",
        claims=[],
        evidence=[],
        clarification=Clarification(
            question="Which list did you mean?", options=["disk", "screen lock"]
        ),
    )
    assert ok.clarification.options == ["disk", "screen lock"]
    with pytest.raises(ValidationError, match="only a clarification"):
        envelope(clarification=Clarification(question="?"))


def card(**overrides):
    base = {
        "proposal_id": "p-1",
        "action_type": "remediation_ticket",
        "company_id": "acme-001",
        "target_device_id": "1LYSSFD074BB",
        "target_label": "acme-macbook-2",
        "rationale": "screen_lock failing since 2026-05-22",
        "evidence_refs": [REF],
        "scope": {"check_id": "screen_lock"},
        "expected_effect": "Opens one simulated ticket",
        "status": "pending",
        "payload_hash": "abc123",
    }
    return ProposalCard(**{**base, **overrides})


def test_proposal_requires_its_payload_and_only_it():
    proposal = card()
    with pytest.raises(ValidationError, match="requires a proposal"):
        envelope(outcome="proposal", claims=[], evidence=[])
    assert envelope(outcome="proposal", claims=[], proposal=proposal).proposal.status == "pending"
    with pytest.raises(ValidationError, match="only a proposal"):
        envelope(proposal=proposal)


def test_a_proposal_for_another_company_is_rejected():
    with pytest.raises(ValidationError, match="company_id must match"):
        envelope(outcome="proposal", claims=[], proposal=card(company_id="globex-002"))


def test_a_proposal_citing_unlisted_evidence_is_rejected():
    with pytest.raises(ValidationError, match="proposal cites refs without evidence"):
        envelope(outcome="proposal", claims=[], evidence=[], proposal=card())
    other = "acme-001/1LYSSFD074BB@2026-06-12T08:39:00Z#screen_lock"
    with pytest.raises(ValidationError, match="proposal cites refs without evidence"):
        envelope(outcome="proposal", claims=[], proposal=card(evidence_refs=[REF, other]))


def test_a_proposal_with_matching_company_and_evidence_is_valid():
    ok = envelope(outcome="proposal", claims=[], proposal=card())
    assert ok.proposal.company_id == ok.scope.company_id
    assert ok.proposal.evidence_refs == [REF]


def test_a_proposal_must_cite_at_least_one_ref():
    with pytest.raises(ValidationError, match="at least 1 item"):
        card(evidence_refs=[])


def test_every_claim_must_cite_at_least_one_ref():
    with pytest.raises(ValidationError, match="Field required"):
        Claim(text="c")
    with pytest.raises(ValidationError, match="at least 1 item"):
        Claim(text="c", refs=[])
    with pytest.raises(ValidationError, match="at least 1 item"):
        envelope(claims=[{"text": "c", "refs": []}])


VALID_REASON = {
    "denied": "not_found",
    "unsupported": "off_topic",
    "tool_error": "tool_failure",
    "insufficient_data": "insufficient_history",
    "no_match": "no_matching_records",
}


@pytest.mark.parametrize("outcome", ["denied", "unsupported", "tool_error"])
def test_refusal_outcomes_carry_no_claims_or_evidence(outcome):
    refusal = {"outcome": outcome, "reason_code": VALID_REASON[outcome]}
    assert envelope(**refusal, claims=[], evidence=[]).outcome == outcome
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(**refusal)
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(**refusal, claims=[])
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(**refusal, evidence=[])


@pytest.mark.parametrize("outcome", ["no_match", "insufficient_data"])
def test_no_match_and_insufficient_data_may_carry_grounded_claims(outcome):
    assert envelope(outcome=outcome, reason_code=VALID_REASON[outcome]).claims[0].refs == [REF]


def test_unknown_fields_and_outcomes_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        envelope(surprise="x")
    with pytest.raises(ValidationError, match="Input should be"):
        envelope(outcome="maybe")


def evidence_only(*refs):
    """An envelope that carries only evidence, so the evidence rules are the ones that can fire."""
    return envelope(
        outcome="no_match",
        reason_code="no_matching_records",
        claims=[],
        evidence=[EvidenceItem(ref=r, label="disk", value="v") for r in refs],
    )


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-ref",
        "",
        "acme-001/MT7PJB7N5LRE@2026-06-12#disk",  # timestamp is not a full UTC instant
        "acme-001/MT7PJB7N5LRE@2026-06-12T08:39:00Z",  # no path
        "acme-001@2026-06-12T08:39:00Z#disk",  # no device
    ],
)
def test_a_malformed_evidence_ref_is_rejected(bad):
    with pytest.raises(ValidationError, match=re.escape(f"evidence ref {bad!r} is malformed")):
        evidence_only(bad)


def test_an_evidence_ref_from_another_company_is_rejected():
    with pytest.raises(
        ValidationError, match=re.escape(f"evidence ref {FOREIGN_REF!r} belongs to another company")
    ):
        evidence_only(REF, FOREIGN_REF)


def test_a_claim_citing_another_companys_ref_is_rejected():
    foreign_claim = [Claim(text="x", refs=[FOREIGN_REF])]
    # listed as evidence too, so the only thing wrong with it is the tenant
    both = [EvidenceItem(ref=r, label="disk", value="v") for r in (REF, FOREIGN_REF)]
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(claims=foreign_claim, evidence=both)
    # not listed as evidence: the claim itself is what is refused, not just the missing evidence
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(claims=foreign_claim)


def test_a_malformed_claim_ref_is_rejected_before_the_missing_evidence_rule():
    with pytest.raises(ValidationError, match=re.escape("evidence ref 'junk' is malformed")):
        envelope(claims=[Claim(text="x", refs=["junk"])])


def test_a_proposal_citing_another_companys_ref_is_rejected():
    both = [EvidenceItem(ref=r, label="disk", value="v") for r in (REF, FOREIGN_REF)]
    foreign_card = card(evidence_refs=[FOREIGN_REF])
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(outcome="proposal", claims=[], evidence=both, proposal=foreign_card)
    # not listed as evidence: the proposal's own ref is what is refused
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(outcome="proposal", claims=[], proposal=foreign_card)


def test_a_malformed_proposal_ref_is_rejected():
    with pytest.raises(ValidationError, match=re.escape("evidence ref 'junk' is malformed")):
        envelope(outcome="proposal", claims=[], proposal=card(evidence_refs=["junk"]))


def test_duplicate_evidence_refs_are_rejected():
    with pytest.raises(ValidationError, match="duplicate evidence refs"):
        evidence_only(REF, REF)
    other = "acme-001/MT7PJB7N5LRE@2026-06-11T08:39:00Z#disk"
    assert len(evidence_only(REF, other).evidence) == 2


def test_scope_as_of_must_carry_a_time_zone():
    naive = datetime(2026, 6, 12, 11, 0)
    with pytest.raises(ValidationError, match="timezone"):
        ScopeInfo(company_id="acme-001", as_of=naive)
    with pytest.raises(ValidationError, match="timezone"):
        ScopeInfo.model_validate({"company_id": "acme-001", "as_of": "2026-06-12T11:00:00"})
    plus_two = timezone(timedelta(hours=2))
    assert ScopeInfo(company_id="acme-001", as_of=naive.replace(tzinfo=plus_two)).as_of.utcoffset()
    assert ScopeInfo.model_validate(
        {"company_id": "a", "as_of": "2026-06-12T11:00:00Z"}
    ).as_of.tzinfo


def test_trace_event_kinds_are_closed():
    event = TraceEvent(trace_id="t-1", seq=1, kind="tool_call", summary="low_disk(threshold=10)")
    assert event.latency_ms is None and event.tool is None
    with pytest.raises(ValidationError, match="Input should be"):
        TraceEvent(trace_id="t-1", seq=1, kind="thinking", summary="x")


def test_a_trace_id_is_required():
    with pytest.raises(ValidationError, match="trace_id"):
        AnswerEnvelope(
            outcome="answer",
            headline="h",
            claims=[Claim(text="c", refs=[REF])],
            evidence=[EvidenceItem(ref=REF, label="l", value="v")],
            scope=SCOPE,
        )


@pytest.mark.parametrize(
    "outcome", ["denied", "unsupported", "tool_error", "insufficient_data", "no_match"]
)
def test_refusals_and_empty_results_need_a_reason_code(outcome):
    with pytest.raises(ValidationError, match="requires a reason_code"):
        envelope(outcome=outcome, claims=[], evidence=[])
    ok = envelope(outcome=outcome, claims=[], evidence=[], reason_code=VALID_REASON[outcome])
    assert ok.reason_code == VALID_REASON[outcome]


@pytest.mark.parametrize("outcome", ["answer", "clarification"])
def test_other_outcomes_must_not_carry_a_reason_code(outcome):
    extra = (
        {"clarification": Clarification(question="Which?")} if outcome == "clarification" else {}
    )
    claims = [] if outcome == "clarification" else [Claim(text="c", refs=[REF])]
    evidence = [] if outcome == "clarification" else [EvidenceItem(ref=REF, label="l", value="v")]
    with pytest.raises(ValidationError, match="must not carry a reason_code"):
        envelope(
            outcome=outcome, claims=claims, evidence=evidence, reason_code="not_found", **extra
        )


@pytest.mark.parametrize("outcome", ["denied", "unsupported", "tool_error"])
def test_refusals_carry_no_explanation(outcome):
    with pytest.raises(ValidationError, match="must not carry an explanation"):
        envelope(
            outcome=outcome,
            claims=[],
            evidence=[],
            reason_code=VALID_REASON[outcome],
            explanation="free text",
        )


def test_claim_count_must_match_its_device_ids():
    assert Claim(text="c", refs=[REF], count=2, device_ids=["A", "B"]).count == 2
    with pytest.raises(ValidationError, match="count must equal"):
        Claim(text="c", refs=[REF], count=3, device_ids=["A", "B"])


def test_a_claim_count_needs_device_ids():
    with pytest.raises(ValidationError, match="claim count requires device_ids"):
        Claim(text="c", refs=[REF], count=3)
    with pytest.raises(ValidationError, match="claim count requires device_ids"):
        Claim(text="c", refs=[REF], count=0, device_ids=[])
    assert Claim(text="c", refs=[REF]).count is None
    assert Claim(text="c", refs=[REF], device_ids=["A"]).count is None


def test_a_claim_may_not_repeat_a_device():
    with pytest.raises(ValidationError, match="duplicate device_ids in a claim"):
        Claim(text="c", refs=[REF], device_ids=["A", "B", "A"])
    with pytest.raises(ValidationError, match="duplicate device_ids in a claim"):
        Claim(text="c", refs=[REF], count=2, device_ids=["A", "A"])
    assert Claim(text="c", refs=[REF], count=2, device_ids=["A", "B"]).count == 2


def one_entity_list(*device_ids):
    items = [EntityRef(rank=i, device_id=d, label=f"host-{d}") for i, d in enumerate(device_ids, 1)]
    return EntityList(list_id="l-1", title="t", items=items)


def test_every_entity_list_device_must_appear_in_some_claim():
    claim = Claim(text="c", refs=[REF], count=2, device_ids=["A", "B"])
    ok = envelope(claims=[claim], entity_lists=[one_entity_list("A", "B")])
    assert ok.entity_lists[0].items[1].device_id == "B"
    message = "entity list references a device that no claim mentions"
    with pytest.raises(ValidationError, match=message):
        envelope(claims=[claim], entity_lists=[one_entity_list("A", "C")])
    with pytest.raises(ValidationError, match=message):
        envelope(entity_lists=[one_entity_list("A")])  # the claim names no devices at all
    with pytest.raises(ValidationError, match=message):
        envelope(
            outcome="no_match",
            reason_code="no_matching_records",
            claims=[],
            evidence=[],
            entity_lists=[one_entity_list("A")],
        )


def test_an_entity_device_may_be_mentioned_by_any_one_of_several_claims():
    claims = [
        Claim(text="c1", refs=[REF], count=1, device_ids=["A"]),
        Claim(text="c2", refs=[REF], count=1, device_ids=["B"]),
    ]
    assert envelope(claims=claims, entity_lists=[one_entity_list("A", "B")]).entity_lists


ALLOWED_PAIRS = {
    "denied": [
        "not_found",
        "prompt_injection",
        "not_eligible",
        "insufficient_evidence",
        "other_company",
    ],
    "unsupported": ["off_topic", "unsupported_action", "data_unavailable"],
    "tool_error": ["tool_failure"],
    "insufficient_data": ["insufficient_history", "step_limit"],
    "no_match": ["no_matching_records"],
}
ALL_REASONS = [
    "no_matching_records",
    "insufficient_history",
    "not_found",
    "off_topic",
    "unsupported_action",
    "prompt_injection",
    "tool_failure",
    "step_limit",
    "not_eligible",
    "insufficient_evidence",
    "data_unavailable",
    "other_company",
]


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [(o, r) for o, reasons in ALLOWED_PAIRS.items() for r in reasons],
)
def test_each_allowed_outcome_and_reason_pair_validates(outcome, reason):
    assert (
        envelope(outcome=outcome, claims=[], evidence=[], reason_code=reason).reason_code == reason
    )


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [(o, r) for o, reasons in ALLOWED_PAIRS.items() for r in ALL_REASONS if r not in reasons],
)
def test_every_other_outcome_and_reason_pair_is_rejected(outcome, reason):
    message = f"reason_code {reason} is not valid for a {outcome} outcome"
    with pytest.raises(ValidationError, match=re.escape(message)):
        envelope(outcome=outcome, claims=[], evidence=[], reason_code=reason)


def test_a_meaningless_pair_names_both_sides():
    with pytest.raises(
        ValidationError,
        match=re.escape("reason_code no_matching_records is not valid for a denied outcome"),
    ):
        envelope(outcome="denied", claims=[], evidence=[], reason_code="no_matching_records")


def test_entity_lists_are_ordered_and_unique():
    items = [
        EntityRef(rank=1, device_id="A", label="host-a"),
        EntityRef(rank=2, device_id="B", label="host-b"),
    ]
    assert EntityList(list_id="low-disk-1", title="Low disk", items=items).items[1].rank == 2
    with pytest.raises(ValidationError, match="ranks must run"):
        EntityList(list_id="x", title="t", items=[EntityRef(rank=2, device_id="A", label="a")])
    with pytest.raises(ValidationError, match="duplicate device"):
        EntityList(
            list_id="x",
            title="t",
            items=[
                EntityRef(rank=1, device_id="A", label="a"),
                EntityRef(rank=2, device_id="A", label="a"),
            ],
        )


def test_duplicate_entity_list_ids_are_rejected():
    one = EntityList(list_id="same", title="t", items=[EntityRef(rank=1, device_id="A", label="a")])
    claim = Claim(text="c", refs=[REF], count=1, device_ids=["A"])
    with pytest.raises(ValidationError, match="duplicate entity list ids"):
        envelope(claims=[claim], entity_lists=[one, one])


def test_coverage_is_structured():
    cov = Coverage(population=10, stale=["A"], excluded={"out_of_scope": ["W1", "W2"]})
    assert envelope(coverage=cov).coverage.excluded["out_of_scope"] == ["W1", "W2"]
    with pytest.raises(ValidationError):
        Coverage(population=-1)


def test_schema_files_are_per_model_required_complete_and_committed():
    schemas = build_schema()
    assert set(schemas) == {"AnswerEnvelope", "TraceEvent"}
    envelope_schema = schemas["AnswerEnvelope"]
    # serialization mode + defaults-required: arrays and nullable fields are always present
    for field in (
        "claims",
        "evidence",
        "notes",
        "entity_lists",
        "suggested_actions",
        "coverage",
        "reason_code",
        "explanation",
        "trace_id",
    ):
        assert field in envelope_schema["required"], field
    assert not (SCHEMA_DIR / "schema.json").exists()
    for name, schema in schemas.items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        text = path.read_text(encoding="utf-8")
        assert json.loads(text) == schema
        assert text.endswith("\n") and text == json.dumps(schema, indent=2, sort_keys=True) + "\n"
    assert SCHEMA_DIR == REPO_ROOT / "contracts"


# ---- Plan 2B amendments: trace events and the closed excluded-reason set ----------------------------


def test_every_graph_stage_has_a_trace_kind():
    stages = {
        "scope",
        "plan",
        "reference",
        "tool_call",
        "tool_result",
        "retry",
        "compose",
        "explain",
        "verify",
        "respond",
        "memory",
        "error",
    }
    assert stages == set(get_args(TraceKind))


def test_a_trace_event_names_its_turn_and_may_name_a_tool():
    event = TraceEvent(
        trace_id="t-9", seq=3, kind="tool_result", tool="trend", summary="trend returned"
    )
    assert (event.trace_id, event.tool, event.seq) == ("t-9", "trend", 3)
    with pytest.raises(ValidationError, match="trace_id"):
        TraceEvent(seq=1, kind="plan", summary="x")
    with pytest.raises(ValidationError, match="trace_id"):
        TraceEvent(trace_id="", seq=1, kind="plan", summary="x")
    with pytest.raises(ValidationError):
        TraceEvent(trace_id="t", seq=-1, kind="plan", summary="x")
    with pytest.raises(ValidationError):
        TraceEvent(trace_id="t", seq=1, kind="plan", summary="x", latency_ms=-5)


def test_the_trace_schema_requires_every_field():
    schema = build_schema()["TraceEvent"]
    assert set(schema["required"]) == {
        "trace_id",
        "seq",
        "kind",
        "tool",
        "summary",
        "data",
        "latency_ms",
    }


def test_excluded_reasons_are_a_closed_set():
    keys = set(get_args(ExcludedReason))
    assert keys == {
        "out_of_scope",
        "unknown_version",
        "insufficient_history",
        "no_reading",
        "no_readings",
    }
    assert Coverage(population=1, excluded={"out_of_scope": ["A"]}).excluded == {
        "out_of_scope": ["A"]
    }
    with pytest.raises(ValidationError):
        Coverage(population=1, excluded={"made_up_reason": ["A"]})


def test_action_type_covers_the_four_actions():
    assert set(get_args(ActionType)) == {
        "remediation_ticket",
        "replacement_flag",
        "upgrade_order",
        "employee_notification",
    }


@pytest.mark.parametrize("reason", ["not_eligible", "insufficient_evidence"])
def test_action_refusal_reasons_are_valid_only_on_denied(reason):
    denied = envelope(outcome="denied", claims=[], evidence=[], reason_code=reason, notes=["why"])
    assert denied.reason_code == reason and denied.notes == ["why"]
    with pytest.raises(ValidationError, match="must not carry a reason_code"):
        envelope(reason_code=reason)


# ---- Plan 7: suggested actions ----------------------------------------------------------------------


def suggestion(**overrides):
    base = {
        "action_type": "upgrade_order",
        "device_id": "MT7PJB7N5LRE",
        "target_label": "acme-thinkpad-8",
        "label": "Order a storage upgrade (512 GiB -> 1 TiB)",
        "reason": "Free disk stayed below 10% for 7 days",
        "request": "Order a storage upgrade for acme-thinkpad-8",
        "refs": [REF],
    }
    return SuggestedAction(**{**base, **overrides})


def test_suggested_actions_default_to_none():
    assert envelope().suggested_actions == []


def test_a_grounded_suggested_action_validates():
    ok = envelope(suggested_actions=[suggestion()])
    assert ok.suggested_actions[0].request == "Order a storage upgrade for acme-thinkpad-8"
    assert ok.model_dump()["suggested_actions"][0]["refs"] == [REF]


def test_a_suggestion_must_cite_at_least_one_ref():
    with pytest.raises(ValidationError, match="at least 1 item"):
        suggestion(refs=[])


def test_a_suggestion_citing_unlisted_evidence_is_rejected():
    other = "acme-001/MT7PJB7N5LRE@2026-06-11T08:39:00Z#disk"
    with pytest.raises(ValidationError, match="suggested action cites refs without evidence"):
        envelope(suggested_actions=[suggestion(refs=[REF, other])])
    with pytest.raises(ValidationError, match="suggested action cites refs without evidence"):
        envelope(
            evidence=[],
            claims=[],
            outcome="no_match",
            reason_code="no_matching_records",
            suggested_actions=[suggestion()],
        )


def test_a_suggestion_citing_another_companys_ref_is_rejected():
    both = [EvidenceItem(ref=r, label="disk", value="v") for r in (REF, FOREIGN_REF)]
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(evidence=both, suggested_actions=[suggestion(refs=[FOREIGN_REF])])
    # not listed as evidence: the tenant is what is refused, not just the missing evidence
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(suggested_actions=[suggestion(refs=[FOREIGN_REF])])


def test_a_malformed_suggestion_ref_is_rejected():
    with pytest.raises(ValidationError, match=re.escape("evidence ref 'junk' is malformed")):
        envelope(suggested_actions=[suggestion(refs=["junk"])])


def test_a_suggestion_names_one_of_the_four_actions_and_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        suggestion(action_type="reboot_device")
    with pytest.raises(ValidationError):
        SuggestedAction(**{**suggestion().model_dump(), "surprise": 1})
    for action in get_args(ActionType):
        assert suggestion(action_type=action).action_type == action


@pytest.mark.parametrize("outcome", ["denied", "unsupported", "tool_error"])
def test_refusal_outcomes_carry_no_suggestions(outcome):
    reason = {"denied": "not_found", "unsupported": "off_topic", "tool_error": "tool_failure"}
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(
            outcome=outcome,
            claims=[],
            evidence=[],
            reason_code=reason[outcome],
            suggested_actions=[suggestion()],
        )


def unsupported(**overrides):
    base = {
        "outcome": "unsupported",
        "trace_id": "t-1",
        "headline": "Not recorded.",
        "scope": SCOPE,
        "reason_code": "data_unavailable",
    }
    return AnswerEnvelope(**{**base, **overrides})


def test_alternatives_default_to_empty_and_accept_up_to_four():
    assert unsupported().alternatives == []
    four = [Alternative(label=f"L{i}", request=f"Request {i}?") for i in range(4)]
    assert len(unsupported(alternatives=four).alternatives) == 4


def test_a_fifth_alternative_is_rejected():
    five = [Alternative(label=f"L{i}", request=f"Request {i}?") for i in range(5)]
    with pytest.raises(ValidationError):
        unsupported(alternatives=five)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"label": "", "request": "Ask?"},
        {"label": "Fine", "request": ""},
        {"label": "x" * 61, "request": "Ask?"},
        {"label": "Fine", "request": "x" * 161},
        {"label": "Fine", "request": "Ask?", "extra": 1},
    ],
)
def test_an_alternative_needs_a_short_label_and_a_short_request(kwargs):
    with pytest.raises(ValidationError):
        Alternative(**kwargs)


def test_data_unavailable_is_valid_for_unsupported_only():
    assert unsupported().reason_code == "data_unavailable"
    with pytest.raises(ValidationError, match="not valid"):
        envelope(outcome="denied", claims=[], evidence=[], reason_code="data_unavailable")
