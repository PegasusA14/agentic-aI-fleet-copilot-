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

  it("always leads an answer with the composer's headline, even when the model wrote an explanation", () => {
    const explained = { ...LOW_DISK, summary: "", explanation: "Two laptops are nearly full. They need more storage." };
    expect(replyOf(explained).lead).toBe(LOW_DISK.headline);
    expect(replyOf(explained).headline).toBe(true);
    expect(replyOf(LOW_DISK)).toMatchObject({ lead: LOW_DISK.headline, explanation: null, headline: true });
  });

  it("puts one sentence under the headline: the summary, else the first sentence of the explanation", () => {
    expect(replyOf(LOW_DISK).support).toBe(LOW_DISK.summary);
    const explained = { ...LOW_DISK, summary: "", explanation: "Two laptops are nearly full. They need more storage." };
    expect(replyOf(explained).support).toBe("Two laptops are nearly full.");
    expect(replyOf(explained).explanation).toBe("Two laptops are nearly full. They need more storage.");
    expect(replyOf({ ...explained, explanation: null }).support).toBeNull();
  });

  it("prefers the code-written summary to the model's explanation", () => {
    const both = { ...LOW_DISK, explanation: "Something the model said." };
    expect(replyOf(both).support).toBe(LOW_DISK.summary);
  });

  it("adds no second sentence when the explanation only repeats the headline", () => {
    const echo = { ...LOW_DISK, summary: "", explanation: LOW_DISK.headline };
    expect(replyOf(echo).support).toBeNull();
  });

  it("gives a clarification or a pending proposal no supporting sentence", () => {
    expect(replyOf(PROPOSAL).support).toBeNull();
    expect(replyOf(PROPOSAL, "rejected").support).toBeNull();
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

  it("reads '4 of your 5 …' and keeps the words between the two numbers", () => {
    expect(leadCount("4 of your 5 Macs are behind macOS 15.")).toEqual({
      kind: "of",
      n: 4,
      m: 5,
      joiner: " of your ",
      rest: " Macs are behind macOS 15.",
    });
    expect(leadCount("3 of the 8 laptops")).toMatchObject({ n: 3, m: 8, joiner: " of the " });
  });

  it("never ticks an identifier or a version", () => {
    expect(leadCount("14.5 of 15 is the version")).toBeNull();
    expect(leadCount("4 of your 5.2 Macs")).toBeNull();
    expect(leadCount("MT7PJB7N5LRE has 2 of 10")).toBeNull();
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
