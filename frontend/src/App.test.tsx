import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "./App";
import { fixtureTransport } from "./lib/transport";
import { AssistantMessage } from "./components/chat/AssistantMessage";
import { outcomeStyle } from "./lib/outcomes";
import { fixturesFor, getFixture } from "./lib/fixtures";
import { setFinePointer } from "./test/motion";

const noop = () => {};

async function signInAs(name: string) {
  const user = userEvent.setup();
  render(<App transport={fixtureTransport(0)} />);
  await user.click(await screen.findByRole("button", { name: new RegExp(name) }));
  return user;
}

async function ask(user: ReturnType<typeof userEvent.setup>, question: string) {
  await user.type(screen.getByLabelText(/ask a question about your fleet/i), `${question}{Enter}`);
}

beforeEach(() => window.sessionStorage.clear());

describe("sign-in and company boundary", () => {
  it("offers the three demo companies and shows the chosen one as a read-only badge", async () => {
    render(<App transport={fixtureTransport(0)} />);
    for (const name of ["Acme", "Globex", "Initech"]) {
      expect(await screen.findByRole("button", { name: new RegExp(name) })).toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole("button", { name: /Globex/ }));
    expect(screen.getByLabelText("Company: Globex")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
  });

  it("opens home with a mono label that names the pinned date, above the headline and the three starters", async () => {
    await signInAs("Acme");
    expect(document.querySelector(".hero__label")).toHaveTextContent("Fleet Copilot · As of 2026-06-12");
    expect(within(screen.getByRole("group", { name: "Suggested questions" })).getAllByRole("button")).toHaveLength(3);
  });

  it("draws the fleet pulse on home: a dot per device in the signed-in company, none for another company's", async () => {
    await signInAs("Globex");
    expect(await screen.findByRole("img", { name: /^8 devices in your fleet, \d+ need/ })).toBeInTheDocument();
    expect(document.querySelectorAll(".pulse__dot")).toHaveLength(8);
    expect(document.querySelector(".pulse__label")).toHaveTextContent(/8\s*devices/i);
  });

  it("answers only from the signed-in company's data", async () => {
    const user = await signInAs("Globex");
    await ask(user, "Which devices are low on disk space?");
    expect(await screen.findByText("2 of 8 devices have under 10% disk space free")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/acme-001|initech-003/);
  });

  it("returns to the sign-in screen when switching company", async () => {
    const user = await signInAs("Acme");
    await user.click(screen.getByRole("button", { name: /switch company/i }));
    expect(await screen.findByRole("button", { name: /Initech/ })).toBeInTheDocument();
  });
});

describe("the working thread", () => {
  it("docks into a thread on the first question and shows the answer first", async () => {
    const user = await signInAs("Acme");
    await user.click(screen.getByRole("button", { name: "Which devices are low on disk?" }));
    const headline = await screen.findByText("2 of 10 devices have under 10% disk space free");
    const reply = headline.closest("article") as HTMLElement;
    expect(within(reply).queryByText("Answer")).not.toBeInTheDocument();
    expect(within(reply).queryByText(/Evaluated/)).not.toBeInTheDocument();
    // A list answer is a table with its scope line and three folded disclosures, not a sentence and chips.
    expect(within(reply).getByRole("table", { name: "Low disk (<10% free)" })).toBeInTheDocument();
    expect(within(reply).getByText("10 devices checked · 8 healthy")).toBeInTheDocument();
    expect(within(reply).queryByRole("button", { name: "Details" })).not.toBeInTheDocument();
    expect(within(reply).getByRole("button", { name: /^Evidence 2 records$/ })).toHaveAttribute("aria-expanded", "false");
    expect(within(reply).queryByText(/MT7PJB7N5LRE \(acme-macbook-4;/)).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Conversation history" })).toBeInTheDocument();
  });

  it("opens the cited readings inline and the trace of the work in the drawer", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices are low on disk space?");
    // The two rows cite two readings; the readings the suggested actions rest on are not part of this answer's evidence.
    const cited = 2;
    expect(getFixture("acme-001", "low_disk").envelope.evidence.length).toBeGreaterThan(cited);
    await user.click(await screen.findByRole("button", { name: `Evidence ${cited} records` }));
    const readings = screen.getByRole("list", { name: "Readings behind this answer" });
    expect(within(readings).getAllByRole("listitem")).toHaveLength(cited);
    expect(within(readings).getAllByText("acme-macbook-4").length).toBeGreaterThan(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "How I got this" }));
    await user.click(screen.getByRole("button", { name: "Show work" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("list", { name: "Steps taken" })).toBeInTheDocument();
    expect(within(dialog).getByText("Calling low_disk_devices")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("refuses another company's device in the same words as any unknown device", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Show the disk history for device 4SEFDLPTKXAM");
    const reply = (await screen.findByText("I can't find that in your fleet.")).closest("article") as HTMLElement;
    expect(within(reply).getByText("Not found")).toBeInTheDocument();
    expect(within(reply).queryByText(/globex/i)).not.toBeInTheDocument();
  });

  it("asks which check to run, and the options ask the real question", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices need attention?");
    await user.click(await screen.findByRole("button", { name: "Outdated macOS" }));
    expect(await screen.findByText(/^4 of 7 macOS devices/)).toBeInTheDocument();
  });

  it("makes approval deliberate and labels the outcome as simulated", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    expect(await screen.findByText("Awaiting your approval")).toBeInTheDocument();
    expect(screen.getByText("I'd open a screen lock ticket for acme-macbook-2.")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is sent until you approve it\.$/)).toBeInTheDocument();
    const card = screen.getByRole("region", { name: "Proposed action" });
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(within(card).queryByRole("status")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Approve and execute (simulated)" }));
    expect(await within(card).findByRole("status")).toHaveTextContent(
      "Recorded in Tickets as entry 1. Nothing left this system.",
    );
    expect(document.querySelectorAll(".mark")).toHaveLength(1);
    expect(within(card).getByText("Executed (simulated)")).toBeInTheDocument();
    expect(screen.queryByText("Awaiting your approval")).not.toBeInTheDocument();
    expect(screen.queryByText(/waits for your approval/)).not.toBeInTheDocument();
    expect(screen.getByText("Executed (simulated): Remediation ticket for acme-macbook-2.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("answers 'what should I do?' with suggestion chips, and a chip asks for that action as the next message", async () => {
    const user = await signInAs("Acme");
    await ask(user, "What should I do?");
    const group = await screen.findByRole("group", { name: "Suggested actions" });
    const chips = within(group).getAllByRole("button");
    const part = (chip: HTMLElement, name: string) => chip.querySelector(`.chip__${name}`)?.textContent;
    expect(chips.map((chip) => [part(chip, "label"), part(chip, "device")])).toEqual([
      ["Order a storage upgrade (512 GiB → 1 TiB) for acme-macbook-4", undefined],
      ["Order a storage upgrade (1 TiB → 2 TiB) for acme-thinkpad-8", undefined],
    ]);
    expect(part(chips[0], "reason")).toBe("10.2 GiB free of 512.0 GiB (2.0% free) in the latest reading.");
    // The advice is the chips; the claims behind them wait under Details.
    expect(screen.queryByText(/^acme-macbook-4 \(MT7PJB7N5LRE\):/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(/Simulated example\. Built from the stored low-disk answer/)).toBeInTheDocument();

    setFinePointer();
    await user.click(chips[1]);
    expect(screen.getByRole("region", { name: "Question: Order a storage upgrade for acme-thinkpad-8" })).toBeInTheDocument();
    const card = await screen.findByRole("region", { name: "Proposed action" });
    expect(card).toHaveTextContent("Create upgrade order");
    expect(card).toHaveTextContent("acme-thinkpad-8");
    expect(card).toHaveTextContent("1 TiB → 2 TiB");
    expect(card).not.toHaveTextContent(/hardware/i);
    expect(chips[1]).toHaveAccessibleName(/\(asked\)/);
    expect(card).toHaveTextContent("8NM23J95R5I6");
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByLabelText(/ask a question about your fleet/i)).toHaveFocus();
  });

  it("lets a proposal be rejected without confirmation and changes nothing", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const card = screen.getByRole("region", { name: "Proposed action" });
    expect(await within(card).findByRole("status")).toHaveTextContent("Nothing was changed. The rejection is logged.");
    expect(within(card).getByText("Rejected")).toBeInTheDocument();
  });
});

describe("navigation", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("keeps Approvals and Audit in the sidebar and leaves the top bar to brand, company, as-of and Switch company", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices are low on disk space?");
    await screen.findByText("2 of 10 devices have under 10% disk space free");
    const bar = screen.getByRole("banner");
    const sidebar = screen.getByRole("navigation", { name: "Conversation history" });
    expect(within(bar).queryByRole("link")).not.toBeInTheDocument();
    expect(within(bar).getByLabelText("Company: Acme")).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Switch company" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("link", { name: "Approvals" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("link", { name: "Audit" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("button", { name: "New chat" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Approvals" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "Audit" })).toHaveLength(1);
  });

  it("shows the sidebar on the home state too, with Approvals and Audit reachable", async () => {
    await signInAs("Acme");
    const sidebar = screen.getByRole("navigation", { name: "Conversation history" });
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
    expect(within(sidebar).getByRole("link", { name: "Approvals" })).toHaveAttribute("href", "#/approvals");
    expect(within(sidebar).getByRole("link", { name: "Audit" })).toHaveAttribute("href", "#/audit");
    expect(await within(sidebar).findByText("Your conversations will appear here.")).toBeInTheDocument();
  });

  it("starts over from New chat: no thread, a fresh home", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices are low on disk space?");
    await screen.findByText("2 of 10 devices have under 10% disk space free");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
  });

  it("leaves the home hero behind when the approvals or audit view opens", async () => {
    const user = await signInAs("Acme");
    expect(document.querySelector(".hero")).not.toBeNull();
    await user.click(within(screen.getByRole("navigation", { name: "Conversation history" })).getByRole("link", { name: "Audit" }));
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(document.querySelector(".hero")).toBeNull();
  });
});

describe("conversation history", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  const sidebar = () => screen.getByRole("navigation", { name: "Conversation history" });
  const LOW_DISK = "Which devices are low on disk space?";

  it("lists an answered conversation under Today, marks it open, and refreshes after each turn", async () => {
    const user = await signInAs("Acme");
    await ask(user, LOW_DISK);
    const item = await within(sidebar()).findByRole("button", { name: LOW_DISK });
    expect(item).toHaveAttribute("aria-current", "true");
    expect(within(sidebar()).getByRole("list", { name: "Today" })).toContainElement(item);
    await ask(user, "Which laptops show battery wear?");
    await screen.findAllByRole("article");
    await waitFor(() => expect(within(sidebar()).getAllByRole("button", { name: LOW_DISK })).toHaveLength(1));
    expect(within(sidebar()).getAllByRole("button", { name: LOW_DISK })).toHaveLength(1);
  });

  it("reopens a stored conversation with its turns, and the next question continues it", async () => {
    const transport = fixtureTransport(0);
    const user = userEvent.setup();
    render(<App transport={transport} />);
    await user.click(await screen.findByRole("button", { name: /Acme/ }));
    await ask(user, LOW_DISK);
    await screen.findByText("2 of 10 devices have under 10% disk space free");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await ask(user, "Which laptops show battery wear?");
    await screen.findAllByRole("article");
    expect(await transport.conversations()).toHaveLength(2);

    await user.click(await within(sidebar()).findByRole("button", { name: LOW_DISK }));
    expect(await screen.findByText("2 of 10 devices have under 10% disk space free")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: `Question: ${LOW_DISK}` })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Question: Which laptops show battery wear?" })).not.toBeInTheDocument();
    expect(within(sidebar()).getByRole("button", { name: LOW_DISK })).toHaveAttribute("aria-current", "true");

    await ask(user, "Which devices need attention?");
    await screen.findByRole("button", { name: "Outdated macOS" });
    const listed = await transport.conversations();
    expect(listed).toHaveLength(2);
    expect(listed[0].title).toBe(LOW_DISK);
    expect(listed[0].turns).toBe(2);
  });

  it("opens a conversation from the approvals view by going back to the chat", async () => {
    const user = await signInAs("Acme");
    await ask(user, LOW_DISK);
    await user.click(within(sidebar()).getByRole("link", { name: "Audit" }));
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    await user.click(within(sidebar()).getByRole("button", { name: LOW_DISK }));
    expect(await screen.findByLabelText(/ask a question about your fleet/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Audit" })).not.toBeInTheDocument();
  });

  it("deletes a conversation after a confirmation, and an open one returns to a fresh home", async () => {
    const transport = fixtureTransport(0);
    const user = userEvent.setup();
    render(<App transport={transport} />);
    await user.click(await screen.findByRole("button", { name: /Acme/ }));
    await ask(user, LOW_DISK);
    await screen.findByText("2 of 10 devices have under 10% disk space free");
    await user.click(await within(sidebar()).findByRole("button", { name: `Delete conversation: ${LOW_DISK}` }));
    await user.click(within(sidebar()).getByRole("button", { name: "Cancel" }));
    expect(await transport.conversations()).toHaveLength(1);
    await user.click(within(sidebar()).getByRole("button", { name: `Delete conversation: ${LOW_DISK}` }));
    await user.click(within(sidebar()).getByRole("button", { name: "Delete" }));
    expect(await within(sidebar()).findByText("Your conversations will appear here.")).toBeInTheDocument();
    expect(await transport.conversations()).toEqual([]);
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("keeps each company's history to itself", async () => {
    const transport = fixtureTransport(0);
    const user = userEvent.setup();
    render(<App transport={transport} />);
    await user.click(await screen.findByRole("button", { name: /Acme/ }));
    await ask(user, LOW_DISK);
    await within(sidebar()).findByRole("button", { name: LOW_DISK });
    await user.click(screen.getByRole("button", { name: /switch company/i }));
    await user.click(await screen.findByRole("button", { name: /Globex/ }));
    expect(await within(sidebar()).findByText("Your conversations will appear here.")).toBeInTheDocument();
    expect(within(sidebar()).queryByRole("button", { name: LOW_DISK })).not.toBeInTheDocument();
  });
});

describe("the sidebar drawer on small screens", () => {
  const original = window.matchMedia;
  beforeEach(() => {
    window.location.hash = "";
    window.matchMedia = ((query: string) =>
      ({
        matches: true,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList) as typeof window.matchMedia;
  });
  afterEach(() => {
    window.matchMedia = original;
  });

  it("hides the sidebar behind a menu button, opens it as a dialog and closes it on Escape", async () => {
    const user = await signInAs("Acme");
    expect(screen.queryByRole("navigation", { name: "Conversation history" })).not.toBeInTheDocument();
    const menu = within(screen.getByRole("banner")).getByRole("button", { name: "Open menu" });
    await user.click(menu);
    const dialog = await screen.findByRole("dialog", { name: "Menu" });
    expect(within(dialog).getByRole("link", { name: "Approvals" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Audit" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("reopens a conversation from the drawer and closes it", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices are low on disk space?");
    await screen.findByText("2 of 10 devices have under 10% disk space free");
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(await screen.findByRole("button", { name: "New chat" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(await screen.findByRole("button", { name: "Which devices are low on disk space?" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("2 of 10 devices have under 10% disk space free")).toBeInTheDocument();
  });
});

describe("approvals", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("counts pending proposals in the navigation and clears the count after a decision", async () => {
    const user = await signInAs("Acme");
    const sidebar = screen.getByRole("navigation", { name: "Conversation history" });
    expect(within(sidebar).getByRole("link", { name: "Approvals" })).toBeInTheDocument();
    await ask(user, "Open a ticket for the first one");
    expect(await within(sidebar).findByRole("link", { name: "Approvals, 1 pending" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await within(sidebar).findByRole("link", { name: "Approvals" })).toBeInTheDocument();
  });

  it("opens the approvals view from the navigation and decides from there", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    const sidebar = screen.getByRole("navigation", { name: "Conversation history" });
    await user.click(await within(sidebar).findByRole("link", { name: "Approvals, 1 pending" }));
    expect(window.location.hash).toBe("#/approvals");
    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/ask a question about your fleet/i)).not.toBeInTheDocument();
    const card = await screen.findByRole("region", { name: "Proposed action" });
    expect(within(card).getByRole("list", { name: "Evidence" })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("Nothing was changed. The rejection is logged.");
    expect(within(card).getByText("Rejected")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /back to chat/i }));
    expect(await screen.findByLabelText(/ask a question about your fleet/i)).toBeInTheDocument();
  });

  it("goes straight to the approvals view for #/approvals, and leaves it on sign-out", async () => {
    window.location.hash = "#/approvals";
    const user = userEvent.setup();
    render(<App transport={fixtureTransport(0)} />);
    await user.click(await screen.findByRole("button", { name: /Acme/ }));
    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(await screen.findByText(/nothing is waiting for your decision/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /switch company/i }));
    expect(await screen.findByRole("button", { name: /Initech/ })).toBeInTheDocument();
    expect(window.location.hash).not.toBe("#/approvals");
  });
});

describe("the composer", () => {
  it("cannot send an empty question, sends on Enter, and keeps Shift+Enter for new lines", async () => {
    const user = userEvent.setup();
    render(<App transport={fixtureTransport(0)} />);
    await user.click(await screen.findByRole("button", { name: /Acme/ }));
    const send = screen.getByRole("button", { name: "Send question" });
    expect(send).toBeDisabled();
    const box = screen.getByLabelText(/ask a question about your fleet/i);
    await user.type(box, "hello{Shift>}{Enter}{/Shift}there");
    expect(box).toHaveValue("hello\nthere");
    expect(send).toBeEnabled();
  });

  it("has no static 'Ask' pill, and the suggestions button is a lightbulb titled 'Suggested questions'", async () => {
    await signInAs("Acme");
    const button = screen.getByRole("button", { name: "Suggested questions" });
    expect(button).toHaveAttribute("title", "Suggested questions");
    expect(button.querySelector("svg.lucide-lightbulb")).not.toBeNull();
    expect(document.querySelector(".mode")).toBeNull();
  });

  it("asks for a follow-up once docked, with the box, the suggestions button and send in one row", async () => {
    const user = await signInAs("Acme");
    const box = screen.getByLabelText(/ask a question about your fleet/i);
    expect(box).toHaveAttribute("placeholder", "Ask about your fleet…");
    await ask(user, "Which devices are low on disk space?");
    await screen.findByText("2 of 10 devices have under 10% disk space free");
    expect(screen.getByLabelText(/ask a question about your fleet/i)).toHaveAttribute(
      "placeholder",
      "Ask a follow-up, or ask what to do…",
    );
    const form = document.querySelector(".composer") as HTMLElement;
    expect(form).toHaveAttribute("data-docked", "true");
    expect(within(form).getByRole("button", { name: "Suggested questions" })).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Send question" })).toBeInTheDocument();
  });

  it("fills the box from the suggestions menu without sending", async () => {
    const user = await signInAs("Acme");
    await user.click(screen.getByRole("button", { name: "Suggested questions" }));
    await user.click(screen.getByRole("button", { name: "Which laptops show battery wear?" }));
    expect(screen.getByLabelText(/ask a question about your fleet/i)).toHaveValue("Which laptops show battery wear?");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});

describe("outcome states", () => {
  it("gives every outcome except a plain answer its own mark and word", () => {
    const seen = new Map<string, string>();
    for (const fixture of fixturesFor("acme-001")) {
      const { unmount, container } = render(
        <AssistantMessage envelope={fixture.envelope} busy={false} onOpenEvidence={noop} onOpenTrace={noop} onAsk={noop} />,
      );
      const mark = container.querySelector(".msg__state .mark") as HTMLElement | null;
      if (fixture.envelope.outcome === "answer") {
        expect(mark).toBeNull();
      } else if (fixture.envelope.outcome === "proposal") {
        // The proposal card owns the state: no second mark above it.
        expect(mark).toBeNull();
        expect(container.querySelector(".proposal .mark")).toHaveTextContent("Awaiting your approval");
        seen.set("proposal", "Awaiting your approval");
      } else {
        expect(mark?.textContent).toBe(
          outcomeStyle(fixture.envelope.outcome, fixture.envelope.reason_code).word,
        );
        expect(mark?.querySelector("svg")).not.toBeNull();
        seen.set(fixture.envelope.outcome, mark?.textContent ?? "");
      }
      unmount();
    }
    expect(seen.size).toBe(7);
    expect(new Set(seen.values()).size).toBe(7);
  });

  it("discloses what was not checked, with a reason for each device, under a list answer", async () => {
    const fixture = getFixture("acme-001", "macos_older_than_15");
    render(<AssistantMessage envelope={fixture.envelope} busy={false} onOpenEvidence={noop} onOpenTrace={noop} onAsk={noop} />);
    const excluded = fixture.envelope.coverage?.excluded_devices ?? [];
    expect(excluded.length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: new RegExp(`^Not checked ${excluded.length} devices$`) }));
    const rows = within(screen.getByRole("list", { name: "Devices this check did not cover" })).getAllByRole("listitem");
    expect(rows).toHaveLength(excluded.length);
    for (const [index, device] of excluded.entries()) {
      expect(rows[index]).toHaveTextContent(device.label);
      expect(rows[index]).toHaveTextContent(device.reason_text);
    }
  });

  it("discloses coverage, under Details, on every answer that has no table", async () => {
    const fixture = getFixture("acme-001", "macos_older_than_15");
    const plain = { ...fixture.envelope, table: null, scope_line: null };
    render(<AssistantMessage envelope={plain} busy={false} onOpenEvidence={noop} onOpenTrace={noop} onAsk={noop} />);
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Evaluated")).toBeInTheDocument();
    expect(screen.getByText("7 devices")).toBeInTheDocument();
    expect(screen.getByText("Excluded").nextElementSibling).toHaveTextContent(/\d+ out of scope/);
  });

  it("flags synthetic fixtures as simulated examples", async () => {
    const fixture = getFixture("acme-001", "tool_error");
    render(
      <AssistantMessage
        envelope={fixture.envelope}
        note={fixture.meta.note}
        busy={false}
        onOpenEvidence={noop}
        onOpenTrace={noop}
        onAsk={noop}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(/simulated example/i)).toBeInTheDocument();
  });
});

describe("the proposal card in the app", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("puts Edit details in the composer, focused on a fine pointer, and creates nothing", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    const card = await screen.findByRole("region", { name: "Proposed action" });
    setFinePointer();
    await user.click(within(card).getByRole("button", { name: "Edit details" }));
    const box = screen.getByLabelText(/ask a question about your fleet/i) as HTMLTextAreaElement;
    expect(box.value).toBe("Change the proposal for acme-macbook-2: ");
    expect(box).toHaveFocus();
    // Nothing was sent: still one question, and the proposal is still waiting.
    expect(screen.getAllByRole("region", { name: /^Question:/ })).toHaveLength(1);
    expect(within(card).getByText("Awaiting your approval")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("leaves the text in the composer without stealing focus where there is no fine pointer", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    const card = await screen.findByRole("region", { name: "Proposed action" });
    window.matchMedia = ((query: string) =>
      ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList) as typeof window.matchMedia;
    await user.click(within(card).getByRole("button", { name: "Edit details" }));
    const box = screen.getByLabelText(/ask a question about your fleet/i) as HTMLTextAreaElement;
    expect(box.value).toBe("Change the proposal for acme-macbook-2: ");
    expect(box).not.toHaveFocus();
  });

  it("opens the Audit view filtered to the proposal from View in Audit", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const card = screen.getByRole("region", { name: "Proposed action" });
    await user.click(within(card).getByRole("link", { name: "View in Audit" }));
    expect(window.location.hash).toMatch(/^#\/audit\?proposal=/);
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    const id = decodeURIComponent(window.location.hash.split("proposal=")[1]);
    expect(await screen.findByLabelText("Proposal id")).toHaveValue(id);
    const list = await screen.findByRole("list", { name: "Audit entries" });
    // Only that proposal's entries: proposed, then rejected; the device leads by hostname with its serial quiet.
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getAllByText("acme-macbook-2").length).toBeGreaterThan(0);
    expect(within(list).getAllByText("1LYSSFD074BB").length).toBeGreaterThan(0);
  });

  it("folds next steps beyond three behind '+N more', and opens them from the keyboard", async () => {
    const user = userEvent.setup();
    render(<App transport={fixtureTransport(0)} />);
    await user.click(await screen.findByRole("button", { name: /Initech/ }));
    await ask(user, "Which Macs run a macOS version older than 15?");
    const group = await screen.findByRole("group", { name: "Suggested actions" });
    expect(within(group).getAllByRole("button", { name: /^Open an? .* ticket for initech-/ })).toHaveLength(3);
    const more = within(group).getByRole("button", { name: "+1 more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    more.focus();
    await user.keyboard("{Enter}");
    expect(within(group).getAllByRole("button", { name: /^Open an? .* ticket for initech-/ })).toHaveLength(4);
    expect(within(group).getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
  });
});
