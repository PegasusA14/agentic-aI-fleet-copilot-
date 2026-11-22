/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/index.css", "utf8");
const svg = readFileSync("public/favicon.svg", "utf8");

describe("the Neon-style dark world", () => {
  it("carries the spec's tokens", () => {
    for (const token of [
      "--bg: #0a0a0a",
      "--panel: #111111",
      "--panel-2: #171717",
      "--line: #1f1f1f",
      "--line-strong: #2e2e2e",
      "--ink: #ededed",
      "--ink-2: #a3a3a3",
      "--ink-3: #8a8a8a",
      "--accent: #00e599",
      "--attention: #f5b740",
      "--alert: #ff6369",
      "--action: #7ca8ff",
    ]) {
      expect(css).toContain(token);
    }
  });

  it("has no gradients, glows or fade masks, in the styles or the favicon", () => {
    expect(css).not.toMatch(/gradient\(/i);
    expect(css).not.toMatch(/mask(-image)?:/i);
    expect(svg).not.toMatch(/gradient/i);
  });

  it("keeps none of the old warm palette", () => {
    for (const old of ["#fbf8f5", "#f6a23c", "#f7916a", "#ee5a6f", "#d9480f", "#1c1714", "rgba(28, 23, 20"]) {
      expect(css.toLowerCase()).not.toContain(old);
    }
  });

  it("uses no display weight above 500", () => {
    expect(css).not.toMatch(/font-weight:\s*(6|7|8|9)\d\d/);
  });

  it("puts shadows on floating layers only", () => {
    const shadows = css.match(/box-shadow:[^;]+;/g) ?? [];
    expect(shadows.every((rule: string) => rule.includes("var(--shadow-float)"))).toBe(true);
  });

  // Decision (mockup colours win): Approve is a filled green button, the one solid accent fill on a control. The rule
  // that kept green to live data, pass and focus now has this single exception. Gradients stay banned (above).
  it("fills the Approve button with the accent, and no other button", () => {
    const rule = (selector: string) => css.match(new RegExp(`${selector.replace(/[.]/g, "\\.")}\\s*\\{[^}]*\\}`))?.[0] ?? "";
    expect(rule(".btn--approve")).toContain("background: var(--accent)");
    expect(rule(".btn--approve")).toContain("color: var(--on-accent)");
    for (const other of [".btn", ".btn--primary", ".link-btn", ".chip", ".proposal__edit"]) {
      expect(rule(other)).not.toMatch(/background:\s*var\(--accent\)/);
    }
  });

  it("marks a pending proposal with an amber hairline and the awaiting label in amber", () => {
    expect(css).toMatch(/\.proposal\[data-status="pending"\]\s*\{[^}]*border-color:\s*var\(--attention-line\)/);
    expect(css).toContain("--attention-line:");
  });
});
