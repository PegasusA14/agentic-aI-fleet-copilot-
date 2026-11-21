import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";

function renderBar(onMenu?: () => void) {
  render(<TopBar companyName="Acme" asOf="2026-06-12T11:00:00Z" onSignOut={() => {}} onMenu={onMenu} />);
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
    const bar = renderBar(() => {});
    expect(within(bar).getByRole("button", { name: "Open menu" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Switch company" })).toBeInTheDocument();
  });

  it("holds brand, company, as-of and Switch company only: Approvals and Audit live in the sidebar", () => {
    const bar = renderBar();
    expect(within(bar).getByLabelText("Company: Acme")).toBeInTheDocument();
    expect(within(bar).queryByRole("link")).not.toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: "Open menu" })).not.toBeInTheDocument();
  });

  it("offers no second way to start over", () => {
    const bar = renderBar();
    expect(within(bar).queryByRole("button", { name: /new conversation|new question|new chat/i })).not.toBeInTheDocument();
  });

  it("opens the drawer from the menu button", async () => {
    const onMenu = vi.fn();
    const bar = renderBar(onMenu);
    await userEvent.click(within(bar).getByRole("button", { name: "Open menu" }));
    expect(onMenu).toHaveBeenCalledTimes(1);
  });
});
