import { describe, expect, it } from "vitest";
import { getFixture } from "./fixtures";
import { paragraphsOf, replyOf } from "./reply";
import type { AnswerEnvelope } from "./types";

const bare = (envelope: AnswerEnvelope): AnswerEnvelope => ({ ...envelope, reply: "", explanation: null });
const LOW_DISK = bare(getFixture("acme-001", "low_disk").envelope);
const PROPOSAL = bare(getFixture("acme-001", "proposal").envelope);

describe("paragraphsOf", () => {
  it("splits on blank lines, trims each part and drops empty ones", () => {
    expect(paragraphsOf("One.\n\nTwo.\n  \n\n  Three.  ")).toEqual(["One.", "Two.", "Three."]);
    expect(paragraphsOf("One line.\nStill one paragraph.")).toEqual(["One line.\nStill one paragraph."]);
    expect(paragraphsOf("")).toEqual([]);
    expect(paragraphsOf(null)).toEqual([]);
    expect(paragraphsOf("  \n\n  ")).toEqual([]);
  });

  it("leaves every character as written, markup included", () => {
    expect(paragraphsOf("**bold** <b>x</b>")).toEqual(["**bold** <b>x</b>"]);
  });
});

describe("replyOf with a reply", () => {
  const spoken = (extra: Partial<AnswerEnvelope> = {}): AnswerEnvelope => ({
    ...LOW_DISK,
    reply: "First paragraph.\n\nSecond paragraph.",
    explanation: "First paragraph.\n\nSecond paragraph.",
    ...extra,
  });

  it("keeps the composer's headline as the lead and carries the reply as paragraphs under it", () => {
    const reply = replyOf(spoken());
    expect(reply.lead).toBe(LOW_DISK.headline);
    expect(reply.body).toEqual(["First paragraph.", "Second paragraph."]);
    expect(reply).toMatchObject({ headline: true, emphatic: true, explanation: null });
  });

  it("moves the summary into the table card for a table answer, and drops the first-sentence support", () => {
    const withTable = spoken();
    expect(withTable.table).not.toBeNull();
    expect(replyOf(withTable)).toMatchObject({ support: null, tableSummary: LOW_DISK.summary });
  });

  it("keeps the summary as one quiet line when the answer has no table", () => {
    expect(replyOf(spoken({ table: null }))).toMatchObject({ support: LOW_DISK.summary, tableSummary: null });
  });

  it("falls back to the headline, summary and first explanation sentence when the reply is empty", () => {
    const empty = spoken({ reply: "", explanation: "Two laptops are nearly full. They need more storage.", summary: "" });
    expect(replyOf(empty)).toMatchObject({ body: [], tableSummary: null, support: "Two laptops are nearly full." });
    expect(replyOf({ ...LOW_DISK, reply: "   " })).toMatchObject({ body: [], support: LOW_DISK.summary });
  });

  it("leads a chat turn with the reply alone: no headline, the first paragraph is the lead", () => {
    const chat = { ...LOW_DISK, outcome: "chat" as const, reply: "Hello there.\n\nAsk me anything." };
    expect(replyOf(chat)).toMatchObject({ lead: "Hello there.", body: ["Hello there.", "Ask me anything."], headline: false });
  });

  it("puts the reply under a waiting proposal's lead in place of its supporting sentence, and drops it once decided", () => {
    const waiting = { ...PROPOSAL, reply: "That is ready for your review." };
    expect(replyOf(waiting)).toMatchObject({
      lead: "I'd open a screen lock ticket for acme-macbook-2.",
      support: null,
      body: ["That is ready for your review."],
    });
    expect(replyOf(waiting, "rejected").body).toEqual([]);
  });
});
