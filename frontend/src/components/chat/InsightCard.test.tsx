import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getFixture } from "../../lib/fixtures";
import { setReducedMotion } from "../../test/motion";
import { InsightCard } from "./InsightCard";

const BATTERY = getFixture("acme-001", "battery_wear").envelope;
const HEALTH = getFixture("acme-001", "fleet_health").envelope;
const insight = BATTERY.insights[0]!;

function setup(props: Partial<React.ComponentProps<typeof InsightCard>> = {}) {
  const onAsk = vi.fn();
  const user = userEvent.setup();
  const view = render(
    <InsightCard insight={insight} actions={BATTERY.suggested_actions} busy={false} onAsk={onAsk} {...props} />,
  );
  return { user, onAsk, ...view };
}

describe("a full insight card", () => {
  it("says the state in a word, the category, and the serial", () => {
    setup();
    expect(screen.getByText("Act soon")).toBeInTheDocument();
    expect(screen.getByText("Battery · Declining")).toBeInTheDocument();
    expect(screen.getByText(insight.device_id)).toBeInTheDocument();
  });

  it("leads with the finding, then the readings as a list, then why it matters", () => {
    setup();
    expect(screen.getByText("Finding")).toBeInTheDocument();
    expect(screen.getByText(insight.finding)).toBeInTheDocument();
    for (const item of insight.evidence_kv) {
      expect(screen.getByText(item.label)).toBeInTheDocument();
      expect(screen.getByText(item.value)).toHaveClass("mono");
    }
    expect(screen.getByText("Why this matters")).toBeInTheDocument();
    expect(screen.getByText(insight.why)).toBeInTheDocument();
  });

  it("shows the method line, and a chart that calls the threshold ours", () => {
    setup();
    expect(screen.getByText(insight.method)).toHaveClass("insight__method");
    expect(screen.getByRole("img")).toHaveAccessibleName(/Our service threshold/);
    expect(document.body.textContent).not.toMatch(/battery health/i);
  });

  it("offers the approvals for this device only, each saying it needs one, the first in green", () => {
    setup();
    const group = screen.getByRole("group", { name: "Suggested actions" });
    const chips = within(group).getAllByRole("button");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("Flag acme-thinkpad-8 for replacement");
    expect(chips[0]).toHaveAttribute("data-primary");
    expect(chips[1]).not.toHaveAttribute("data-primary");
    for (const chip of chips) {
      expect(chip).toHaveTextContent("Needs your approval");
      expect(chip.querySelector("svg")).not.toBeNull();
    }
    expect(screen.queryByText(/acme-dell-9/)).not.toBeInTheDocument();
  });

  it("sends an action's own request, and View history sends a plain question", async () => {
    const { user, onAsk } = setup();
    await user.click(screen.getByRole("button", { name: /Flag acme-thinkpad-8 for replacement/ }));
    expect(onAsk).toHaveBeenLastCalledWith("Flag acme-thinkpad-8 for replacement");
    await user.click(screen.getByRole("button", { name: "View history" }));
    expect(onAsk).toHaveBeenLastCalledWith("Show the 30-day battery history of acme-thinkpad-8");
  });

  it("waits while a turn is running", () => {
    setup({ busy: true });
    expect(screen.getByRole("button", { name: "View history" })).toBeDisabled();
  });

  it("leaves the chart out for a check that is pass or fail, and keeps its readings", () => {
    const check = HEALTH.insights.find((i) => i.category === "compliance")!;
    setup({ insight: check, actions: [] });
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("Compliance · Screen lock")).toBeInTheDocument();
    for (const item of check.evidence_kv) expect(screen.getByText(item.value)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View history" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Suggested actions" })).not.toBeInTheDocument();
  });

  it("draws its line in only on a reply that just arrived, never on reopen or under reduced motion", () => {
    const drawn = () => document.querySelector(".spark__draw")?.hasAttribute("data-draw");
    setReducedMotion(false);
    const first = setup();
    expect(drawn()).toBe(true);
    first.unmount();
    const again = setup({ reopened: true });
    expect(drawn()).toBe(false);
    again.unmount();
    setReducedMotion(true);
    setup();
    expect(drawn()).toBe(false);
  });

  it("charts a disk series without an estimate", () => {
    const disk = HEALTH.insights.find((i) => i.category === "disk")!;
    const { container } = setup({ insight: disk, actions: [] });
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(container.querySelector(".spark__proj")).toBeNull();
  });
});

describe("a compact insight card", () => {
  const watch = HEALTH.insights.find((i) => i.severity === "watch")!;

  it("is one line and a short evidence line, with no chart, no actions and no Why", () => {
    setup({ insight: watch, actions: [], compact: true });
    expect(screen.getByText("Watch")).toBeInTheDocument();
    expect(screen.getByText(watch.finding)).toBeInTheDocument();
    for (const item of watch.evidence_kv.slice(0, 2)) expect(screen.getByText(item.value)).toBeInTheDocument();
    expect(screen.queryByText(watch.evidence_kv[2]!.value)).not.toBeInTheDocument();
    expect(screen.queryByText("Why this matters")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View history" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show details" })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens to the full card and folds back", async () => {
    setReducedMotion(true);
    const { user } = setup({ insight: watch, actions: [], compact: true });
    await user.click(screen.getByRole("button", { name: "Show details" }));
    expect(screen.getByText("Why this matters")).toBeInTheDocument();
    expect(screen.getByText(watch.why)).toBeInTheDocument();
    expect(screen.getByText(watch.method)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View history" })).toBeInTheDocument();
    const hide = screen.getByRole("button", { name: "Show less" });
    expect(hide).toHaveAttribute("aria-expanded", "true");
    await user.click(hide);
    expect(screen.queryByText("Why this matters")).not.toBeInTheDocument();
  });
});
