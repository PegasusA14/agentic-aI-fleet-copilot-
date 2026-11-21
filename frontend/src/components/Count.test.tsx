import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setReducedMotion } from "../test/motion";

const seen: { value: number; animated?: boolean; respectMotionPreference?: boolean }[] = [];

vi.mock("@number-flow/react", () => ({
  __esModule: true,
  default: (props: { value: number; animated?: boolean; respectMotionPreference?: boolean }) => {
    seen.push(props);
    return <span data-testid="flow">{props.value}</span>;
  },
  useIsSupported: () => true,
}));

import { Count, CountedText, Tally } from "./Count";

afterEach(() => {
  seen.length = 0;
  setReducedMotion(true);
});

describe("Count", () => {
  it("hands number-flow the value and lets it animate when motion is allowed", () => {
    setReducedMotion(false);
    render(<Count value={7} />);
    expect(screen.getByTestId("flow")).toHaveTextContent("7");
    expect(seen.at(-1)).toMatchObject({ value: 7, animated: true, respectMotionPreference: true });
  });

  it("never lets number-flow animate under reduced motion", () => {
    setReducedMotion(true);
    render(<Count value={7} countUp />);
    expect(seen.every((props) => props.animated === false)).toBe(true);
    // And it does not start from zero: the right number is there from the first paint.
    expect(seen[0].value).toBe(7);
  });

  it("rolls up from zero for a number that has just arrived", () => {
    setReducedMotion(false);
    render(<Count value={7} countUp />);
    expect(seen[0].value).toBe(0);
    expect(screen.getByTestId("flow")).toHaveTextContent("7");
  });

  it("does not roll up from zero when it was not asked to", () => {
    setReducedMotion(false);
    render(<Count value={7} />);
    expect(seen[0].value).toBe(7);
  });

  it("follows the value when it changes", () => {
    setReducedMotion(false);
    const { rerender } = render(<Count value={2} />);
    rerender(<Count value={3} />);
    expect(screen.getByTestId("flow")).toHaveTextContent("3");
  });
});

describe("CountedText and Tally", () => {
  it("reads the whole sentence once as plain text and hides the pieces", () => {
    render(
      <p>
        <CountedText text="2 of 10 devices">
          <Count value={2} /> of <Count value={10} /> devices
        </CountedText>
      </p>,
    );
    expect(screen.getByText("2 of 10 devices")).toHaveClass("sr-only");
    const visible = screen.getAllByTestId("flow")[0].parentElement as HTMLElement;
    expect(visible).toHaveAttribute("aria-hidden", "true");
  });

  it("reads a tally as passed/total", () => {
    render(<Tally passed={9} total={12} />);
    expect(screen.getByText("9/12")).toHaveClass("sr-only");
    expect(screen.getAllByTestId("flow").map((node) => node.textContent)).toEqual(["9", "12"]);
  });
});
