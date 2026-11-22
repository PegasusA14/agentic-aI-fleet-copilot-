import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getFixture } from "../lib/fixtures";
import { setReducedMotion } from "../test/motion";
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
    expect(screen.getByText("Checking the telemetry…")).toBeInTheDocument();
    expect(screen.queryByText(LOW.trace[2].summary)).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("replaces the working line with the reply when the turn is done", () => {
    const { container } = render(
      <Thread turns={[turn({ id: "1", events: LOW.trace })]} onAsk={() => {}} onOpenEvidence={() => {}} onOpenTrace={() => {}} />,
    );
    expect(container.querySelector(".working")).toBeNull();
    expect(container.querySelector(".dotmatrix")).toBeNull();
  });

  it("announces 'Reply ready.' and the headline once, when a running turn finishes", () => {
    const handlers = { onAsk: vi.fn(), onOpenEvidence: vi.fn(), onOpenTrace: vi.fn() };
    const { rerender, container } = render(
      <Thread turns={[turn({ id: "1", status: "working", envelope: undefined, fresh: true })]} {...handlers} />,
    );
    const region = container.querySelector(".sr-only[role='status']") as HTMLElement;
    expect(region).toBeEmptyDOMElement();
    const explained = { ...LOW.envelope, explanation: "Two laptops are nearly full. Both need a storage upgrade." };
    act(() => {
      rerender(<Thread turns={[turn({ id: "1", envelope: explained, fresh: true })]} {...handlers} />);
    });
    expect(region).toHaveTextContent(`Reply ready. ${LOW.envelope.headline}`);
    expect(region).not.toHaveTextContent("Both need");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveClass("sr-only");
  });

  it("stays silent for a reopened turn that was never running", () => {
    const { container } = render(
      <Thread turns={[turn({ id: "1" })]} onAsk={() => {}} onOpenEvidence={() => {}} onOpenTrace={() => {}} />,
    );
    expect(container.querySelector(".sr-only[role='status']")).toBeEmptyDOMElement();
  });

  it("animates only turns asked in this session", () => {
    const { container } = render(
      <Thread
        turns={[turn({ id: "1" }), turn({ id: "2", fresh: true })]}
        onAsk={() => {}}
        onOpenEvidence={() => {}}
        onOpenTrace={() => {}}
      />,
    );
    const [reopened, fresh] = Array.from(container.querySelectorAll(".turn"));
    expect(reopened).not.toHaveAttribute("data-fresh");
    expect(fresh).toHaveAttribute("data-fresh");
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
    await user.click(screen.getAllByRole("button", { name: "How I got this" })[0]);
    await user.click(screen.getByRole("button", { name: "Show work" }));
    expect(onOpenTrace).toHaveBeenCalledWith(expect.objectContaining({ id: "1" }));
  });

  it("retries by asking the turn's own question again", async () => {
    const { user, onAsk } = setup([turn({ id: "1", question: "Which disks are full?" })]);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onAsk).toHaveBeenCalledWith("Which disks are full?");
  });
});

describe("Thread repeats", () => {
  const original = Element.prototype.scrollIntoView;
  afterEach(() => {
    Element.prototype.scrollIntoView = original;
  });
  const again: TurnData = turn({
    id: "2",
    envelope: { ...LOW.envelope, trace_id: "second-trace", repeat_of: LOW.envelope.trace_id, reply: "" },
  });

  it("collapses the repeat to a card and announces it", () => {
    const handlers = { onAsk: vi.fn(), onOpenEvidence: vi.fn(), onOpenTrace: vi.fn() };
    const { rerender, container } = render(
      <Thread turns={[turn({ id: "1" }), turn({ ...again, status: "working", envelope: undefined, fresh: true })]} {...handlers} />,
    );
    act(() => {
      rerender(<Thread turns={[turn({ id: "1" }), { ...again, fresh: true }]} {...handlers} />);
    });
    expect(screen.getByText(/Same answer as above\. Nothing has changed since 2026-06-12 11:00 UTC\./)).toBeInTheDocument();
    const regions = container.querySelectorAll(".turn > .sr-only[role='status']");
    expect(regions[1]).toHaveTextContent("Reply ready. Same answer as above.");
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("jumps to the earlier turn by its trace id", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const { user } = setup([turn({ id: "1" }), again]);
    scroll.mockClear();
    await user.click(screen.getByRole("button", { name: "Jump to it" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.instances[0]).toBe(document.getElementById("turn-1"));
    expect(document.getElementById("turn-1")).toHaveFocus();
  });

  it("offers no jump when the earlier answer is not in the thread", () => {
    setup([again]);
    expect(screen.queryByRole("button", { name: "Jump to it" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show again" })).toBeInTheDocument();
  });
});

describe("Thread scrolling", () => {
  const original = window.matchMedia;
  afterEach(() => {
    window.matchMedia = original;
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  function reduced(on: boolean) {
    window.matchMedia = ((query: string) => ({ ...original(query), matches: on && /reduce/.test(query) })) as typeof window.matchMedia;
  }

  it("brings a new question into view smoothly, or at once under reduced motion", () => {
    for (const [on, behavior] of [
      [false, "smooth"],
      [true, "auto"],
    ] as const) {
      reduced(on);
      const scroll = vi.fn();
      Element.prototype.scrollIntoView = scroll;
      const { unmount } = render(
        <Thread
          turns={[turn({ id: "1", fresh: true, status: "working", envelope: undefined })]}
          onAsk={() => {}}
          onOpenEvidence={() => {}}
          onOpenTrace={() => {}}
        />,
      );
      expect(scroll).toHaveBeenCalledWith({ block: "start", behavior });
      expect(scroll.mock.contexts[0]).toHaveClass("msg--user");
      unmount();
    }
  });

  it("lands a reopened conversation at its end in one jump, only for the newest turn", () => {
    reduced(false);
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(
      <Thread
        turns={[turn({ id: "1" }), turn({ id: "2", question: "Q2" })]}
        onAsk={() => {}}
        onOpenEvidence={() => {}}
        onOpenTrace={() => {}}
      />,
    );
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll).toHaveBeenCalledWith({ block: "end", behavior: "auto" });
    expect(scroll.mock.contexts[0]).toHaveAccessibleName("Question: Q2");
  });
});

describe("Thread reveal of a proposal's decision row", () => {
  const original = window.matchMedia;
  const PROPOSAL = getFixture("acme-001", "proposal");
  afterEach(() => {
    window.matchMedia = original;
    vi.restoreAllMocks();
  });

  function reduced(on: boolean) {
    window.matchMedia = ((query: string) => ({ ...original(query), matches: on && /reduce/.test(query) })) as typeof window.matchMedia;
  }

  /** A 400px scroller (y 0..400) over a composer (y 400..480); the decision row's bottom is at y 900 before any scroll. */
  function rig() {
    const rect = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) });
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      if (this.classList.contains("stage__main")) return rect(0, 400) as DOMRect;
      if (this.classList.contains("stage__composer")) return rect(400, 480) as DOMRect;
      if (this.classList.contains("msg--user")) return rect(120, 160) as DOMRect;
      if (this.classList.contains("msg__lead")) return rect(180, 230) as DOMRect;
      if (this.classList.contains("proposal__actions")) return rect(850, 900) as DOMRect;
      return rect(0, 0) as DOMRect;
    });
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
    return { scrollTo };
  }

  function mount(envelope = PROPOSAL.envelope) {
    return render(
      <div className="stage__main">
        <Thread
          turns={[turn({ id: "1", fresh: true, envelope })]}
          onAsk={() => {}}
          onOpenEvidence={() => {}}
          onOpenTrace={() => {}}
          decide={{ approve: vi.fn(), reject: vi.fn() } as never}
        />
        <div className="stage__composer" />
      </div>,
    );
  }

  it("scrolls so the Approve / Reject row ends above the composer, not the question at the top", () => {
    reduced(true);
    const { scrollTo } = rig();
    mount();
    expect(scrollTo).toHaveBeenCalled();
    const last = scrollTo.mock.calls.at(-1)![0] as ScrollToOptions;
    // The row's bottom (900) minus the band's bottom (400 - 16): 516. The question alone would have been 120 - 16 = 104.
    expect(last.top).toBe(900 - (400 - 16));
    expect(last.behavior).toBe("auto");
  });

  it("keeps the question at the top for a reply with no decision row", () => {
    reduced(false);
    const { scrollTo } = rig();
    mount(LOW.envelope);
    const last = scrollTo.mock.calls.at(-1)![0] as ScrollToOptions;
    expect(last.top).toBe(120 - 16);
    expect(last.behavior).toBe("smooth");
  });
});

describe("Thread motion", () => {
  afterEach(() => setReducedMotion(true));
  const handlers = { onAsk: vi.fn(), onOpenEvidence: vi.fn(), onOpenTrace: vi.fn() };

  it("shows the dot-matrix loader while a turn runs and none once the answer has landed", async () => {
    setReducedMotion(false);
    const running = [turn({ id: "1", status: "working", envelope: undefined, fresh: true })];
    const { container, rerender } = render(<Thread turns={running} {...handlers} />);
    expect(container.querySelector(".dotmatrix")).not.toBeNull();
    rerender(<Thread turns={[turn({ id: "1", fresh: true })]} {...handlers} />);
    // The loader fades for 160 ms, then the answer arrives in its place.
    await waitFor(() => expect(container.querySelector(".dotmatrix")).toBeNull(), { timeout: 2000 });
    expect(await screen.findByRole("article")).toHaveTextContent(LOW.envelope.headline);
    expect(container.querySelector(".working")).toBeNull();
  });

  it("swaps at once under reduced motion: no loader the moment the answer is there", () => {
    setReducedMotion(true);
    const running = [turn({ id: "1", status: "working", envelope: undefined, fresh: true })];
    const { container, rerender } = render(<Thread turns={running} {...handlers} />);
    expect(container.querySelector(".dotmatrix")).toHaveAttribute("data-still");
    rerender(<Thread turns={[turn({ id: "1", fresh: true })]} {...handlers} />);
    expect(container.querySelector(".dotmatrix")).toBeNull();
    expect(screen.getByRole("article")).toBeInTheDocument();
  });

  it("brings a new question in with a fade and a 6px rise, but not a reopened one", () => {
    setReducedMotion(false);
    const { container } = render(
      <Thread turns={[turn({ id: "1", fresh: true }), turn({ id: "2", question: "Older question" })]} {...handlers} />,
    );
    const bubbles = container.querySelectorAll<HTMLElement>(".msg--user");
    expect(bubbles[0].style.opacity).toBe("0");
    expect(bubbles[0].style.transform).toContain("translateY(6px)");
    expect(bubbles[1].style.opacity).not.toBe("0");
  });

  it("does not move a new question under reduced motion", () => {
    setReducedMotion(true);
    const { container } = render(<Thread turns={[turn({ id: "1", fresh: true })]} {...handlers} />);
    const bubble = container.querySelector<HTMLElement>(".msg--user") as HTMLElement;
    expect(bubble.style.opacity).not.toBe("0");
    expect(bubble.style.transform).not.toContain("translateY");
  });
});
