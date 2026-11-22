from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

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
    "chat",
]
ActionType = Literal[
    "remediation_ticket", "replacement_flag", "upgrade_order", "employee_notification"
]
ProposalStatus = Literal[
    "pending", "approved", "rejected", "executed", "failed", "uncertain", "invalidated"
]
TraceKind = Literal[
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
]
ReasonCode = Literal[
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
    "already_done",
    "too_many_pending",
    "data_unavailable",
    "other_company",
]
# The only keys a query may put in `excluded` (and so the only keys `Coverage.excluded` accepts).
ExcludedReason = Literal[
    "out_of_scope",
    "unknown_version",
    "insufficient_history",
    "no_reading",
    "no_readings",
]
_REASON_REQUIRED = {"denied", "unsupported", "tool_error", "insufficient_data", "no_match"}
_VALID_REASONS: dict[str, frozenset[str]] = {
    "denied": frozenset(
        {
            "not_found",
            "prompt_injection",
            "not_eligible",
            "insufficient_evidence",
            "already_done",
            "too_many_pending",
            "other_company",
        }
    ),
    "unsupported": frozenset({"off_topic", "unsupported_action", "data_unavailable"}),
    "tool_error": frozenset({"tool_failure"}),
    "insufficient_data": frozenset({"insufficient_history", "step_limit"}),
    "no_match": frozenset({"no_matching_records"}),
    "chat": frozenset(),  # a chat reply has no reason
}
_NO_FREE_TEXT = {"denied", "unsupported", "tool_error"}
# Outcomes that carry no claims, evidence or list presentation. A chat reply is words only.
_NO_CLAIMS = _NO_FREE_TEXT | {"chat"}
# The refusals a conversational reply may frame: code declined for lack of evidence, or the data holds no
# such thing. Every other refusal (a missing record, another company, an injected instruction) is fixed text.
REPLY_REASONS = frozenset(
    {"not_eligible", "insufficient_evidence", "already_done", "data_unavailable"}
)
MAX_REPLY_CHARS = 1500
ColumnKind = Literal["device", "text", "number", "percent", "version", "pill", "date"]
Tone = Literal["neutral", "attention", "ok"]
MAX_TABLE_ROWS = 50
MAX_EXCLUDED_DEVICES = 100
MAX_FOLLOW_UPS = 4
MAX_SUMMARY_CHARS = 200
MAX_INSIGHTS = 10
MAX_INSIGHT_TEXT_CHARS = 240
MAX_SERIES_POINTS = 200
InsightCategory = Literal["battery", "disk", "memory", "compliance"]
InsightSeverity = Literal["act_soon", "watch", "ok"]
ProjectionStatus = Literal["projected", "none", "beyond_horizon"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class ExcludedDevice(_Model):
    """One device a check did not cover, with a code-written reason a person can read."""

    device_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    reason: ExcludedReason
    reason_text: str = Field(min_length=1, max_length=160)


class Coverage(_Model):
    """What the answer covered and what it could not: the disclosures a reader needs to judge it."""

    population: int = Field(ge=0)
    stale: list[str] = Field(default_factory=list)
    missing_latest: list[str] = Field(default_factory=list)
    excluded: dict[ExcludedReason, list[str]] = Field(default_factory=dict)
    # The same devices as `excluded`, one entry each, with hostname and reason in words.
    excluded_devices: list[ExcludedDevice] = Field(
        default_factory=list, max_length=MAX_EXCLUDED_DEVICES
    )

    @model_validator(mode="after")
    def _excluded_devices_are_excluded(self) -> Coverage:
        for item in self.excluded_devices:
            if item.device_id not in self.excluded.get(item.reason, []):
                raise ValueError("excluded_devices lists a device that `excluded` does not")
        return self


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


MAX_CHOICES = 6
MAX_NEEDS = 4
MAX_NEED_CHARS = 120


class Alternative(_Model):
    """A question the copilot can answer, offered after a refusal. Code-written; `request` is sent as the next message."""

    label: str = Field(min_length=1, max_length=60)
    request: str = Field(min_length=1, max_length=160)


class Clarification(_Model):
    question: str
    # The labels of `choices`, kept so a reader that only knows plain options still works.
    options: list[str] = Field(default_factory=list)
    # Code-built answers to the question: each carries the full request to send, not just its label.
    choices: list[Alternative] = Field(default_factory=list, max_length=MAX_CHOICES)

    @model_validator(mode="after")
    def _options_are_the_choice_labels(self) -> Clarification:
        if self.choices and self.options != [choice.label for choice in self.choices]:
            raise ValueError("options must be the labels of choices, in order")
        return self


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
    # Code-written (see `actions.service._risk_note`): who is affected and that the effect is simulated and
    # audited. Not part of the payload hash. Defaulted so a card stored before this field existed still loads.
    risk: str = Field(default="", max_length=200)
    status: ProposalStatus
    payload_hash: str


class SuggestedAction(_Model):
    """One action the agent recommends. It is advice only: acting is a separate, approved proposal.

    `request` is the natural-language message the UI sends when the user picks the suggestion.
    """

    action_type: ActionType
    device_id: str = Field(min_length=1)
    target_label: str = Field(min_length=1)
    label: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    request: str = Field(min_length=1)
    refs: list[str] = Field(min_length=1)


class Column(_Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=40)
    kind: ColumnKind


class Cell(_Model):
    """One value in a table row: `display` is what is shown, `value` is what a sort compares."""

    display: str = Field(max_length=120)
    value: str | float | int | None = None
    tone: Tone | None = None


class Row(_Model):
    """One device (or one device and finding) in a table. `refs` are its sources, all in `evidence`."""

    device_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    cells: dict[str, Cell]
    refs: list[str] = Field(min_length=1)


class SortSpec(_Model):
    key: str
    direction: Literal["asc", "desc"]


class AnswerTable(_Model):
    """A list answer as a table. Every cell is code-written from tool records; the rows are already sorted."""

    title: str = Field(min_length=1, max_length=120)
    columns: list[Column] = Field(min_length=1, max_length=8)
    rows: list[Row] = Field(max_length=MAX_TABLE_ROWS)
    # How many rows there were before the cap, when it cut any.
    total_rows: int | None = Field(default=None, ge=0)
    default_sort: SortSpec

    @model_validator(mode="after")
    def _consistent(self) -> AnswerTable:
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate column keys")
        if self.default_sort.key not in keys:
            raise ValueError("default_sort names a column that is not in the table")
        for row in self.rows:
            if not set(row.cells) <= set(keys):
                raise ValueError("a row has a cell for a column that is not in the table")
        if self.total_rows is not None and self.total_rows < len(self.rows):
            raise ValueError("total_rows is smaller than the rows shown")
        return self


class ScopeLine(_Model):
    """The counts behind the footer line: what was checked, what matched, what was left out."""

    checked: int = Field(ge=0)
    matched: int = Field(ge=0)
    # Checked devices that did not match, when every checked device has a known status; else None.
    ok: int | None = Field(default=None, ge=0)
    ok_label: str = Field(
        default="", max_length=40
    )  # what `ok` counts: "up to date", "healthy", ...
    not_checked: int = Field(ge=0)
    company_name: str = Field(default="", max_length=120)
    basis: str = Field(min_length=1, max_length=120)  # e.g. "latest snapshot per device"
    as_of: AwareDatetime

    @model_validator(mode="after")
    def _counts(self) -> ScopeLine:
        if self.matched > self.checked:
            raise ValueError("matched cannot exceed checked")
        if self.ok is not None and (self.ok + self.matched > self.checked or not self.ok_label):
            raise ValueError("ok must fit within the unmatched checked devices and be labelled")
        return self


class KeyValue(_Model):
    label: str = Field(min_length=1, max_length=60)
    value: str = Field(max_length=120)


class SeriesPoint(_Model):
    at: AwareDatetime
    value: float


class SeriesThreshold(_Model):
    """A line drawn on a series. `source` says whose number it is: ours (policy), never the vendor's."""

    value: float
    label: str = Field(min_length=1, max_length=80)
    source: Literal["policy"]


class InsightSeries(_Model):
    unit: str = Field(min_length=1, max_length=80)
    points: list[SeriesPoint] = Field(max_length=MAX_SERIES_POINTS)
    threshold: SeriesThreshold | None = None


class InsightFit(_Model):
    """A straight-line fit over the series: slope per day, how much of the scatter it explains, points used."""

    slope_per_day: float
    r2: float = Field(ge=0, le=1)
    n: int = Field(ge=2)


class InsightProjection(_Model):
    """An ESTIMATE of when the series reaches the threshold. `none`: not downward or too few points."""

    estimate: date | None = None
    earliest: date | None = None
    latest: date | None = None  # None with `earliest` set: the trend gives no upper bound
    status: ProjectionStatus

    @model_validator(mode="after")
    def _consistent(self) -> InsightProjection:
        if self.status == "projected":
            if self.estimate is None or self.earliest is None:
                raise ValueError("a projected estimate needs an estimate and an earliest date")
            if self.earliest > self.estimate or (
                self.latest is not None and self.latest < self.estimate
            ):
                raise ValueError("the range must contain the estimate")
        elif self.estimate or self.earliest or self.latest:
            raise ValueError("only a projected estimate carries dates")
        return self


class Insight(_Model):
    """One finding about one device, written by code: what it is, how bad, the numbers behind it, how sure."""

    device_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: InsightCategory
    severity: InsightSeverity
    finding: str = Field(min_length=1, max_length=MAX_INSIGHT_TEXT_CHARS)
    evidence_kv: list[KeyValue] = Field(default_factory=list, max_length=8)
    series: InsightSeries
    fit: InsightFit | None = None
    projection: InsightProjection | None = None
    # How the finding was reached, in a few words: "Estimate · straight-line fit · 30 snapshots".
    method: str = Field(min_length=1, max_length=120)
    why: str = Field(min_length=1, max_length=MAX_INSIGHT_TEXT_CHARS)
    refs: list[str] = Field(min_length=1, max_length=8)
    suggested_action_types: list[ActionType] = Field(default_factory=list, max_length=4)


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
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    reason_code: ReasonCode | None = None
    explanation: str | None = None
    # The verified conversational text the model wrote for this turn. Empty when there is none (a fixed
    # refusal, or a reply that did not pass the checks). `explanation` carries the same text where allowed.
    reply: str = Field(default="", max_length=MAX_REPLY_CHARS)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=4)
    # What would make a refused action possible, one line each, written by code from the eligibility rules.
    needs: list[Annotated[str, Field(min_length=1, max_length=MAX_NEED_CHARS)]] = Field(
        default_factory=list, max_length=MAX_NEEDS
    )
    # True only on the `other_company` refusal: the UI may offer to switch to a different company.
    offer_switch_company: bool = False
    # Presentation of a list answer. All code-written and optional, so older envelopes still validate.
    table: AnswerTable | None = None
    summary: str = Field(default="", max_length=MAX_SUMMARY_CHARS)
    scope_line: ScopeLine | None = None
    follow_ups: list[Alternative] = Field(default_factory=list, max_length=MAX_FOLLOW_UPS)
    # trace_id of the earlier turn in this conversation that gave this same answer.
    repeat_of: str | None = None
    # Ranked, code-written findings per device (an answer outcome only), at most MAX_INSIGHTS.
    insights: list[Insight] = Field(default_factory=list, max_length=MAX_INSIGHTS)
    # How many notes this conversation holds after the turn (things the user asked to be remembered).
    notes_count: int = Field(default=0, ge=0)

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
        if self.outcome in _NO_CLAIMS and (self.claims or self.evidence or self.suggested_actions):
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
        for suggestion in self.suggested_actions:
            for ref in suggestion.refs:
                self._check_ref(ref)
        if self.table is not None:
            for row in self.table.rows:
                for ref in row.refs:
                    self._check_ref(ref)
                    if EvidenceRef.parse(ref).device_id != row.device_id:
                        raise ValueError("a table row cites a reading of another device")
        known = {item.ref for item in self.evidence}
        for claim in self.claims:
            missing = [r for r in claim.refs if r not in known]
            if missing:
                raise ValueError(f"claim cites refs without evidence: {missing}")
        for suggestion in self.suggested_actions:
            missing = [r for r in suggestion.refs if r not in known]
            if missing:
                raise ValueError(f"suggested action cites refs without evidence: {missing}")
        if self.table is not None:
            if self.outcome != "answer":
                raise ValueError("only an answer outcome may carry a table")
            for row in self.table.rows:
                missing = [r for r in row.refs if r not in known]
                if missing:
                    raise ValueError(f"table row cites refs without evidence: {missing}")
        if self.outcome in _NO_CLAIMS and (
            self.summary or self.scope_line or self.follow_ups or self.repeat_of
        ):
            raise ValueError(f"{self.outcome} outcome must not carry list-answer presentation")
        if self.insights and self.outcome != "answer":
            raise ValueError("only an answer outcome may carry insights")
        for insight in self.insights:
            for ref in insight.refs:
                self._check_ref(ref)
                if EvidenceRef.parse(ref).device_id != insight.device_id:
                    raise ValueError("an insight cites a reading of another device")
            missing = [r for r in insight.refs if r not in known]
            if missing:
                raise ValueError(f"insight cites refs without evidence: {missing}")
        if self.repeat_of is not None and self.outcome != "answer":
            raise ValueError("only an answer outcome may repeat an earlier answer")
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
        if self.reason_code is not None and allowed is not None and self.reason_code not in allowed:
            raise ValueError(
                f"reason_code {self.reason_code} is not valid for a {self.outcome} outcome"
            )
        if self.offer_switch_company and self.reason_code != "other_company":
            raise ValueError("only an other_company refusal may offer to switch company")
        if self.outcome in _NO_FREE_TEXT and self.explanation is not None:
            raise ValueError(f"{self.outcome} outcome must not carry an explanation")
        if self.reply and not self._may_reply():
            raise ValueError(f"a {self.outcome} outcome with this reason must not carry a reply")
        list_ids = [entity_list.list_id for entity_list in self.entity_lists]
        if len(list_ids) != len(set(list_ids)):
            raise ValueError("duplicate entity list ids")
        mentioned = {device_id for claim in self.claims for device_id in claim.device_ids}
        for entity_list in self.entity_lists:
            if any(item.device_id not in mentioned for item in entity_list.items):
                raise ValueError("entity list references a device that no claim mentions")
        return self

    def _may_reply(self) -> bool:
        """A reply is for answers, proposals, chat and the code-declined refusals; never fixed security text."""
        if self.outcome in ("denied", "unsupported"):
            return self.reason_code in REPLY_REASONS
        return self.outcome in ("answer", "proposal", "chat")

    def _check_ref(self, ref: str) -> None:
        """A reference must follow the evidence grammar and point into this envelope's own tenant."""
        try:
            parsed = EvidenceRef.parse(ref)
        except ValueError:
            raise ValueError(f"evidence ref {ref!r} is malformed") from None
        if parsed.company_id != self.scope.company_id:
            raise ValueError(f"evidence ref {ref!r} belongs to another company")


class TraceEvent(_Model):
    """One step of a turn, streamed to the UI and stored for the observability console.

    `data` is open but MUST already be redacted: counts, tool names and redacted arguments only.
    Never raw telemetry values, employee ids or exception text.
    """

    trace_id: str = Field(min_length=1)
    seq: int = Field(ge=0)
    kind: TraceKind
    tool: str | None = None
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = Field(default=None, ge=0)
