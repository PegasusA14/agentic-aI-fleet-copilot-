/** The words that lead a reply, written once so the message and the screen-reader announcement agree. */
import { decidedSheet } from "./proposals";
import type { AnswerEnvelope, ProposalCard } from "./types";

/** Code-written: the server's proposal headline also says "nothing has changed", but in a longer sentence. */
export const PROPOSAL_LEAD = "Here is the proposal. Nothing changes until you approve it.";

/** The first sentence of a reply, for the announcement a screen reader makes when it lands. */
export function firstSentence(text: string): string {
  const match = /^.*?[.!?](?=\s|$)/s.exec(text.trim());
  return (match ? match[0] : text).trim();
}

export interface Reply {
  /** The bold one-liner: the composer's headline for an answer, or the words of a clarification, proposal or decision. */
  lead: string;
  /** The one supporting sentence under it: the composer's `summary`, else the first sentence of the verified explanation. */
  support: string | null;
  /** The verified model explanation in full, when there is one. The reply shows only its first sentence. */
  explanation: string | null;
  /** The lead is the composer's own headline, so it may carry a ticking count. */
  headline: boolean;
}

/**
 * What the reply opens with. `status` is the proposal's state as the person has since changed it.
 * The composer's headline always leads an answer; a verified explanation can only add the sentence under it.
 */
export function replyOf(envelope: AnswerEnvelope, status?: ProposalCard["status"]): Reply {
  const proposal = envelope.proposal;
  const now = status ?? proposal?.status;
  const decided = proposal && now ? decidedSheet(now, proposal) : null;
  const words = (lead: string): Reply => ({ lead, support: null, explanation: null, headline: false });
  if (decided) return words(decided.headline);
  if (envelope.clarification) return words(envelope.clarification.question);
  if (proposal && now === "pending") return words(PROPOSAL_LEAD);
  const explanation = envelope.explanation?.trim() || null;
  const summary = envelope.summary?.trim() || null;
  const sentence = explanation ? firstSentence(explanation) : null;
  const support = summary ?? (sentence && sentence.toLowerCase() !== envelope.headline.trim().toLowerCase() ? sentence : null);
  return { lead: envelope.headline, support, explanation, headline: true };
}

/** A headline that opens with a count the composer wrote: "2 of 10 …", "4 of your 5 …" or "3 actions …". */
export type LeadCount =
  | { kind: "of"; n: number; m: number; rest: string; joiner?: string }
  | { kind: "actions"; n: number; rest: string };

/**
 * Reads the count at the start of a code-written headline. Only the composer's own headline is ever passed here;
 * the model's prose is never parsed or altered. Anything that does not open with a count returns null, and only
 * quantities tick: a version or a serial that happens to start with digits is not a count.
 */
export function leadCount(text: string): LeadCount | null {
  const of = /^(\d{1,6}) of (?:(your|the|all|our|these|those) )?(\d{1,6})(?=\s|$)([\s\S]*)$/.exec(text);
  if (of) {
    const base = { kind: "of" as const, n: Number(of[1]), m: Number(of[3]), rest: of[4] };
    return of[2] ? { ...base, joiner: ` of ${of[2]} ` } : base;
  }
  const actions = /^(\d{1,6})( actions?(?=\W|$)[\s\S]*)$/.exec(text);
  if (actions) return { kind: "actions", n: Number(actions[1]), rest: actions[2] };
  return null;
}
