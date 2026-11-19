import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fleet_copilot.config import REPO_ROOT
from fleet_copilot.contracts import (
    AnswerEnvelope,
    Claim,
    Clarification,
    EvidenceItem,
    ProposalCard,
    ScopeInfo,
    TraceEvent,
)
from fleet_copilot.contracts_export import build_schema

SCOPE = ScopeInfo(
    company_id="acme-001",
    as_of=datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
    rule="free < 10%",
    population=10,
)
REF = "acme-001/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk"


def envelope(**overrides):
    base = {
        "outcome": "answer",
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
    assert envelope(outcome="no_match", claims=[], evidence=[]).outcome == "no_match"


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


@pytest.mark.parametrize("outcome", ["denied", "unsupported", "tool_error"])
def test_refusal_outcomes_carry_no_claims_or_evidence(outcome):
    assert envelope(outcome=outcome, claims=[], evidence=[]).outcome == outcome
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(outcome=outcome)
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(outcome=outcome, claims=[])
    with pytest.raises(ValidationError, match="must not carry claims or evidence"):
        envelope(outcome=outcome, evidence=[])


@pytest.mark.parametrize("outcome", ["no_match", "insufficient_data"])
def test_no_match_and_insufficient_data_may_carry_grounded_claims(outcome):
    assert envelope(outcome=outcome).claims[0].refs == [REF]


def test_unknown_fields_and_outcomes_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        envelope(surprise="x")
    with pytest.raises(ValidationError, match="Input should be"):
        envelope(outcome="maybe")


def test_trace_event_kinds_are_closed():
    assert TraceEvent(seq=1, kind="tool_call", summary="low_disk(threshold=10)").latency_ms is None
    with pytest.raises(ValidationError, match="Input should be"):
        TraceEvent(seq=1, kind="thinking", summary="x")


def test_committed_schema_matches_the_models():
    committed = json.loads((REPO_ROOT / "contracts" / "schema.json").read_text(encoding="utf-8"))
    assert committed == build_schema()
