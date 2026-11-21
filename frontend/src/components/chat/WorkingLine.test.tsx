import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { getFixture } from "../../lib/fixtures";
import type { TraceEvent } from "../../lib/types";
import { WorkingLine } from "./WorkingLine";

const TRACE = getFixture("acme-001", "low_disk").trace;

function event(kind: TraceEvent["kind"], summary = "raw summary the person should not read"): TraceEvent {
  return { seq: 0, kind, summary, data: {}, tool: null, latency_ms: null, trace_id: "t" };
}

describe("WorkingLine", () => {
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

  it("marks its spinner decorative", () => {
    const { container } = render(<WorkingLine events={[]} />);
    expect(container.querySelector(".spinner")).toHaveAttribute("aria-hidden", "true");
  });
});
