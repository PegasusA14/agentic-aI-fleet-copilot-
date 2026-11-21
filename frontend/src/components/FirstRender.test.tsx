import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getFixture } from "../lib/fixtures";
import { setReducedMotion } from "../test/motion";

const seen: number[] = [];

vi.mock("@number-flow/react", () => ({
  __esModule: true,
  default: (props: { value: number }) => {
    seen.push(props.value);
    return <span>{props.value}</span>;
  },
  useIsSupported: () => true,
}));

import { AssistantMessage } from "./chat/AssistantMessage";
import { FleetPulse } from "./FleetPulse";

afterEach(() => {
  seen.length = 0;
  setReducedMotion(true);
});

describe("numbers never count up from zero", () => {
  it("a fresh reply headline hands number-flow the final digits from its first render", () => {
    setReducedMotion(false);
    const envelope = getFixture("acme-001", "low_disk").envelope;
    render(<AssistantMessage envelope={envelope} busy={false} onOpenEvidence={vi.fn()} onOpenTrace={vi.fn()} onAsk={vi.fn()} />);
    expect(envelope.headline).toMatch(/^2 of 10 /);
    expect(seen).toEqual([2, 10]);
  });

  it("the pulse label hands number-flow the devices and the flagged count from its first render", () => {
    setReducedMotion(false);
    render(<FleetPulse size={10} flagged={["A", "B", "C"]} />);
    expect(seen).toEqual([10, 3]);
  });
});
