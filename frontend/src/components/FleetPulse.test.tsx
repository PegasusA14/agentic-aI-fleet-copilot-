import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { fleetFindings, fleetSize } from "../lib/fixtures";
import { setReducedMotion } from "../test/motion";
import { FleetPulse } from "./FleetPulse";

afterEach(() => setReducedMotion(true));

const dots = (container: HTMLElement) => container.querySelectorAll(".pulse__dot");
const lit = (container: HTMLElement) => container.querySelectorAll(".pulse__dot[data-lit]");

describe("the fleet pulse", () => {
  it("draws one dot per device and names the fleet in an accessible label", () => {
    const { container } = render(<FleetPulse size={10} />);
    expect(dots(container)).toHaveLength(10);
    expect(screen.getByRole("img", { name: "10 devices in your fleet" })).toBeInTheDocument();
    expect(lit(container)).toHaveLength(0);
  });

  it("says 'device' for a fleet of one", () => {
    render(<FleetPulse size={1} />);
    expect(screen.getByRole("img", { name: "1 device in your fleet" })).toBeInTheDocument();
  });

  it.each([null, 0])("draws nothing at all when the size is %s", (size) => {
    const { container } = render(<FleetPulse size={size} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lights one distinct dot per flagged device and says how many in the label", () => {
    const flagged = ["A1", "B2", "C3"];
    const { container } = render(<FleetPulse size={10} flagged={flagged} />);
    expect(lit(container)).toHaveLength(3);
    expect(screen.getByRole("img", { name: /^10 devices in your fleet, 3 with a/ })).toBeInTheDocument();
  });

  it("never lights more dots than there are devices", () => {
    const { container } = render(<FleetPulse size={2} flagged={["A", "B", "C", "D"]} />);
    expect(lit(container)).toHaveLength(2);
  });

  it("shows a mono count under the dots that the label already covers (hidden from assistive technology)", () => {
    const { container } = render(<FleetPulse size={7} flagged={["A"]} />);
    const label = container.querySelector(".pulse__label") as HTMLElement;
    expect(label).toHaveAttribute("aria-hidden", "true");
    expect(label.textContent).toMatch(/7\s*devices\s*·\s*1\s*flagged/);
  });

  it("fades the dots in one after another when motion is allowed, and breathes only the lit ones", () => {
    setReducedMotion(false);
    const { container } = render(<FleetPulse size={10} flagged={["A", "B"]} />);
    for (const dot of dots(container)) expect((dot as HTMLElement).style.opacity).toBe("0");
    const cores = [...container.querySelectorAll<HTMLElement>(".pulse__core")];
    expect(cores.filter((core) => core.style.animationDelay !== "")).toHaveLength(2);
  });

  it("is simply there under reduced motion: no entrance, no breathing", () => {
    setReducedMotion(true);
    const { container } = render(<FleetPulse size={10} flagged={["A", "B"]} />);
    for (const dot of dots(container)) expect((dot as HTMLElement).style.opacity).not.toBe("0");
    const cores = [...container.querySelectorAll<HTMLElement>(".pulse__core")];
    expect(cores.every((core) => core.style.animationDelay === "")).toBe(true);
  });

  it("is built from the replayed fleet: Acme has 10 devices and four with a finding", () => {
    expect(fleetSize("acme-001")).toBe(10);
    const flagged = fleetFindings("acme-001");
    expect(flagged).toHaveLength(4);
    const { container } = render(<FleetPulse size={fleetSize("acme-001")} flagged={flagged} />);
    expect(lit(container)).toHaveLength(4);
  });
});
