import { type Fixture, fixturesFor, getFixture } from "./fixtures";

export const FALLBACK_SCENARIO = "clarification";

function normalise(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

/**
 * Replay-mode routing: pick the stored real answer whose keywords best match the question.
 * Score is the total length of matched keywords, so longer phrases beat short words; ties keep
 * fixture order. A question that matches nothing gets the clarification, never a guess.
 * The live agent replaces this whole module.
 */
export function routeQuestion(tenantId: string, question: string): Fixture {
  const text = normalise(question);
  let best: { fixture: Fixture; score: number } | null = null;
  for (const fixture of fixturesFor(tenantId)) {
    const score = fixture.meta.keywords.reduce(
      (sum, keyword) => (text.includes(keyword.toLowerCase()) ? sum + keyword.length : sum),
      0,
    );
    if (score > 0 && (best === null || score > best.score)) best = { fixture, score };
  }
  return best?.fixture ?? getFixture(tenantId, FALLBACK_SCENARIO);
}
