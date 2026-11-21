/** How a proposal reads: the action, the facts it rests on, its state and the effect table in plain words. */
import { ACTION_WORDS } from "./audit";
import type { ProposalCard } from "./types";

type ActionType = ProposalCard["action_type"];
type Status = ProposalCard["status"];

type ActionCard = Pick<ProposalCard, "action_type" | "target_label"> & { scope?: ProposalCard["scope"] };

/** What an upgrade is for, in a word: the component the proposal names, or "hardware" only when it names none. */
function upgradeKind(card: ActionCard): string {
  const component = card.scope?.component?.trim().toLowerCase();
  return component ? component.replaceAll("_", " ") : "hardware";
}

/** The sentence a person approves: what will be done, to which device. The same words in the card, the dialog and Approvals. */
export function actionSentence(card: ActionCard): string {
  const device = card.target_label;
  switch (card.action_type) {
    case "remediation_ticket":
      return `Open a remediation ticket for ${device}`;
    case "replacement_flag":
      return `Flag ${device} for replacement`;
    case "upgrade_order":
      return `Order a ${upgradeKind(card)} upgrade for ${device}`;
    case "employee_notification":
      return `Notify the assigned employee about ${device}`;
  }
}

/** The thing that was made, as a noun phrase: "Storage upgrade order". */
function actionNoun(card: ActionCard): string {
  if (card.action_type !== "upgrade_order") return ACTION_WORDS[card.action_type];
  const kind = upgradeKind(card);
  return `${kind.charAt(0).toUpperCase()}${kind.slice(1)} upgrade order`;
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

/** Codes the server writes into a proposal's scope, in the words a person reads. */
const VALUE_WORDS: Record<string, Record<string, string>> = {
  check_id: {
    screen_lock: "Screen lock",
    disk_encryption: "Disk encryption",
    os_up_to_date: "OS up to date",
  },
  topic: {
    low_disk: "Low disk",
    memory_pressure: "Memory pressure",
    failing_check: "Failing check",
    battery_service: "Battery service",
  },
  component: {
    memory: "Memory",
    storage: "Storage",
  },
};

function displayValue(key: string, value: string): string {
  const words = VALUE_WORDS[key];
  if (!words) return value;
  return words[value] ?? humanise(value);
}

/**
 * The facts a proposal rests on, in words. The device is left out when the card already names it
 * (`target`), and Current and Target read as one change: "512 GiB → 1 TiB".
 */
export function scopeFacts(scope: Record<string, string>, target?: string): ScopeFact[] {
  const facts: ScopeFact[] = [];
  const change = scope.current && scope.target ? `${scope.current} → ${scope.target}` : null;
  for (const [key, value] of Object.entries(scope)) {
    if (!value) continue;
    if (key === "device" && value === target) continue;
    if (change && (key === "current" || key === "target")) {
      if (key === "current") facts.push({ key: "change", label: "Change", value: change, token: false });
      continue;
    }
    facts.push({
      key,
      label: SCOPE_LABELS[key] ?? humanise(key),
      value: displayValue(key, value),
      token: key === "device" || key === "employee",
    });
  }
  return facts;
}

export function shortHash(hash: string): string {
  return hash.slice(0, 12);
}

export type StatusTone = "action" | "pass" | "neutral" | "attention" | "alert";
export type StatusMarkName = "review" | "check" | "x" | "triangle" | "alert" | "hollow";

export const STATUS_STYLE: Record<Status, { word: string; tone: StatusTone; mark: StatusMarkName }> = {
  pending: { word: "Awaiting your approval", tone: "action", mark: "review" },
  approved: { word: "Approved", tone: "action", mark: "check" },
  executed: { word: "Executed (simulated)", tone: "pass", mark: "check" },
  rejected: { word: "Rejected", tone: "neutral", mark: "x" },
  invalidated: { word: "Invalidated", tone: "attention", mark: "triangle" },
  failed: { word: "Failed", tone: "alert", mark: "alert" },
  uncertain: { word: "Outcome uncertain", tone: "attention", mark: "triangle" },
};

/** What the reply says once a person has decided. The card's own mark carries the state; this is only the words. */
export interface DecidedSheet {
  /** Written by code from the decision; the server's "waits for your approval" no longer holds. */
  headline: string;
}

/** What the reply says once a person has decided; null while the proposal still waits. */
export function decidedSheet(status: Status, card: ActionCard): DecidedSheet | null {
  switch (status) {
    case "executed":
      return { headline: `Executed (simulated): ${actionNoun(card)} for ${card.target_label}.` };
    case "rejected":
      return { headline: "Rejected: no action was taken." };
    case "invalidated":
      return { headline: "Not executed: the evidence no longer supports it." };
    case "failed":
      return { headline: "Not executed: the simulated action failed." };
    default:
      return null;
  }
}
