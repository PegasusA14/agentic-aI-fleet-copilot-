import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { getFixture } from "../../lib/fixtures";
import { WorkingLine } from "./WorkingLine";

const TRACE = getFixture("acme-001", "low_disk").trace;

describe("WorkingLine", () => {
  it("says it is reading the question before any step arrives", () => {
    render(<WorkingLine events={[]} />);
    expect(screen.getByRole("status", { name: "Working" })).toHaveTextContent("Reading your question");
  });

  it("shows only the latest step, one line", () => {
    render(<WorkingLine events={TRACE.slice(0, 4)} />);
    const line = screen.getByRole("status", { name: "Working" });
    expect(line).toHaveTextContent(TRACE[3].summary);
    expect(line).not.toHaveTextContent(TRACE[2].summary);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("ignores the closing respond event", () => {
    render(<WorkingLine events={TRACE} />);
    expect(screen.getByRole("status", { name: "Working" })).not.toHaveTextContent("Answer ready");
  });

  it("marks its spinner decorative", () => {
    const { container } = render(<WorkingLine events={[]} />);
    expect(container.querySelector(".spinner")).toHaveAttribute("aria-hidden", "true");
  });
});
