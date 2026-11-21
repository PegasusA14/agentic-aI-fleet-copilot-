/** How the audit trail reads: every event, action and refusal reason in plain words. Raw `detail` is never shown. */
import type { AuditEntry, AuditEventName } from "./transport";
import type { ProposalCard } from "./types";

export type AuditMarkName = "review" | "check" | "x" | "triangle" | "alert" | "lock" | "hollow";
export type AuditTone = "action" | "pass" | "neutral" | "attention" | "alert" | "denied";

export const AUDIT_EVENT: Record<AuditEventName, { word: string; tone: AuditTone; mark: AuditMarkName }> = {
  proposal_created: { word: "Proposed", tone: "action", mark: "review" },
  proposal_refused: { word: "Refused", tone: "denied", mark: "lock" },
  proposal_approved: { word: "Approved", tone: "action", mark: "check" },
  proposal_executed: { word: "Executed (simulated)", tone: "pass", mark: "check" },
  proposal_rejected: { word: "Rejected", tone: "neutral", mark: "x" },
  proposal_invalidated: { word: "Invalidated", tone: "denied", mark: "triangle" },
  proposal_failed: { word: "Failed", tone: "alert", mark: "alert" },
  approval_refused: { word: "Approval refused", tone: "denied", mark: "lock" },
  duplicate_approval: { word: "Duplicate approval", tone: "neutral", mark: "hollow" },
};

/** An event this build does not know yet still reads as a state, never as a bare code. */
export const UNKNOWN_EVENT = { word: "Other event", tone: "neutral", mark: "hollow" } as const;

export const ACTION_WORDS: Record<ProposalCard["action_type"], string> = {
  remediation_ticket: "Remediation ticket",
  replacement_flag: "Replacement flag",
  upgrade_order: "Upgrade order",
  employee_notification: "Employee notification",
};

/** The simulated effect tables the operator ledger counts, in the order they are listed. */
export const EFFECT_TABLES: Record<string, string> = {
  tickets: "Tickets",
  replacement_flags: "Replacement flags",
  upgrade_orders: "Upgrade orders",
  employee_notifications: "Employee notifications",
};

const REASONS: Record<string, string> = {
  not_eligible: "not supported by the evidence",
  insufficient_evidence: "not enough recent evidence",
  not_found: "not found",
  hash_mismatch: "proposal changed",
  forbidden: "not allowed",
  changed: "the data changed since it was proposed",
  recipient_changed: "the device was reassigned to another employee",
  already_done: "already done recently",
  too_many_pending: "too many proposals waiting",
};

const STATUSES = new Set(["pending", "approved", "executed", "rejected", "invalidated", "failed", "uncertain"]);

/** The reason an entry recorded, in words; null when it recorded none or one this build cannot word. */
export function reasonWords(detail: AuditEntry["detail"]): string | null {
  const reason = detail?.reason;
  if (typeof reason !== "string") return null;
  if (reason in REASONS) return REASONS[reason];
  const pending = /^not_pending:([a-z]+)$/.exec(reason);
  if (pending && STATUSES.has(pending[1])) return `already ${pending[1]}`;
  return null;
}

export function actionWords(action: string | null): string | null {
  if (!action) return null;
  return ACTION_WORDS[action as ProposalCard["action_type"]] ?? null;
}

export function eventStyle(event: string): { word: string; tone: AuditTone; mark: AuditMarkName } {
  return AUDIT_EVENT[event as AuditEventName] ?? UNKNOWN_EVENT;
}
