/// <reference types="node" />
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/index.css", "utf8");
const svg = readFileSync("public/favicon.svg", "utf8");

/** The `:root { ... }` block: the one place a colour, a font or a shadow is written down. */
const tokenBlock = css.match(/:root\s*\{[^}]*\}/)?.[0] ?? "";
const outsideTokens = css.replace(tokenBlock, "");
const rule = (selector: string) =>
  css.match(new RegExp(`(?:^|\\n)${selector.replace(/[.[\]"=()]/g, "\\$&")}\\s*\\{[^}]*\\}`))?.[0] ?? "";

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "fixtures" ? [] : sourceFiles(path);
    return /\.(tsx?|css)$/.test(name) && !/\.test\./.test(name) && !/\.d\.ts$/.test(name) ? [path] : [];
  });
}

describe("the light design system", () => {
  it("carries the tokens: a white ground, ink and muted ink, hairlines, one blue accent and the state tones", () => {
    expect(tokenBlock).toContain("color-scheme: light");
    for (const token of [
      "--bg: #ffffff",
      "--surface: #f4f4f4",
      "--line: #e6e6e6",
      "--line-subtle: #f0f0f0",
      "--ink: #0a0a0a",
      "--ink-2: #6b6b6b",
      "--accent: #155dfc",
      "--accent-hover: #0f4fe0",
      "--accent-wash: #eff6ff",
      "--pass: #009767",
      "--pass-wash: #ecfdf5",
      "--attention: #dd7400",
      "--attention-wash: #fffbeb",
      "--alert: #c62828",
    ]) {
      expect(tokenBlock).toContain(token);
    }
  });

  it("writes every colour literal once, in the token block, and never in a component", () => {
    const literal = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(|\boklab\(/;
    expect(outsideTokens).not.toMatch(literal);
    for (const file of sourceFiles("src")) {
      if (file.endsWith("index.css")) continue;
      expect(readFileSync(file, "utf8"), file).not.toMatch(literal);
    }
  });

  it("has no gradients, glows or fade masks, in the styles or the favicon", () => {
    expect(css).not.toMatch(/gradient\(/i);
    expect(css).not.toMatch(/mask(-image)?:/i);
    expect(svg).not.toMatch(/gradient/i);
  });

  it("keeps no trace of the old dark palette", () => {
    for (const old of ["#0a0a0a;\n  --panel", "#111111", "#171717", "#00e599", "#7ca8ff", "#ff6369", "#f5b740"]) {
      expect(css.toLowerCase()).not.toContain(old);
    }
    expect(css).not.toContain("color-scheme: dark");
  });

  it("sets Geist for text, Geist Mono for labels and numbers, and Instrument Serif italic for one word only", () => {
    expect(tokenBlock).toMatch(/--font:\s*"Geist Variable"/);
    expect(tokenBlock).toMatch(/--mono:\s*"Geist Mono Variable"/);
    expect(tokenBlock).toMatch(/--serif:\s*"Instrument Serif"/);
    expect(outsideTokens.match(/var\(--serif\)/g)).toHaveLength(1);
    const em = rule(".hero__em");
    expect(em).toContain("font-family: var(--serif)");
    expect(em).toContain("font-style: italic");
  });

  it("uses no display weight above 500", () => {
    expect(css).not.toMatch(/font-weight:\s*(6|7|8|9)\d\d/);
  });

  it("has square corners: no radius but zero, the token (zero), a hairline softening or a round dot", () => {
    expect(tokenBlock).toContain("--r-panel: 0;");
    expect(tokenBlock).toContain("--r-input: 0;");
    const radii = outsideTokens.match(/border-radius:\s*[^;]+;/g) ?? [];
    expect(radii.length).toBeGreaterThan(20);
    const allowed = /^border-radius:\s*(0|50%|inherit|var\(--r-(panel|input|hair)\));$/;
    expect(radii.filter((r) => !allowed.test(r))).toEqual([]);
  });

  it("puts shadows on floating layers only", () => {
    const shadows = css.match(/box-shadow:[^;]+;/g) ?? [];
    expect(shadows.length).toBeGreaterThan(0);
    expect(shadows.every((s: string) => s.includes("var(--shadow-float)"))).toBe(true);
    expect(tokenBlock).toMatch(/--shadow-float:[^;]*rgba\(10, 10, 10, 0\.\d+\)/);
  });

  // Approve is the one solid accent fill on a control. The primary next step is outlined in the accent on its pale wash.
  it("fills the Approve button with the blue accent, and no other button", () => {
    expect(rule(".btn--approve")).toContain("background: var(--accent)");
    expect(rule(".btn--approve")).toContain("color: var(--on-accent)");
    for (const other of [".btn", ".btn--primary", ".link-btn", ".chip", ".proposal__edit"]) {
      expect(rule(other)).not.toMatch(/background:\s*var\(--accent\)/);
    }
  });

  it("outlines the primary next step in the accent on the accent wash, and Reject stays an outlined neutral", () => {
    const primary = rule('.chip--action[data-primary]');
    expect(primary).toContain("border-color: var(--accent)");
    expect(primary).toContain("background: var(--accent-wash)");
    expect(rule(".btn")).toContain("border: 1px solid var(--line-hover)");
    expect(rule(".btn")).toContain("background: transparent");
  });

  it("draws the sparkline in the accent with an amber threshold, and keeps its words in ink", () => {
    expect(rule(".spark__line,\n.spark__proj")).toContain("stroke: var(--accent)");
    expect(rule(".spark__threshold line")).toContain("stroke: var(--attention)");
    expect(rule(".spark__threshold text,\n.spark__axis")).toContain("fill: var(--ink-2)");
  });

  it("sets state words in the darker ink of their tone, so the words keep 4.5:1 and the marks keep the tone", () => {
    expect(tokenBlock).toContain("--pass-ink: #00704b");
    expect(tokenBlock).toContain("--attention-ink: #a85400");
    // Only a drawn mark (an icon or a dot) takes the tone itself; a rule that colours text never does.
    const toned = [...outsideTokens.matchAll(/([^{}]+)\{[^}]*\n\s*color:\s*var\(--(pass|attention)\);/g)].map((m) => m[1].trim());
    expect(toned.length).toBeGreaterThan(0);
    expect(toned.filter((selector) => !/svg|icon|mark/.test(selector))).toEqual([]);
  });

  it("sets the model's reply in ink at a reading measure, with no gradient and no weight above 500", () => {
    const reply = css.match(/\.reply__p\s*\{[^}]*\}/)?.[0] ?? "";
    expect(reply).toContain("color: var(--ink)");
    expect(reply).toMatch(/line-height:\s*1\.[56]/);
    expect(css.match(/\.reply\s*\{[^}]*\}/)?.[0]).toContain("max-width: 68ch");
    expect(reply).not.toContain("var(--ink-2)");
  });

  it("keeps the notes popover a floating white layer: its shadow is the float shadow and it grows from its corner", () => {
    const panel = css.match(/\.notes__panel\s*\{[^}]*\}/)?.[0] ?? "";
    expect(panel).toContain("box-shadow: var(--shadow-float)");
    expect(panel).toContain("background: var(--panel)");
    expect(panel).toContain("transform-origin: bottom left");
  });

  it("marks a pending proposal with an amber hairline and the awaiting label in amber", () => {
    expect(css).toMatch(/\.proposal\[data-status="pending"\]\s*\{[^}]*border-color:\s*var\(--attention-line\)/);
    expect(css).toContain("--attention-line:");
  });

  it("has no coloured side border on a row or card", () => {
    expect(outsideTokens).not.toMatch(/border-(left|right)(-color)?:\s*[^;]*var\(--(accent|attention|alert|pass)\)/);
  });
});

// jsdom does no layout, so these read the rules that keep the shell exactly one window tall. A real-browser check of the
// same promise (document height equals window height after a long conversation) lives in the screenshot script.
describe("the app shell is one window tall and only the thread scrolls", () => {
  it("makes the shell the height of the window with dvh, and clips it so nothing can scroll the page", () => {
    const app = rule(".app");
    expect(app).toMatch(/height:\s*100vh;/);
    expect(app).toMatch(/height:\s*100dvh;/);
    expect(app).toContain("overflow: clip");
    expect(app).toMatch(/grid-template-rows:\s*auto minmax\(0, 1fr\)/);
  });

  it("lets the stage shrink inside its grid row, and makes the thread the only scroller in the chat column", () => {
    expect(rule(".stage")).toContain("min-height: 0");
    const main = rule('.stage[data-view="thread"] .stage__main');
    expect(main).toContain("flex: 1");
    expect(main).toContain("overflow-y: auto");
    expect(main).toContain("position: relative");
    expect(rule(".stage__composer")).not.toMatch(/position:\s*(absolute|fixed)/);
  });

  it("contains the visually hidden text inside each scroller, so it cannot stretch the document", () => {
    expect(rule(".sr-only")).toContain("position: absolute");
    for (const view of ["approvals", "observe"]) {
      const stage = rule(`.stage[data-view="${view}"]`);
      expect(stage).toContain("position: relative");
      expect(stage).toContain("overflow-y: auto");
    }
  });

  it("gives the sidebar the full height of its row and its own scroll for the history", () => {
    expect(rule(".sidebar-wrap")).toContain("min-height: 0");
    expect(rule(".sidebar")).toContain("min-height: 0");
    expect(rule(".sidebar__history")).toMatch(/overflow-y:\s*auto/);
  });
});
