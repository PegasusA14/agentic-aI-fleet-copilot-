/** A vertical extent in viewport coordinates, as `getBoundingClientRect` reports it. */
export interface Box {
  top: number;
  bottom: number;
}

export interface RevealInput {
  /** The scrolling region the thread lives in. */
  scroller: Box;
  /** A docked composer that may sit over the scroller's lower edge. */
  composer?: Box | null;
  /** Breathing room kept inside the visible band at both edges. */
  margin: number;
  question: Box;
  /** The reply's first line of words. */
  headline?: Box | null;
  /** A proposal's Approve / Reject row, while it waits for a decision. */
  decision?: Box | null;
}

/**
 * How far to scroll (positive is down) when a reply arrives. The question goes to the top, as it always has, unless that
 * would leave what a person must act on outside the view: the decision row of a proposal, else the reply's headline.
 * That box is then brought fully into view above the composer, or by its top edge when it is taller than the view.
 */
export function revealDelta({ scroller, composer, margin, question, headline, decision }: RevealInput): number {
  const top = scroller.top + margin;
  const bottom = Math.min(scroller.bottom, composer ? composer.top : Infinity) - margin;
  const toQuestion = question.top - top;
  const must = decision ?? headline;
  if (!must) return toQuestion;
  // With the question at the top, a box that is already inside the band needs nothing more.
  if (must.bottom - toQuestion <= bottom && must.top - toQuestion >= top) return toQuestion;
  // Otherwise the box wins over the question: seat its bottom edge on the band's, or its top edge when it is too tall.
  if (must.bottom - must.top > bottom - top) return must.top - top;
  return Math.min(Math.max(must.bottom - bottom, toQuestion), must.top - top);
}
