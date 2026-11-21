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
