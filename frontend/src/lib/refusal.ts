import type { AnswerEnvelope } from "../types/AnswerEnvelope";

/**
 * Which refusal card a reply takes, by its reason code. The card says the same thing the words do; the mark only
 * repeats it: a warning where more evidence would change the answer, a lock where the company is the limit,
 * a plain mark where the copilot cannot do what was asked, and none where the data simply does not hold it.
 */
export type RefusalKind =
  /** The readings do not back the action: says what would. Amber warning mark. */
  | "evidence"
  /** Another company: lock mark, and the way to switch. */
  | "company"
  /** Not found, a refused instruction: one uniform answer with no chips, so nothing about other companies leaks. */
  | "uniform"
  /** Outside the topic or the data: what the copilot can do instead. No mark. */
  | "scope"
  /** An action the copilot does not take in chat. Neutral mark. */
  | "neutral";

const KINDS: Record<string, RefusalKind> = {
  not_eligible: "evidence",
  insufficient_evidence: "evidence",
  already_done: "evidence",
  too_many_pending: "evidence",
  other_company: "company",
  not_found: "uniform",
  prompt_injection: "uniform",
  off_topic: "scope",
  data_unavailable: "scope",
  unsupported_action: "neutral",
};

/** The refusal card for a reply, or null when the reply is not one of these refusals. */
export function refusalKind(envelope: AnswerEnvelope): RefusalKind | null {
  if (envelope.outcome !== "denied" && envelope.outcome !== "unsupported") return null;
  return (envelope.reason_code && KINDS[envelope.reason_code]) || null;
}
