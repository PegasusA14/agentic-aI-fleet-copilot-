import { describe, expect, it } from "vitest";
import { revealDelta, type Box } from "./reveal";

const box = (top: number, bottom: number): Box => ({ top, bottom });

// A 400px-tall scroller from y=100 to y=500 with an 8px margin: the visible band is 108..492.
const scroller = box(100, 500);
const MARGIN = 8;

describe("revealDelta", () => {
  it("puts the question at the top when no headline or decision needs more", () => {
    expect(revealDelta({ scroller, margin: MARGIN, question: box(250, 290) })).toBe(250 - 108);
  });

  it("keeps the question at the top when the headline already fits below it", () => {
    const delta = revealDelta({ scroller, margin: MARGIN, question: box(300, 340), headline: box(360, 400) });
    expect(delta).toBe(300 - 108);
  });

  it("keeps the question at the top when the decision row also fits", () => {
    const delta = revealDelta({ scroller, margin: MARGIN, question: box(300, 340), decision: box(480, 530) });
    // The decision's bottom lands at 530 - 192 = 338, well inside the band.
    expect(delta).toBe(192);
  });

  it("scrolls on until the decision row is fully visible when the question at the top would hide it", () => {
    const delta = revealDelta({ scroller, margin: MARGIN, question: box(300, 340), decision: box(780, 830) });
    expect(delta).toBe(830 - 492);
    expect(830 - delta).toBeLessThanOrEqual(492);
    expect(780 - delta).toBeGreaterThanOrEqual(108);
  });

  it("stops above a docked composer that overlaps the scroller", () => {
    const delta = revealDelta({
      scroller,
      composer: box(420, 500),
      margin: MARGIN,
      question: box(300, 340),
      decision: box(780, 830),
    });
    expect(830 - delta).toBe(420 - MARGIN);
  });

  it("ignores a composer that sits below the scroller", () => {
    const withBelow = revealDelta({ scroller, composer: box(500, 580), margin: MARGIN, question: box(300, 340), decision: box(780, 830) });
    const without = revealDelta({ scroller, margin: MARGIN, question: box(300, 340), decision: box(780, 830) });
    expect(withBelow).toBe(without);
  });

  it("aligns the top of a decision row taller than the view, so its start is not lost", () => {
    const delta = revealDelta({ scroller, margin: MARGIN, question: box(300, 340), decision: box(600, 1200) });
    expect(delta).toBe(600 - 108);
  });

  it("never leaves the reply's headline below the fold when there is no decision row", () => {
    // The question is tall, so top-aligning it would leave the headline under the edge.
    const delta = revealDelta({ scroller, margin: MARGIN, question: box(300, 700), headline: box(720, 760) });
    expect(760 - delta).toBeLessThanOrEqual(492);
    expect(720 - delta).toBeGreaterThanOrEqual(108);
  });

  it("prefers the decision row over the headline when both are given", () => {
    const delta = revealDelta({
      scroller,
      margin: MARGIN,
      question: box(300, 340),
      headline: box(360, 400),
      decision: box(780, 830),
    });
    expect(830 - delta).toBeLessThanOrEqual(492);
  });
});
