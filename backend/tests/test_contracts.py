import json
import re
from datetime import UTC, datetime, timedelta, timezone

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
FOREIGN_REF = "globex-002/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk"


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


def evidence_only(*refs):
    """An envelope that carries only evidence, so the evidence rules are the ones that can fire."""
    return envelope(
        outcome="no_match",
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
    assert ScopeInfo.model_validate({"company_id": "a", "as_of": "2026-06-12T11:00:00Z"}).as_of.tzinfo


def test_trace_event_kinds_are_closed():
    assert TraceEvent(seq=1, kind="tool_call", summary="low_disk(threshold=10)").latency_ms is None
    with pytest.raises(ValidationError, match="Input should be"):
        TraceEvent(seq=1, kind="thinking", summary="x")


def test_committed_schema_matches_the_models():
    committed = json.loads((REPO_ROOT / "contracts" / "schema.json").read_text(encoding="utf-8"))
    assert committed == build_schema()
