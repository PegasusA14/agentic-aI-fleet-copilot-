/** How a proposal reads: the action, the facts it rests on, its state and the effect table in plain words. */
import type { ProposalCard } from "./types";

type ActionType = ProposalCard["action_type"];
type Status = ProposalCard["status"];

/** The sentence a person approves: what will be done, to which device. */
export function actionSentence(card: Pick<ProposalCard, "action_type" | "target_label">): string {
  const device = card.target_label;
  switch (card.action_type) {
    case "remediation_ticket":
      return `Open a remediation ticket for ${device}`;
    case "replacement_flag":
      return `Flag ${device} for replacement`;
    case "upgrade_order":
      return `Order a hardware upgrade for ${device}`;
    case "employee_notification":
      return `Notify the assigned employee about ${device}`;
  }
}

/** The simulated table each action writes to, in words. */
export const EFFECT_TABLE_WORDS: Record<ActionType, string> = {
  remediation_ticket: "Tickets",
  replacement_flag: "Replacement flags",
  upgrade_order: "Upgrade orders",
  employee_notification: "Employee notifications",
};

const SCOPE_LABELS: Record<string, string> = {
  device: "Device",
  check_id: "Check",
  severity: "Severity",
  component: "Component",
  current: "Current",
  target: "Target",
  topic: "Topic",
  employee: "Employee",
  reason: "Reason",
  message: "Message",
};

function humanise(key: string): string {
  const spaced = key.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export interface ScopeFact {
  key: string;
  label: string;
  value: string;
  /** Device and employee ids are tokens: set in the mono face. */
  token: boolean;
}

export function scopeFacts(scope: Record<string, string>): ScopeFact[] {
  return Object.entries(scope)
    .filter(([, value]) => value)
    .map(([key, value]) => ({
      key,
      label: SCOPE_LABELS[key] ?? humanise(key),
      value,
      token: key === "device" || key === "employee",
    }));
}

export function shortHash(hash: string): string {
  return hash.slice(0, 12);
}

export type StatusTone = "action" | "pass" | "neutral" | "attention" | "alert";
export type StatusMarkName = "review" | "check" | "x" | "triangle" | "alert" | "hollow";

export const STATUS_STYLE: Record<Status, { word: string; tone: StatusTone; mark: StatusMarkName }> = {
  pending: { word: "Pending", tone: "action", mark: "review" },
  approved: { word: "Approved", tone: "action", mark: "check" },
  executed: { word: "Executed (simulated)", tone: "pass", mark: "check" },
  rejected: { word: "Rejected", tone: "neutral", mark: "x" },
  invalidated: { word: "Invalidated", tone: "attention", mark: "triangle" },
  failed: { word: "Failed", tone: "alert", mark: "alert" },
  uncertain: { word: "Outcome uncertain", tone: "attention", mark: "triangle" },
};
