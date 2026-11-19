from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    refs: list[str] = Field(default_factory=list)


class ScopeInfo(_Model):
    company_id: str
    as_of: datetime
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
    evidence_refs: list[str]
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
        if self.outcome == "answer" and not self.claims:
            raise ValueError("answer outcome requires at least one claim")
        known = {item.ref for item in self.evidence}
        for claim in self.claims:
            missing = [r for r in claim.refs if r not in known]
            if missing:
                raise ValueError(f"claim cites refs without evidence: {missing}")
        return self


class TraceEvent(_Model):
    seq: int
    kind: TraceKind
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
