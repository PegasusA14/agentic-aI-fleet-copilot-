import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getFixture } from "../../lib/fixtures";
import type { AnswerEnvelope } from "../../lib/types";
import { AssistantMessage } from "./AssistantMessage";
import { COPIED_MS } from "./ReplyTools";

const MACOS = { ...getFixture("initech-003", "macos_older_than_15").envelope, reply: "", explanation: null };
const LOW_DISK = { ...getFixture("acme-001", "low_disk").envelope, reply: "", explanation: null };
const QUESTION = "Which Macs need a macOS update?";

function setup(envelope: AnswerEnvelope, extra: Partial<React.ComponentProps<typeof AssistantMessage>> = {}) {
  const handlers = { onOpenEvidence: vi.fn(), onOpenTrace: vi.fn(), onAsk: vi.fn() };
  const user = userEvent.setup();
  const view = render(<AssistantMessage envelope={envelope} busy={false} question={QUESTION} {...handlers} {...extra} />);
  return { user, ...handlers, ...view };
}

const disclosure = (name: RegExp | string) => screen.getByRole("button", { name });

describe("a list answer", () => {
  it("leads with the headline, then one supporting sentence, and not the sentence that lists every serial", () => {
    setup(MACOS);
    const lead = document.querySelector(".msg__lead") as HTMLElement;
    expect(lead).toHaveAttribute("data-headline");
    expect(lead).toHaveTextContent(MACOS.headline);
    expect(screen.getByText(MACOS.summary)).toHaveClass("msg__support");
    expect(screen.queryByText(MACOS.claims[0].text)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("DARFCP0BQM2G (initech-macbook-6; macOS 14.5)");
    expect(document.querySelector(".devchips, .msg__claim, .msg__claims")).toBeNull();
  });

  it("shows the headline even when the model wrote an explanation, and prefers the composer's summary under it", () => {
    setup({ ...MACOS, explanation: "Four of five Macs are behind. Please update them." });
    expect(document.querySelector(".msg__lead")).toHaveTextContent(MACOS.headline);
    expect(screen.getByText(MACOS.summary)).toBeInTheDocument();
    expect(screen.queryByText(/Four of five Macs/)).not.toBeInTheDocument();
  });

  it("falls back to the first sentence of the explanation when there is no summary", () => {
    setup({ ...MACOS, summary: "", explanation: "Four of five Macs are behind. Please update them." });
    expect(screen.getByText("Four of five Macs are behind.")).toHaveClass("msg__support");
  });

  it("renders the table, the scope line, and no Details toggle", () => {
    setup(MACOS);
    expect(screen.getByRole("table", { name: "Macs behind macOS 15" })).toBeInTheDocument();
    expect(screen.getByText("5 Macs checked · 1 on macOS 15 or later · 2 not checked")).toBeInTheDocument();
    expect(screen.getByText("Initech · latest snapshot per device · as of 2026-06-12 11:00 UTC")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Details" })).not.toBeInTheDocument();
  });

  it("keeps the old serial-by-serial note out of the reply, since Not checked says it with reasons", () => {
    setup(MACOS);
    expect(screen.queryByRole("list", { name: "Notes" })).not.toBeInTheDocument();
    expect(screen.queryByText(/are out of scope for this check/)).not.toBeInTheDocument();
  });
});

describe("the disclosures", () => {
  it("starts with all three folded, each a button that says what is inside", () => {
    setup(MACOS);
    for (const name of [/^Evidence 4 records$/, /^Not checked 2 devices$/, /^How I got this$/]) {
      expect(disclosure(name)).toHaveAttribute("aria-expanded", "false");
    }
    expect(screen.queryByRole("list", { name: "Readings behind this answer" })).not.toBeInTheDocument();
  });

  it("folds each one open and shut on a panel that is inert while closed", async () => {
    const { user } = setup(MACOS);
    const toggle = disclosure(/^Evidence/);
    const panel = document.getElementById(toggle.getAttribute("aria-controls") as string) as HTMLElement;
    expect(panel).toHaveAttribute("inert");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("inert");
    await user.click(toggle);
    expect(panel).toHaveAttribute("inert");
  });

  it("lists the evidence as numbered rows: hostname, what was read, when, with no raw reference", async () => {
    const { user } = setup(MACOS);
    await user.click(disclosure(/^Evidence/));
    const rows = within(screen.getByRole("list", { name: "Readings behind this answer" })).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("1");
    expect(rows[0]).toHaveTextContent("initech-macbook-2");
    expect(rows[0]).toHaveTextContent("OS version 13.6");
    expect(rows[0]).toHaveTextContent("latest snapshot");
    expect(rows[3]).toHaveTextContent("initech-mac-4");
    expect(document.body.textContent).not.toMatch(/initech-003\/[A-Z0-9]+@/);
  });

  it("lists what was not checked with each device's hostname, serial and reason", async () => {
    const { user } = setup(MACOS);
    await user.click(disclosure(/^Not checked/));
    const rows = within(screen.getByRole("list", { name: "Devices this check did not cover" })).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("initech-dell-1");
    expect(rows[0]).toHaveTextContent("W2656QDAE2ZJ");
    expect(rows[0]).toHaveTextContent("Windows 11, not a Mac");
    expect(rows[1]).toHaveTextContent("initech-dell-5");
    expect(rows[1]).toHaveTextContent("Windows 11, not a Mac");
  });

  it("has no Not checked row when every device was checked", () => {
    setup(LOW_DISK);
    expect(screen.queryByRole("button", { name: /^Not checked/ })).not.toBeInTheDocument();
    expect(disclosure(/^Evidence 2 records$/)).toBeInTheDocument();
  });

  it("explains how it was got: the rule, how it was counted, and Show work opens the trace", async () => {
    const { user, onOpenTrace } = setup(MACOS);
    await user.click(disclosure(/^How I got this/));
    expect(screen.getByText(MACOS.scope.rule as string)).toBeInTheDocument();
    expect(screen.getByText(MACOS.scope.calculation as string)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show work" }));
    expect(onOpenTrace).toHaveBeenCalledTimes(1);
  });

  it("opens evidence from a Source chip and lights the row it numbers, without using the drawer", async () => {
    const { user, onOpenEvidence } = setup(MACOS);
    await user.click(screen.getByRole("button", { name: "Source 3 for initech-macbook-6" }));
    expect(disclosure(/^Evidence/)).toHaveAttribute("aria-expanded", "true");
    const rows = within(screen.getByRole("list", { name: "Readings behind this answer" })).getAllByRole("listitem");
    expect(rows.filter((row) => row.hasAttribute("data-hot"))).toEqual([rows[2]]);
    expect(rows[2]).toHaveTextContent("initech-macbook-6");
    expect(rows[2]).toHaveFocus();
    expect(onOpenEvidence).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Source 1 for initech-macbook-2" }));
    expect(within(screen.getByRole("list", { name: "Readings behind this answer" })).getAllByRole("listitem")[0]).toHaveAttribute("data-hot");
  });
});

describe("next steps", () => {
  const ACTION = {
    action_type: "remediation_ticket" as const,
    device_id: "SPKRRDF0L786",
    target_label: "initech-macbook-2",
    label: "Open an update ticket for initech-macbook-2",
    reason: "macOS 13.6 is two major versions behind.",
    request: "Open a ticket for initech-macbook-2",
    refs: [MACOS.evidence[2].ref] as [string],
  };
  const WITH_ACTIONS: AnswerEnvelope = {
    ...MACOS,
    suggested_actions: [ACTION, { ...ACTION, device_id: "XZYK11QTPWEC", target_label: "initech-macbook-7", label: "Notify the owner of initech-macbook-7", request: "Notify the owner of initech-macbook-7", action_type: "employee_notification" }],
  };

  it("labels the group and offers the follow-up questions as plain chips that send their request", async () => {
    const { user, onAsk } = setup(MACOS);
    expect(screen.getByText("Next steps")).toHaveClass("label");
    const group = screen.getByRole("group", { name: "Follow-up questions" });
    const chips = within(group).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual(MACOS.follow_ups.map((f) => f.label));
    for (const chip of chips) expect(chip.querySelector("svg")).toBeNull();
    await user.click(chips[0]);
    expect(onAsk).toHaveBeenCalledWith("Which devices were not checked, and why?");
  });

  it("puts a shield and 'Needs your approval' on every action, the first one leading", async () => {
    const { user, onAsk } = setup(WITH_ACTIONS);
    const group = screen.getByRole("group", { name: "Suggested actions" });
    const chips = within(group).getAllByRole("button");
    expect(chips).toHaveLength(2);
    for (const chip of chips) {
      expect(chip).toHaveTextContent("Needs your approval");
      expect(chip.querySelector(".chip__approval svg")).not.toBeNull();
    }
    expect(chips[0]).toHaveAttribute("data-primary");
    expect(chips[1]).not.toHaveAttribute("data-primary");
    // The label already names the device, so it is not named a second time.
    expect(chips[0].querySelector(".chip__device")).toBeNull();
    await user.click(chips[0]);
    expect(onAsk).toHaveBeenCalledWith("Open a ticket for initech-macbook-2");
    expect(chips[0]).not.toHaveAttribute("aria-pressed");
    expect(chips[0]).toHaveAccessibleName(/\(asked\)/);
  });

  it("puts the shield on an advice reply's chips too", () => {
    setup({ ...WITH_ACTIONS, table: null, scope_line: null });
    for (const chip of within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button")) {
      expect(chip).toHaveTextContent("Needs your approval");
    }
  });

  it("waits while a turn runs", () => {
    setup(WITH_ACTIONS, { busy: true });
    for (const chip of screen.getAllByRole("button", { name: /Open an update ticket|Notify the owner|Which devices were not|What should we do/ })) {
      expect(chip).toBeDisabled();
    }
  });

  it("shows no Next steps label when there is nothing to offer", () => {
    setup({ ...MACOS, follow_ups: [], suggested_actions: [] });
    expect(screen.queryByText("Next steps")).not.toBeInTheDocument();
  });
});

describe("copy and retry", () => {
  let writeText: ReturnType<typeof vi.fn>;
  /** userEvent installs its own clipboard when it is set up, so the stub goes in after `setup`. */
  function stubClipboard() {
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  }
  afterEach(() => vi.useRealTimers());

  it("copies the headline, the summary and the table as tab-separated text, then says Copied", async () => {
    const { user } = setup(MACOS);
    stubClipboard();
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    const text = writeText.mock.calls[0][0] as string;
    expect(text.split("\n").slice(0, 4)).toEqual([MACOS.headline, MACOS.summary, "", "Device\tmacOS\tBehind 15"]);
    expect(text).toContain("initech-macbook-2\t13.6\t2 major behind");
    expect(screen.getByText("Copied")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy answer" })).toHaveAttribute("data-copied");
  });

  it("copies the rows in the order the person sorted them", async () => {
    const { user } = setup(MACOS);
    stubClipboard();
    await user.click(screen.getByRole("button", { name: "Sort by: Oldest first" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Newest first" }));
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect((writeText.mock.calls[0][0] as string).split("\n")[4]).toBe("initech-macbook-6\t14.5\t1 major behind");
  });

  it("puts the copy icon back after a moment", async () => {
    vi.useFakeTimers();
    setup(MACOS);
    stubClipboard();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy answer" }));
    });
    expect(screen.getByRole("button", { name: "Copy answer" })).toHaveAttribute("data-copied");
    await act(async () => {
      vi.advanceTimersByTime(COPIED_MS + 50);
    });
    expect(screen.getByRole("button", { name: "Copy answer" })).not.toHaveAttribute("data-copied");
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });

  it("says so when the clipboard refuses", async () => {
    const { user } = setup(MACOS);
    stubClipboard();
    writeText.mockRejectedValue(new Error("denied"));
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect(screen.getByText("Could not copy")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy answer" })).not.toHaveAttribute("data-copied");
  });

  it("asks the same question again on Retry", async () => {
    const { user, onAsk } = setup(MACOS);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onAsk).toHaveBeenCalledWith(QUESTION);
  });

  it("disables Retry while a turn runs, and has none without a question", () => {
    const { unmount } = setup(MACOS, { busy: true });
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    unmount();
    setup(MACOS, { question: undefined });
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("gives both a name and a tooltip", () => {
    setup(MACOS);
    expect(screen.getByRole("button", { name: "Copy answer" })).toHaveAttribute("data-tip", "Copy answer");
    expect(screen.getByRole("button", { name: "Retry" })).toHaveAttribute("data-tip");
  });
});

describe("a repeated answer", () => {
  const REPEAT: AnswerEnvelope = { ...MACOS, repeat_of: "earlier-trace", trace_id: "later-trace" };

  it("is one card, not the whole answer again", () => {
    setup(REPEAT, { onJump: vi.fn() });
    expect(screen.getByText("Same answer as above. Nothing has changed since 2026-06-12 11:00 UTC.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(MACOS.headline)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Evidence/ })).not.toBeInTheDocument();
  });

  it("still offers the next steps", async () => {
    const { user, onAsk } = setup(REPEAT);
    await user.click(screen.getByRole("button", { name: "What should we do about these?" }));
    expect(onAsk).toHaveBeenCalledWith("What should we do about these devices?");
  });

  it("jumps to the earlier answer", async () => {
    const onJump = vi.fn();
    const { user } = setup(REPEAT, { onJump });
    await user.click(screen.getByRole("button", { name: "Jump to it" }));
    expect(onJump).toHaveBeenCalledTimes(1);
  });

  it("offers no jump when the earlier answer is not in the thread", () => {
    setup(REPEAT);
    expect(screen.queryByRole("button", { name: "Jump to it" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show again" })).toBeInTheDocument();
  });

  it("shows the whole answer again on request", async () => {
    const { user } = setup(REPEAT, { onJump: vi.fn() });
    await user.click(screen.getByRole("button", { name: "Show again" }));
    expect(screen.queryByText(/Same answer as above/)).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Macs behind macOS 15" })).toBeInTheDocument();
    expect(document.querySelector(".msg__lead")).toHaveTextContent(MACOS.headline);
  });
});
