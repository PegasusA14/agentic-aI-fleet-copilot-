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
  reason: {
    battery_service_recommended: "Battery service recommended",
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

export type StatusTone = "action" | "pass" | "neutral" | "attention" | "alert" | "denied";
export type StatusMarkName = "review" | "check" | "x" | "triangle" | "alert" | "hollow";

export const STATUS_STYLE: Record<Status, { word: string; tone: StatusTone; mark: StatusMarkName }> = {
  pending: { word: "Awaiting your approval", tone: "attention", mark: "review" },
  approved: { word: "Approved", tone: "action", mark: "check" },
  executed: { word: "Executed (simulated)", tone: "pass", mark: "check" },
  rejected: { word: "Rejected", tone: "denied", mark: "x" },
  invalidated: { word: "Invalidated", tone: "denied", mark: "triangle" },
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

/** The short title on the card's header: what approving would do. */
export const ACTION_TITLE: Record<ActionType, string> = {
  upgrade_order: "Create upgrade order",
  remediation_ticket: "Open remediation ticket",
  replacement_flag: "Flag for replacement",
  employee_notification: "Notify employee",
};

/** What a person calls each compliance check, in running text. */
const CHECK_NAME: Record<string, string> = {
  os_up_to_date: "OS update",
  screen_lock: "screen lock",
  disk_encryption: "disk encryption",
};

const CHECK_ID = new RegExp(`\\b(${Object.keys(CHECK_NAME).join("|")})\\b`, "g");

const capitalise = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** Server-written text with its check codes in words: "screen_lock reports fail" reads "Screen lock reports fail". */
export function plainChecks(text: string): string {
  const words = text.replace(CHECK_ID, (id) => CHECK_NAME[id]);
  // Only a sentence that opened with a code is capitalised; "macOS 13.6" is left as written.
  return text.search(CHECK_ID) === 0 ? capitalise(words) : words;
}

const article = (phrase: string) => (/^(os|[aeiou])/i.test(phrase) ? "an" : "a");

/** The first sentence of some text, ending in its own full stop. */
function firstLine(text: string): string {
  const trimmed = text.trim();
  const match = /^.*?[.!?](?=\s|$)/s.exec(trimmed);
  const sentence = (match ? match[0] : trimmed).trim();
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

/**
 * The reply's opening for a proposal, written by code from the card: what the copilot would do, the first sentence
 * of why, and the promise that nothing is sent until a person approves. No model text is read here.
 */
export function proposalLead(card: ProposalCard): { lead: string; support: string } {
  const device = card.target_label;
  let lead: string;
  switch (card.action_type) {
    case "upgrade_order":
      lead = `I'd order ${article(upgradeKind(card))} ${upgradeKind(card)} upgrade for ${device}.`;
      break;
    case "remediation_ticket": {
      const check = CHECK_NAME[card.scope?.check_id ?? ""];
      lead = check
        ? `I'd open ${article(check)} ${check} ticket for ${device}.`
        : `I'd open a remediation ticket for ${device}.`;
      break;
    }
    case "replacement_flag":
      lead = `I'd flag ${device} for replacement.`;
      break;
    case "employee_notification":
      lead = `I'd notify the employee assigned to ${device}.`;
      break;
  }
  const why = card.rationale.trim() ? `${capitalise(firstLine(plainChecks(card.rationale)))} ` : "";
  return { lead, support: `${why}Nothing is sent until you approve it.` };
}

export interface ProposalField {
  key: string;
  label: string;
  value: string;
  /** Quieter text after the value: the device's serial. */
  sub?: string;
}

/** The labelled facts at the top of the card: the device, then what is specific to the action. */
export function proposalFields(card: ProposalCard): ProposalField[] {
  const scope = card.scope ?? {};
  const word = (key: string) => (scope[key] ? displayValue(key, scope[key]) : null);
  const fields: ProposalField[] = [
    {
      key: "device",
      label: "Device",
      value: card.target_label,
      sub: card.target_device_id && card.target_device_id !== card.target_label ? card.target_device_id : undefined,
    },
  ];
  const add = (key: string, label: string, value: string | null) => {
    if (value) fields.push({ key, label, value });
  };
  switch (card.action_type) {
    case "upgrade_order":
      add("component", "Component", word("component"));
      add("spec", "Spec", scope.current && scope.target ? `${scope.current} → ${scope.target}` : (scope.target ?? null));
      break;
    case "remediation_ticket":
      add("check", "Check", word("check_id"));
      add("severity", "Severity", scope.severity ? capitalise(scope.severity) : null);
      break;
    case "replacement_flag":
      add("reason", "Reason", word("reason"));
      break;
    case "employee_notification":
      add("recipient", "Recipient", "The assigned employee");
      add("topic", "Topic", word("topic"));
      break;
  }
  return fields;
}

/** What the card says about the blast radius when the server sent none (a card stored before it did). */
export function riskNote(card: ProposalCard): string {
  return card.risk?.trim() || `Affects only ${card.target_label}. Nothing leaves this system. The decision is recorded in Audit.`;
}

export type ResultTone = "pass" | "neutral" | "attention" | "alert";

export interface ProposalResult {
  tone: ResultTone;
  /** The mark in the circle: a check, a cross, or a warning triangle. */
  icon: "check" | "x" | "warning";
  title: string;
  line: string;
}

/** What was approved, for the result line: "Memory 16 GiB → 32 GiB" for an upgrade, else the action's title. */
function specWords(card: ProposalCard): string {
  if (card.action_type !== "upgrade_order") return ACTION_TITLE[card.action_type];
  return proposalFields(card)
    .filter((f) => f.key !== "device")
    .map((f) => f.value)
    .join(" ");
}

/**
 * What replaces the decision row once the proposal is decided. `byYou` is true only for a decision made in this
 * session: a card found already decided says it was approved, and Audit says by whom.
 */
export function proposalResult(card: ProposalCard, status: Status, byYou: boolean): ProposalResult | null {
  const noun = ACTION_WORDS[card.action_type];
  switch (status) {
    case "approved":
    case "executed":
      return {
        tone: "pass",
        icon: "check",
        title: `${noun} approved`,
        line: `${specWords(card)} on ${card.target_label} · ${byYou ? "approved by you" : "approved"} · simulated`,
      };
    case "rejected":
      return { tone: "neutral", icon: "x", title: `${noun} rejected`, line: "Nothing was changed. The rejection is logged." };
    case "invalidated":
      return {
        tone: "attention",
        icon: "warning",
        title: "No longer valid",
        line: "The data this proposal rested on has changed, so it no longer holds. Nothing was executed.",
      };
    case "failed":
      return {
        tone: "alert",
        icon: "warning",
        title: "Could not run",
        line: "The simulated action did not run, so nothing was recorded.",
      };
    case "uncertain":
      return {
        tone: "attention",
        icon: "warning",
        title: "Outcome uncertain",
        line: "It is not clear whether this ran. Check Audit before asking again.",
      };
    default:
      return null;
  }
}
