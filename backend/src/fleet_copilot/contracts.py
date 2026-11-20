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
ReasonCode = Literal[
    "no_matching_records",
    "insufficient_history",
    "not_found",
    "off_topic",
    "unsupported_action",
    "prompt_injection",
    "tool_failure",
    "step_limit",
]
_REASON_REQUIRED = {"denied", "unsupported", "tool_error", "insufficient_data", "no_match"}
_VALID_REASONS: dict[str, frozenset[str]] = {
    "denied": frozenset({"not_found", "prompt_injection"}),
    "unsupported": frozenset({"off_topic", "unsupported_action"}),
    "tool_error": frozenset({"tool_failure"}),
    "insufficient_data": frozenset({"insufficient_history", "step_limit"}),
    "no_match": frozenset({"no_matching_records"}),
}
_NO_FREE_TEXT = {"denied", "unsupported", "tool_error"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Coverage(_Model):
    """What the answer covered and what it could not: the disclosures a reviewer needs."""

    population: int = Field(ge=0)
    stale: list[str] = Field(default_factory=list)
    missing_latest: list[str] = Field(default_factory=list)
    excluded: dict[str, list[str]] = Field(default_factory=dict)


class EntityRef(_Model):
    rank: int = Field(ge=1)
    device_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class EntityList(_Model):
    """An ordered list shown to the user, so 'the first one' can be resolved against it."""

    list_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    items: list[EntityRef] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered_and_unique(self) -> EntityList:
        if [item.rank for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("entity ranks must run 1..n in order")
        ids = [item.device_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate device in an entity list")
        return self


class EvidenceItem(_Model):
    ref: str
    label: str
    value: str


class Claim(_Model):
    text: str
    refs: list[str] = Field(min_length=1)
    count: int | None = Field(default=None, ge=0)
    device_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _count_matches_devices(self) -> Claim:
        if self.count is not None and not self.device_ids:
            raise ValueError("claim count requires device_ids")
        if len(self.device_ids) != len(set(self.device_ids)):
            raise ValueError("duplicate device_ids in a claim")
        if self.count is not None and self.count != len(self.device_ids):
            raise ValueError("claim count must equal the number of device_ids")
        return self


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
    trace_id: str = Field(min_length=1)
    headline: str
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    scope: ScopeInfo
    clarification: Clarification | None = None
    proposal: ProposalCard | None = None
    notes: list[str] = Field(default_factory=list)
    coverage: Coverage | None = None
    entity_lists: list[EntityList] = Field(default_factory=list)
    reason_code: ReasonCode | None = None
    explanation: str | None = None

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

    @model_validator(mode="after")
    def _disclosure_rules(self) -> AnswerEnvelope:
        needs_reason = self.outcome in _REASON_REQUIRED
        if needs_reason and self.reason_code is None:
            raise ValueError(f"{self.outcome} outcome requires a reason_code")
        if not needs_reason and self.reason_code is not None:
            raise ValueError(f"{self.outcome} outcome must not carry a reason_code")
        allowed = _VALID_REASONS.get(self.outcome)
        if allowed is not None and self.reason_code not in allowed:
            raise ValueError(
                f"reason_code {self.reason_code} is not valid for a {self.outcome} outcome"
            )
        if self.outcome in _NO_FREE_TEXT and self.explanation is not None:
            raise ValueError(f"{self.outcome} outcome must not carry an explanation")
        list_ids = [entity_list.list_id for entity_list in self.entity_lists]
        if len(list_ids) != len(set(list_ids)):
            raise ValueError("duplicate entity list ids")
        mentioned = {device_id for claim in self.claims for device_id in claim.device_ids}
        for entity_list in self.entity_lists:
            if any(item.device_id not in mentioned for item in entity_list.items):
                raise ValueError("entity list references a device that no claim mentions")
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
