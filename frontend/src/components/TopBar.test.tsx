import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TopBar } from "./TopBar";

function renderBar() {
  render(
    <TopBar
      companyName="Acme"
      asOf="2026-06-12T11:00:00Z"
      onSignOut={() => {}}
      approvals={{ pending: 2, active: false }}
      audit={{ active: false }}
    />,
  );
  return screen.getByRole("banner");
}

describe("the top bar on small screens", () => {
  it("keeps the wordmark as the brand mark's accessible name", () => {
    const bar = renderBar();
    expect(within(bar).getByText("Fleet Copilot")).toHaveClass("brand__word");
  });

  it("says the full as-of time on the chip, whatever the visible width", () => {
    const bar = renderBar();
    const chip = within(bar).getByTitle("As of 2026-06-12 11:00 UTC");
    expect(chip).toHaveTextContent("As of 2026-06-12 11:00 UTC");
    expect(chip.querySelector(".asof__short")).toHaveTextContent("2026-06-12");
    expect(chip.querySelector(".asof__short")).toHaveAttribute("aria-hidden", "true");
  });

  it("names every icon-only control", () => {
    const bar = renderBar();
    expect(within(bar).getByRole("link", { name: "Approvals, 2 pending" })).toBeInTheDocument();
    expect(within(bar).getByRole("link", { name: "Audit" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Switch company" })).toBeInTheDocument();
  });

  it("offers no second way to start over", () => {
    const bar = renderBar();
    expect(within(bar).queryByRole("button", { name: /new conversation|new question/i })).not.toBeInTheDocument();
  });
});
