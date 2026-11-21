import { describe, expect, it } from "vitest";
import { getFixture } from "./fixtures";
import { PROPOSAL_LEAD, firstSentence, leadCount, replyOf } from "./reply";

const PROPOSAL = getFixture("acme-001", "proposal").envelope;
const LOW_DISK = getFixture("acme-001", "low_disk").envelope;

describe("replyOf", () => {
  it("leads a waiting proposal with code-written words, not the server's longer headline", () => {
    expect(replyOf(PROPOSAL).lead).toBe(PROPOSAL_LEAD);
    expect(PROPOSAL_LEAD).toBe("Here is the proposal. Nothing changes until you approve it.");
  });

  it("leads a decided proposal with the decision", () => {
    expect(replyOf(PROPOSAL, "rejected").lead).toBe("Rejected: no action was taken.");
  });

  it("leads with the explanation when there is one, and says so", () => {
    expect(replyOf({ ...LOW_DISK, explanation: "Two laptops are nearly full." })).toEqual({
      lead: "Two laptops are nearly full.",
      explanation: "Two laptops are nearly full.",
    });
    expect(replyOf(LOW_DISK)).toEqual({ lead: LOW_DISK.headline, explanation: null });
  });
});

describe("firstSentence", () => {
  it("takes the first sentence, or the whole text when it has no full stop", () => {
    expect(firstSentence("Reply one. Reply two.")).toBe("Reply one.");
    expect(firstSentence("2 of 10 devices have under 10% disk space free")).toBe("2 of 10 devices have under 10% disk space free");
    expect(firstSentence("Is it 10.5 GiB? Yes.")).toBe("Is it 10.5 GiB?");
  });
});

describe("leadCount", () => {
  it("reads N and M from a headline that opens 'N of M'", () => {
    expect(leadCount("2 of 10 devices have under 10% disk space free")).toEqual({
      kind: "of",
      n: 2,
      m: 10,
      rest: " devices have under 10% disk space free",
    });
  });

  it("reads N from a headline that opens 'N actions'", () => {
    expect(leadCount("3 actions are suggested")).toEqual({ kind: "actions", n: 3, rest: " actions are suggested" });
    expect(leadCount("1 action is suggested")).toEqual({ kind: "actions", n: 1, rest: " action is suggested" });
  });

  it.each([
    "7 devices grouped by model",
    "Nearly 2 of 10 devices",
    "2 of ten devices",
    "2 of 10devices",
    "12 actionable items",
    "",
  ])("leaves %j as plain text", (text) => {
    expect(leadCount(text)).toBeNull();
  });
});
