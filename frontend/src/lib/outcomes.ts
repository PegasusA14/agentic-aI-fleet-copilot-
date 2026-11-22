import type { Outcome } from "../types/AnswerEnvelope";

export type Tone = "pass" | "attention" | "alert" | "neutral" | "denied" | "action";
export type MarkName = "check" | "triangle" | "alert" | "hollow" | "lock" | "question" | "review";

export interface OutcomeStyle {
  word: string;
  tone: Tone;
  mark: MarkName;
}

/** Every outcome gets a mark and a word, so state never rests on colour alone. */
export const OUTCOMES: Record<Outcome, OutcomeStyle> = {
  answer: { word: "Answer", tone: "pass", mark: "check" },
  no_match: { word: "No match", tone: "neutral", mark: "hollow" },
  insufficient_data: { word: "Not enough data", tone: "attention", mark: "triangle" },
  clarification: { word: "One detail needed", tone: "action", mark: "question" },
  proposal: { word: "Proposal for your decision", tone: "action", mark: "review" },
  denied: { word: "Not found", tone: "denied", mark: "lock" },
  unsupported: { word: "Not something I can do", tone: "neutral", mark: "hollow" },
  tool_error: { word: "Lookup failed", tone: "alert", mark: "alert" },
};

/**
 * The refusal reasons that have their own words. A refused action is `denied`, but the reason says which kind: a missing
 * device keeps "Not found" and the lock, another company is "Outside your company", an action the readings cannot back
 * is "Not enough evidence", and a question the data cannot answer says so.
 */
const BY_REASON: Record<string, OutcomeStyle> = {
  not_found: { word: "Not found", tone: "denied", mark: "lock" },
  other_company: { word: "Outside your company", tone: "denied", mark: "lock" },
  prompt_injection: { word: "Not followed", tone: "denied", mark: "lock" },
  not_eligible: { word: "Not supported yet", tone: "attention", mark: "triangle" },
  insufficient_evidence: { word: "Not enough evidence", tone: "attention", mark: "triangle" },
  already_done: { word: "Already done", tone: "neutral", mark: "hollow" },
  too_many_pending: { word: "Too many waiting", tone: "attention", mark: "triangle" },
  data_unavailable: { word: "Not in the data", tone: "neutral", mark: "hollow" },
  unsupported_action: { word: "Not available here", tone: "neutral", mark: "hollow" },
};

export function outcomeStyle(outcome: Outcome, reasonCode?: string | null): OutcomeStyle {
  if ((outcome === "denied" || outcome === "unsupported") && reasonCode && BY_REASON[reasonCode]) {
    return BY_REASON[reasonCode];
  }
  return OUTCOMES[outcome];
}
