import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { getFixture } from "../../lib/fixtures";
import { setReducedMotion } from "../../test/motion";
import { Sparkline } from "./Sparkline";

const insight = getFixture("acme-001", "battery_wear").envelope.insights[0]!;

function chart(animate = false) {
  return render(<Sparkline label={insight.label} series={insight.series} projection={insight.projection} animate={animate} />);
}

describe("Sparkline", () => {
  it("is an image with the chart in words, and no per-point labels", () => {
    chart();
    const img = screen.getByRole("img");
    expect(img).toHaveAccessibleName(/acme-thinkpad-8: Capacity index over 30 snapshots, 100 on May 14 to 95\.8 on Jun 12\./);
    expect(img).toHaveAccessibleName(/Our service threshold is 90\./);
    expect(img).toHaveAccessibleName(/straight-line estimate reaches it around Jul 21/);
    // the axis names only the first date, today and the estimate
    const labels = [...img.querySelectorAll(".spark__axis")].map((el) => el.textContent);
    expect(labels).toEqual(["May 14", "Today, Jun 12", "Jul 21 (est.)"]);
  });

  it("draws the readings, a dashed estimate to the threshold, and the threshold named as ours", () => {
    const { container } = chart();
    expect(container.querySelector(".spark__line")).toHaveAttribute("d");
    expect(container.querySelector(".spark__proj")).toHaveAttribute("d");
    expect(container.querySelector(".spark__threshold text")).toHaveTextContent("Our service threshold");
    expect(screen.getByText(insight.series.threshold!.label)).toBeInTheDocument();
    expect(container.querySelector(".spark__now circle")).toHaveAttribute("r", "4");
  });

  it("shows no estimate when the insight has none", () => {
    const { container } = render(<Sparkline label="x" series={insight.series} projection={null} animate={false} />);
    expect(container.querySelector(".spark__proj")).toBeNull();
    expect(screen.getByRole("img").querySelectorAll(".spark__axis")).toHaveLength(2);
  });

  it("is reachable by keyboard: focus shows the latest reading, arrows step, Escape clears", async () => {
    const user = userEvent.setup();
    chart();
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("tabindex", "0");
    expect(document.querySelector(".spark__tip")).toBeNull();

    await user.tab();
    expect(img).toHaveFocus();
    const tip = () => document.querySelector(".spark__tip") as HTMLElement;
    // value first, then the date
    expect(tip().firstElementChild).toHaveTextContent("95.8");
    expect(tip()).toHaveTextContent("Jun 12");

    await user.keyboard("{ArrowLeft}");
    expect(tip().firstElementChild).toHaveTextContent(String(Math.round(insight.series.points[28]!.value * 10) / 10));
    await user.keyboard("{Home}");
    expect(tip().firstElementChild).toHaveTextContent("100");
    expect(tip()).toHaveTextContent("May 14");
    await user.keyboard("{ArrowLeft}");
    expect(tip()).toHaveTextContent("May 14");
    await user.keyboard("{End}");
    expect(tip()).toHaveTextContent("Jun 12");
    expect(screen.getByText("95.8 on Jun 12", { selector: "[aria-live]" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(document.querySelector(".spark__tip")).toBeNull();
  });

  it("snaps the crosshair to the nearest snapshot under the pointer, and lets go on leave", () => {
    chart();
    const img = screen.getByRole("img");
    img.getBoundingClientRect = () => ({ left: 0, top: 0, right: 280, bottom: 96, width: 280, height: 96, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerMove(img, { clientX: 0 });
    expect(document.querySelector(".spark__tip")).toHaveTextContent("100");
    expect(document.querySelector(".spark__cross")).not.toBeNull();
    fireEvent.pointerMove(img, { clientX: 150 });
    expect(document.querySelector(".spark__tip")).not.toHaveTextContent("100");
    fireEvent.pointerLeave(img);
    expect(document.querySelector(".spark__tip")).toBeNull();
  });

  it("draws in on a reply that just arrived, and holds still on reopen or under reduced motion", () => {
    const motionOn = () => document.querySelector(".spark__draw")?.hasAttribute("data-draw");
    chart(true);
    expect(motionOn()).toBe(true);
    expect(document.querySelector(".spark__now")).toHaveAttribute("data-late");
    document.body.innerHTML = "";
    chart(false);
    expect(motionOn()).toBe(false);
    expect(document.querySelector(".spark__now")).not.toHaveAttribute("data-late");
    setReducedMotion(true);
  });
});
