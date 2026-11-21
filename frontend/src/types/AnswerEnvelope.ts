/* Generated from contracts/*.schema.json by scripts/gen-types.mjs. Do not edit. */

/**
 * @maxItems 4
 */
export type Alternatives =
  | []
  | [Alternative]
  | [Alternative, Alternative]
  | [Alternative, Alternative, Alternative]
  | [Alternative, Alternative, Alternative, Alternative];
export type Label = string;
export type Request = string;
export type Count = number | null;
export type DeviceIds = string[];
/**
 * @minItems 1
 */
export type Refs = [string, ...string[]];
export type Text = string;
export type Claims = Claim[];
export type Options = string[];
export type Question = string;
export type DeviceId = string;
export type Label1 = string;
export type Reason = "out_of_scope" | "unknown_version" | "insufficient_history" | "no_reading" | "no_readings";
export type ReasonText = string;
/**
 * @maxItems 100
 */
export type ExcludedDevices = ExcludedDevice[];
export type MissingLatest = string[];
export type Population = number;
export type Stale = string[];
/**
 * @minItems 1
 */
export type Items = [EntityRef, ...EntityRef[]];
export type DeviceId1 = string;
export type Label2 = string;
export type Rank = number;
export type ListId = string;
export type Title = string;
export type EntityLists = EntityList[];
export type Label3 = string;
export type Ref = string;
export type Value = string;
export type Evidence = EvidenceItem[];
export type Explanation = string | null;
/**
 * @maxItems 4
 */
export type FollowUps =
  | []
  | [Alternative]
  | [Alternative, Alternative]
  | [Alternative, Alternative, Alternative]
  | [Alternative, Alternative, Alternative, Alternative];
export type Headline = string;
export type Notes = string[];
export type Outcome =
  "answer" | "no_match" | "insufficient_data" | "clarification" | "proposal" | "denied" | "unsupported" | "tool_error";
export type ActionType = "remediation_ticket" | "replacement_flag" | "upgrade_order" | "employee_notification";
export type CompanyId = string;
/**
 * @minItems 1
 */
export type EvidenceRefs = [string, ...string[]];
export type ExpectedEffect = string;
export type PayloadHash = string;
export type ProposalId = string;
export type Rationale = string;
export type Status = "pending" | "approved" | "rejected" | "executed" | "failed" | "uncertain" | "invalidated";
export type TargetDeviceId = string;
export type TargetLabel = string;
export type ReasonCode =
  | (
      | "no_matching_records"
      | "insufficient_history"
      | "not_found"
      | "off_topic"
      | "unsupported_action"
      | "prompt_injection"
      | "tool_failure"
      | "step_limit"
      | "not_eligible"
      | "insufficient_evidence"
      | "already_done"
      | "too_many_pending"
      | "data_unavailable"
    )
  | null;
export type RepeatOf = string | null;
export type AsOf = string;
export type Calculation = string | null;
export type CompanyId1 = string;
export type Population1 = number | null;
export type Rule = string | null;
export type AsOf1 = string;
export type Basis = string;
export type Checked = number;
export type CompanyName = string;
export type Matched = number;
export type NotChecked = number;
export type Ok = number | null;
export type OkLabel = string;
export type ActionType1 = "remediation_ticket" | "replacement_flag" | "upgrade_order" | "employee_notification";
export type DeviceId2 = string;
export type Label4 = string;
export type Reason1 = string;
/**
 * @minItems 1
 */
export type Refs1 = [string, ...string[]];
export type Request1 = string;
export type TargetLabel1 = string;
export type SuggestedActions = SuggestedAction[];
export type Summary = string;
/**
 * @minItems 1
 * @maxItems 8
 */
export type Columns =
  | [Column]
  | [Column, Column]
  | [Column, Column, Column]
  | [Column, Column, Column, Column]
  | [Column, Column, Column, Column, Column]
  | [Column, Column, Column, Column, Column, Column]
  | [Column, Column, Column, Column, Column, Column, Column]
  | [Column, Column, Column, Column, Column, Column, Column, Column];
export type Key = string;
export type Kind = "device" | "text" | "number" | "percent" | "version" | "pill" | "date";
export type Label5 = string;
export type Direction = "asc" | "desc";
export type Key1 = string;
export type Display = string;
export type Tone = ("neutral" | "attention" | "ok") | null;
export type Value1 = string | number | null;
export type DeviceId3 = string;
export type Label6 = string;
/**
 * @minItems 1
 */
export type Refs2 = [string, ...string[]];
/**
 * @maxItems 50
 */
export type Rows = Row[];
export type Title1 = string;
export type TotalRows = number | null;
export type TraceId = string;

/**
 * The only shape the agent returns. The UI maps it to fixed components; the LLM never emits markup.
 */
export interface AnswerEnvelope {
  alternatives: Alternatives;
  claims: Claims;
  clarification: Clarification | null;
  coverage: Coverage | null;
  entity_lists: EntityLists;
  evidence: Evidence;
  explanation: Explanation;
  follow_ups: FollowUps;
  headline: Headline;
  notes: Notes;
  outcome: Outcome;
  proposal: ProposalCard | null;
  reason_code: ReasonCode;
  repeat_of: RepeatOf;
  scope: ScopeInfo;
  scope_line: ScopeLine | null;
  suggested_actions: SuggestedActions;
  summary: Summary;
  table: AnswerTable | null;
  trace_id: TraceId;
}
/**
 * A question the copilot can answer, offered after a refusal. Code-written; `request` is sent as the next message.
 */
export interface Alternative {
  label: Label;
  request: Request;
}
export interface Claim {
  count: Count;
  device_ids: DeviceIds;
  refs: Refs;
  text: Text;
}
export interface Clarification {
  options: Options;
  question: Question;
}
/**
 * What the answer covered and what it could not: the disclosures a reviewer needs.
 */
export interface Coverage {
  excluded: Excluded;
  excluded_devices: ExcludedDevices;
  missing_latest: MissingLatest;
  population: Population;
  stale: Stale;
}
export interface Excluded {
  [k: string]: string[];
}
/**
 * One device a check did not cover, with a code-written reason a person can read.
 */
export interface ExcludedDevice {
  device_id: DeviceId;
  label: Label1;
  reason: Reason;
  reason_text: ReasonText;
}
/**
 * An ordered list shown to the user, so 'the first one' can be resolved against it.
 */
export interface EntityList {
  items: Items;
  list_id: ListId;
  title: Title;
}
export interface EntityRef {
  device_id: DeviceId1;
  label: Label2;
  rank: Rank;
}
export interface EvidenceItem {
  label: Label3;
  ref: Ref;
  value: Value;
}
export interface ProposalCard {
  action_type: ActionType;
  company_id: CompanyId;
  evidence_refs: EvidenceRefs;
  expected_effect: ExpectedEffect;
  payload_hash: PayloadHash;
  proposal_id: ProposalId;
  rationale: Rationale;
  scope: Scope;
  status: Status;
  target_device_id: TargetDeviceId;
  target_label: TargetLabel;
}
export interface Scope {
  [k: string]: string;
}
export interface ScopeInfo {
  as_of: AsOf;
  calculation: Calculation;
  company_id: CompanyId1;
  population: Population1;
  rule: Rule;
}
/**
 * The counts behind the footer line: what was checked, what matched, what was left out.
 */
export interface ScopeLine {
  as_of: AsOf1;
  basis: Basis;
  checked: Checked;
  company_name: CompanyName;
  matched: Matched;
  not_checked: NotChecked;
  ok: Ok;
  ok_label: OkLabel;
}
/**
 * One action the agent recommends. It is advice only: acting is a separate, approved proposal.
 *
 * `request` is the natural-language message the UI sends when the user picks the suggestion.
 */
export interface SuggestedAction {
  action_type: ActionType1;
  device_id: DeviceId2;
  label: Label4;
  reason: Reason1;
  refs: Refs1;
  request: Request1;
  target_label: TargetLabel1;
}
/**
 * A list answer as a table. Every cell is code-written from tool records; the rows are already sorted.
 */
export interface AnswerTable {
  columns: Columns;
  default_sort: SortSpec;
  rows: Rows;
  title: Title1;
  total_rows: TotalRows;
}
export interface Column {
  key: Key;
  kind: Kind;
  label: Label5;
}
export interface SortSpec {
  direction: Direction;
  key: Key1;
}
/**
 * One device (or one device and finding) in a table. `refs` are its sources, all in `evidence`.
 */
export interface Row {
  cells: Cells;
  device_id: DeviceId3;
  label: Label6;
  refs: Refs2;
}
export interface Cells {
  [k: string]: Cell;
}
/**
 * One value in a table row: `display` is what is shown, `value` is what a sort compares.
 */
export interface Cell {
  display: Display;
  tone: Tone;
  value: Value1;
}
