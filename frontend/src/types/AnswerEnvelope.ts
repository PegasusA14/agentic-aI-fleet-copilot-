/* Generated from contracts/*.schema.json by scripts/gen-types.mjs. Do not edit. */

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
export type MissingLatest = string[];
export type Population = number;
export type Stale = string[];
/**
 * @minItems 1
 */
export type Items = [EntityRef, ...EntityRef[]];
export type DeviceId = string;
export type Label = string;
export type Rank = number;
export type ListId = string;
export type Title = string;
export type EntityLists = EntityList[];
export type Label1 = string;
export type Ref = string;
export type Value = string;
export type Evidence = EvidenceItem[];
export type Explanation = string | null;
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
    )
  | null;
export type AsOf = string;
export type Calculation = string | null;
export type CompanyId1 = string;
export type Population1 = number | null;
export type Rule = string | null;
export type TraceId = string;

/**
 * The only shape the agent returns. The UI maps it to fixed components; the LLM never emits markup.
 */
export interface AnswerEnvelope {
  claims: Claims;
  clarification: Clarification | null;
  coverage: Coverage | null;
  entity_lists: EntityLists;
  evidence: Evidence;
  explanation: Explanation;
  headline: Headline;
  notes: Notes;
  outcome: Outcome;
  proposal: ProposalCard | null;
  reason_code: ReasonCode;
  scope: ScopeInfo;
  trace_id: TraceId;
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
  missing_latest: MissingLatest;
  population: Population;
  stale: Stale;
}
export interface Excluded {
  [k: string]: string[];
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
  device_id: DeviceId;
  label: Label;
  rank: Rank;
}
export interface EvidenceItem {
  label: Label1;
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
