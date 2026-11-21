import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getFixture } from "../lib/fixtures";
import { Thread, type TurnData } from "./Thread";

const LOW = getFixture("acme-001", "low_disk");
const CLARIFY = getFixture("acme-001", "clarification");

function turn(over: Partial<TurnData> & { id: string }): TurnData {
  return { question: "Which devices are low on disk space?", events: [], status: "done", envelope: LOW.envelope, ...over };
}

function setup(turns: TurnData[]) {
  const handlers = { onAsk: vi.fn(), onOpenEvidence: vi.fn(), onOpenTrace: vi.fn() };
  const user = userEvent.setup();
  render(<Thread turns={turns} {...handlers} />);
  return { user, ...handlers };
}

describe("Thread", () => {
  it("renders each turn as the question bubble then the reply", () => {
    setup([turn({ id: "1" })]);
    const section = screen.getByRole("region", { name: "Question: Which devices are low on disk space?" });
    expect(within(section).getByText("Which devices are low on disk space?")).toBeInTheDocument();
    expect(within(section).getByRole("article")).toHaveTextContent(LOW.envelope.headline);
  });

  it("shows one working line with the latest step while a turn runs, and no reply yet", () => {
    setup([turn({ id: "1", status: "working", envelope: undefined, events: LOW.trace.slice(0, 3) })]);
    expect(screen.getByRole("status", { name: "Working" })).toHaveTextContent(LOW.trace[2].summary);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("replaces the working line with the reply when the turn is done", () => {
    const { container } = render(
      <Thread turns={[turn({ id: "1", events: LOW.trace })]} onAsk={() => {}} onOpenEvidence={() => {}} onOpenTrace={() => {}} />,
    );
    expect(screen.queryByRole("status", { name: "Working" })).not.toBeInTheDocument();
    expect(container.querySelector(".spinner")).toBeNull();
  });

  it("disables chips in earlier replies while a later turn runs", () => {
    setup([
      turn({ id: "1", question: "Which devices need attention?", envelope: CLARIFY.envelope }),
      turn({ id: "2", status: "working", envelope: undefined }),
    ]);
    for (const option of within(screen.getByRole("group", { name: "Choose one" })).getAllByRole("button")) {
      expect(option).toBeDisabled();
    }
  });

  it("lets a clarification option ask the next question", async () => {
    const { user, onAsk } = setup([turn({ id: "1", question: "Which devices need attention?", envelope: CLARIFY.envelope })]);
    await user.click(screen.getByRole("button", { name: "Outdated macOS" }));
    expect(onAsk).toHaveBeenCalledWith("Outdated macOS");
  });

  it("answers a failed turn with a lookup-failed message and a way to ask again", async () => {
    const { user, onAsk } = setup([
      turn({ id: "1", status: "failed", envelope: undefined, failure: "The copilot could not be reached." }),
    ]);
    const reply = screen.getByRole("article");
    expect(within(reply).getByText("Lookup failed")).toBeInTheDocument();
    expect(within(reply).getByText("The copilot could not be reached.")).toBeInTheDocument();
    await user.click(within(reply).getByRole("button", { name: "Ask again" }));
    expect(onAsk).toHaveBeenCalledWith("Which devices are low on disk space?");
  });

  it("renders reopened turns, which have no streamed events, through the same components", async () => {
    const { user, onOpenTrace } = setup([turn({ id: "1", events: [] }), turn({ id: "2", question: "Q2", events: [] })]);
    expect(screen.getAllByRole("article")).toHaveLength(2);
    await user.click(screen.getAllByRole("button", { name: "Details" })[0]);
    await user.click(screen.getByRole("button", { name: "Show work" }));
    expect(onOpenTrace).toHaveBeenCalledWith(expect.objectContaining({ id: "1" }));
  });
});
