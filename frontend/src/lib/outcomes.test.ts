import { describe, expect, it } from "vitest";
import { reasonWords } from "./format";
import { outcomeStyle, OUTCOMES } from "./outcomes";

describe("outcomeStyle", () => {
  it("keeps the plain outcome style when there is no reason to refine it", () => {
    expect(outcomeStyle("answer")).toBe(OUTCOMES.answer);
    expect(outcomeStyle("denied", null)).toBe(OUTCOMES.denied);
    expect(outcomeStyle("tool_error", "not_eligible")).toBe(OUTCOMES.tool_error);
  });

  it("keeps Not found with the lock for a denied lookup", () => {
    expect(outcomeStyle("denied", "not_found")).toMatchObject({ word: "Not found", mark: "lock", tone: "denied" });
  });

  it("calls an action that is not eligible Not supported yet, with a neutral mark", () => {
    expect(outcomeStyle("denied", "not_eligible")).toMatchObject({
      word: "Not supported yet",
      tone: "neutral",
      mark: "hollow",
    });
  });

  it("calls a refusal for lack of evidence Not enough evidence, with a warning mark", () => {
    expect(outcomeStyle("denied", "insufficient_evidence")).toMatchObject({
      word: "Not enough evidence",
      tone: "attention",
      mark: "triangle",
    });
  });
});

describe("reasonWords", () => {
  it.each([
    ["not_eligible", /not supported|eligible/i],
    ["insufficient_evidence", /evidence/i],
    ["already_done", /^Already done$/],
    ["too_many_pending", /^Too many proposals waiting$/],
  ])("writes %s as a plain sentence, not the raw code", (code, pattern) => {
    const words = reasonWords(code);
    expect(words).toMatch(pattern);
    expect(words).not.toContain("_");
  });

  it("falls back to the code with spaces for one it does not know", () => {
    expect(reasonWords("some_new_code")).toBe("some new code");
  });
});
