import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { getFixture } from "../../lib/fixtures";
import { setReducedMotion } from "../../test/motion";
import type { TraceEvent } from "../../lib/types";
import { WorkingLine } from "./WorkingLine";

const TRACE = getFixture("acme-001", "low_disk").trace;

function event(kind: TraceEvent["kind"], summary = "raw summary the person should not read"): TraceEvent {
  return { seq: 0, kind, summary, data: {}, tool: null, latency_ms: null, trace_id: "t" };
}

describe("WorkingLine", () => {
  afterEach(() => setReducedMotion(true));

  it("says it is reading the question before any step arrives", () => {
    render(<WorkingLine events={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Reading your question…");
  });

  it.each([
    ["scope", "Reading your question…"],
    ["plan", "Reading your question…"],
    ["tool_call", "Checking the telemetry…"],
    ["tool_result", "Reading the results…"],
    ["compose", "Writing the answer…"],
    ["explain", "Writing the answer…"],
    ["verify", "Checking every number…"],
    ["retry", "Trying a different lookup…"],
  ] as const)("words a %s step as %s", (kind, words) => {
    render(<WorkingLine events={[event(kind)]} />);
    expect(screen.getByRole("status")).toHaveTextContent(words);
  });

  it("never prints the raw step summary", () => {
    render(<WorkingLine events={TRACE.slice(0, 4)} />);
    const line = screen.getByRole("status");
    for (const step of TRACE) expect(line).not.toHaveTextContent(step.summary);
    expect(line).toHaveTextContent("Reading the results…");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("keeps the line before it when a step has no words of its own", () => {
    render(<WorkingLine events={[event("tool_call"), event("error")]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking the telemetry…");
  });

  it("ignores the closing respond event", () => {
    render(<WorkingLine events={TRACE} />);
    expect(screen.getByRole("status")).not.toHaveTextContent("Answer ready");
    expect(screen.getByRole("status")).toHaveTextContent("Checking every number…");
  });

  it("has no name of its own, so the words are what is announced", () => {
    render(<WorkingLine events={[]} />);
    expect(screen.getByRole("status")).not.toHaveAttribute("aria-label");
  });

  it("marks its dot matrix decorative: 3 by 8 dots", () => {
    const { container } = render(<WorkingLine events={[]} />);
    const grid = container.querySelector(".dotmatrix");
    expect(grid).toHaveAttribute("aria-hidden", "true");
    expect(grid?.querySelectorAll(".dotmatrix__dot")).toHaveLength(24);
  });

  it("sweeps the dots when motion is allowed, with each dot starting later than the one before it in its row", () => {
    setReducedMotion(false);
    const { container } = render(<WorkingLine events={[]} />);
    const grid = container.querySelector(".dotmatrix") as HTMLElement;
    expect(grid).not.toHaveAttribute("data-still");
    const dots = [...grid.querySelectorAll<HTMLElement>(".dotmatrix__dot")];
    const delay = (i: number) => parseInt(dots[i].style.animationDelay, 10);
    expect(delay(1)).toBeGreaterThan(delay(0));
    expect(delay(8)).toBeGreaterThan(delay(0));
    expect(dots.some((dot) => dot.hasAttribute("data-lit"))).toBe(false);
  });

  it("holds the dots still under reduced motion: a band stays lit and no dot has an animation delay", () => {
    setReducedMotion(true);
    const { container } = render(<WorkingLine events={[]} />);
    const grid = container.querySelector(".dotmatrix") as HTMLElement;
    expect(grid).toHaveAttribute("data-still");
    const dots = [...grid.querySelectorAll<HTMLElement>(".dotmatrix__dot")];
    expect(dots.filter((dot) => dot.hasAttribute("data-lit")).length).toBeGreaterThan(0);
    expect(dots.every((dot) => dot.style.animationDelay === "")).toBe(true);
    // The words are still there, and the line starts fully visible.
    expect(screen.getByRole("status")).toHaveTextContent("Reading your question…");
    expect(screen.getByRole("status").style.opacity).not.toBe("0");
  });
});
