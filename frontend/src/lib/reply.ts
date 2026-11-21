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

/** A headline that opens with a count the composer wrote: "2 of 10 …" or "3 actions …". */
export type LeadCount = { kind: "of"; n: number; m: number; rest: string } | { kind: "actions"; n: number; rest: string };

/**
 * Reads the count at the start of a code-written headline. Only the composer's own headline is ever passed here;
 * the model's prose is never parsed or altered. Anything that does not open with a count returns null.
 */
export function leadCount(text: string): LeadCount | null {
  const of = /^(\d{1,6}) of (\d{1,6})(?=\s|$)([\s\S]*)$/.exec(text);
  if (of) return { kind: "of", n: Number(of[1]), m: Number(of[2]), rest: of[3] };
  const actions = /^(\d{1,6})( actions?(?=\W|$)[\s\S]*)$/.exec(text);
  if (actions) return { kind: "actions", n: Number(actions[1]), rest: actions[2] };
  return null;
}
