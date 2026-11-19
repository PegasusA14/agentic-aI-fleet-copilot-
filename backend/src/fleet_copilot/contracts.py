from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fleet_copilot.evidence import EvidenceRef

OutcomeType = Literal[
    "answer",
    "no_match",
    "insufficient_data",
    "clarification",
    "proposal",
    "denied",
    "unsupported",
    "tool_error",
]
ActionType = Literal["remediation_ticket", "replacement_flag"]
ProposalStatus = Literal[
    "pending", "approved", "rejected", "executed", "failed", "uncertain", "invalidated"
]
TraceKind = Literal["plan", "tool_call", "tool_result", "verify", "respond", "error"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(_Model):
    ref: str
    label: str
    value: str


class Claim(_Model):
    text: str
    refs: list[str] = Field(min_length=1)


class ScopeInfo(_Model):
    company_id: str
    as_of: AwareDatetime
    rule: str | None = None
    population: int | None = None
    calculation: str | None = None


class Clarification(_Model):
    question: str
    options: list[str] = Field(default_factory=list)


class ProposalCard(_Model):
    proposal_id: str
    action_type: ActionType
    company_id: str
    target_device_id: str
    target_label: str
    rationale: str
    evidence_refs: list[str] = Field(min_length=1)
    scope: dict[str, str]
    expected_effect: str
    status: ProposalStatus
    payload_hash: str


class AnswerEnvelope(_Model):
    """The only shape the agent returns. The UI maps it to fixed components; the LLM never emits markup."""

    outcome: OutcomeType
    headline: str
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    scope: ScopeInfo
    clarification: Clarification | None = None
    proposal: ProposalCard | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_payloads(self) -> AnswerEnvelope:
        if self.outcome == "clarification" and self.clarification is None:
            raise ValueError("clarification outcome requires a clarification")
        if self.outcome != "clarification" and self.clarification is not None:
            raise ValueError("only a clarification outcome may carry a clarification")
        if self.outcome == "proposal" and self.proposal is None:
            raise ValueError("proposal outcome requires a proposal")
        if self.outcome != "proposal" and self.proposal is not None:
            raise ValueError("only a proposal outcome may carry a proposal")
        if self.outcome in ("denied", "unsupported", "tool_error") and (
            self.claims or self.evidence
        ):
            raise ValueError(f"{self.outcome} outcome must not carry claims or evidence")
        if self.outcome == "answer" and not self.claims:
            raise ValueError("answer outcome requires at least one claim")
        for item in self.evidence:
            self._check_ref(item.ref)
        if len({item.ref for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence refs")
        for claim in self.claims:
            for ref in claim.refs:
                self._check_ref(ref)
        if self.proposal is not None:
            for ref in self.proposal.evidence_refs:
                self._check_ref(ref)
        known = {item.ref for item in self.evidence}
        for claim in self.claims:
            missing = [r for r in claim.refs if r not in known]
            if missing:
                raise ValueError(f"claim cites refs without evidence: {missing}")
        if self.proposal is not None:
            if self.proposal.company_id != self.scope.company_id:
                raise ValueError("proposal company_id must match scope company_id")
            missing = [r for r in self.proposal.evidence_refs if r not in known]
            if missing:
                raise ValueError(f"proposal cites refs without evidence: {missing}")
        return self

    def _check_ref(self, ref: str) -> None:
        """A reference must follow the evidence grammar and point into this envelope's own tenant."""
        try:
            parsed = EvidenceRef.parse(ref)
        except ValueError:
            raise ValueError(f"evidence ref {ref!r} is malformed") from None
        if parsed.company_id != self.scope.company_id:
            raise ValueError(f"evidence ref {ref!r} belongs to another company")


class TraceEvent(_Model):
    seq: int
    kind: TraceKind
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
