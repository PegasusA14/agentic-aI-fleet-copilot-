/** The words that lead a reply, written once so the message and the screen-reader announcement agree. */
import { decidedSheet } from "./proposals";
import type { AnswerEnvelope, ProposalCard } from "./types";

/** Code-written: the server's proposal headline also says "nothing has changed", but in a longer sentence. */
export const PROPOSAL_LEAD = "Here is the proposal. Nothing changes until you approve it.";

/**
 * What the reply opens with. `status` is the proposal's state as the person has since changed it.
 * `explanation` is set only when the lead is the verified explanation (it then stands in for the claims).
 */
export function replyOf(
  envelope: AnswerEnvelope,
  status?: ProposalCard["status"],
): { lead: string; explanation: string | null } {
  const proposal = envelope.proposal;
  const now = status ?? proposal?.status;
  const decided = proposal && now ? decidedSheet(now, proposal) : null;
  if (decided) return { lead: decided.headline, explanation: null };
  if (envelope.clarification) return { lead: envelope.clarification.question, explanation: null };
  if (proposal && now === "pending") return { lead: PROPOSAL_LEAD, explanation: null };
  if (envelope.explanation) return { lead: envelope.explanation, explanation: envelope.explanation };
  return { lead: envelope.headline, explanation: null };
}

/** The first sentence of a reply, for the announcement a screen reader makes when it lands. */
export function firstSentence(text: string): string {
  const match = /^.*?[.!?](?=\s|$)/s.exec(text.trim());
  return (match ? match[0] : text).trim();
}
