import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "./App";
import { fixtureTransport } from "./lib/transport";
import { AnswerSheet } from "./components/AnswerSheet";
import { OUTCOMES } from "./lib/outcomes";
import { fixturesFor, getFixture } from "./lib/fixtures";

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
    await user.click(screen.getByRole("button", { name: "Which devices are low on disk space?" }));
    const headline = await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" });
    const sheet = headline.closest("article") as HTMLElement;
    expect(within(sheet).getByText("Answer")).toBeInTheDocument();
    expect(within(sheet).getByText(/Evaluated/)).toBeInTheDocument();
    expect(within(sheet).getByRole("list", { name: /low disk/i })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Conversation history" })).toBeInTheDocument();
  });

  it("opens the cited readings for a claim and the trace of the work", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices are low on disk space?");
    await user.click(await screen.findByRole("button", { name: /show evidence \(2\)/i }));
    const dialog = await screen.findByRole("dialog");
    const readings = within(dialog).getByRole("list", { name: "Cited readings" });
    expect(within(readings).getAllByRole("listitem")).toHaveLength(2);
    expect(within(readings).getByText("acme-001/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("tab", { name: /trace/i }));
    expect(within(dialog).getByRole("list", { name: "Steps taken" })).toBeInTheDocument();
    expect(within(dialog).getByText("Calling low_disk_devices")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("refuses another company's device in the same words as any unknown device", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Show the disk history for device 4SEFDLPTKXAM");
    const headline = await screen.findByRole("heading", { name: "I can't find that in your fleet." });
    const sheet = headline.closest("article") as HTMLElement;
    expect(within(sheet).getByText("Not found")).toBeInTheDocument();
    expect(within(sheet).queryByText(/globex/i)).not.toBeInTheDocument();
  });

  it("asks which check to run, and the options ask the real question", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices need attention?");
    await user.click(await screen.findByRole("button", { name: "Outdated macOS" }));
    expect(await screen.findByRole("heading", { name: /4 of 7 macOS devices/ })).toBeInTheDocument();
  });

  it("makes approval deliberate and labels the outcome as simulated", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    expect(await screen.findByText("Proposal for your decision")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Approve and execute (simulated)" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/recorded in tickets as entry 1 \(simulated\)/i);
    expect(screen.getAllByText("Executed (simulated)").length).toBeGreaterThan(0);
    expect(screen.queryByText("Proposal for your decision")).not.toBeInTheDocument();
    expect(screen.queryByText(/waits for your approval/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^Executed \(simulated\): Remediation ticket for acme-macbook-2\.$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("lets a proposal be rejected without confirmation and changes nothing", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Open a ticket for the first one");
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/rejected\. nothing was executed/i);
  });
});

describe("navigation", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("keeps Approvals and Audit in the sidebar and leaves the top bar to brand, company, as-of and Switch company", async () => {
    const user = await signInAs("Acme");
    await ask(user, "Which devices are low on disk space?");
    await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" });
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
    await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" });
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
  });

  it("sits the approvals and audit views on the quiet working ground", async () => {
    const user = await signInAs("Acme");
    expect(document.querySelector(".aurora")).toHaveAttribute("data-docked", "false");
    await user.click(within(screen.getByRole("navigation", { name: "Conversation history" })).getByRole("link", { name: "Audit" }));
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(document.querySelector(".aurora")).toHaveAttribute("data-docked", "true");
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
    await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" });
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await ask(user, "Which laptops show battery wear?");
    await screen.findAllByRole("article");
    expect(await transport.conversations()).toHaveLength(2);

    await user.click(await within(sidebar()).findByRole("button", { name: LOW_DISK }));
    expect(await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: LOW_DISK, level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Which laptops show battery wear?", level: 2 })).not.toBeInTheDocument();
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
    await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" });
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
    await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" });
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(await screen.findByRole("button", { name: "New chat" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(await screen.findByRole("button", { name: "Which devices are low on disk space?" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "2 of 10 devices have under 10% disk space free" })).toBeInTheDocument();
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
    await user.click(within(card).getByRole("button", { name: /evidence for this proposal/i }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("list", { name: "Cited readings" })).toBeInTheDocument();
    expect(within(drawer).queryByRole("tab", { name: /trace/i })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    expect(await within(card).findByRole("status")).toHaveTextContent(/rejected/i);
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

  it("fills the box from the suggestions menu without sending", async () => {
    const user = await signInAs("Acme");
    await user.click(screen.getByRole("button", { name: "Suggested questions" }));
    await user.click(screen.getByRole("button", { name: "Which laptops show battery wear?" }));
    expect(screen.getByLabelText(/ask a question about your fleet/i)).toHaveValue("Which laptops show battery wear?");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});

describe("outcome states", () => {
  it("renders every typed outcome with its own mark and word", () => {
    const seen = new Map<string, string>();
    for (const fixture of fixturesFor("acme-001")) {
      const { unmount, container } = render(
        <AnswerSheet envelope={fixture.envelope} onOpenEvidence={noop} onOpenTrace={noop} onAsk={noop} />,
      );
      const mark = container.querySelector(".mark") as HTMLElement;
      expect(mark.textContent).toBe(OUTCOMES[fixture.envelope.outcome].word);
      expect(mark.querySelector("svg")).not.toBeNull();
      seen.set(fixture.envelope.outcome, mark.textContent ?? "");
      unmount();
    }
    expect(seen.size).toBe(8);
    expect(new Set(seen.values()).size).toBe(8);
  });

  it("discloses coverage on every answer that has one", () => {
    const fixture = getFixture("acme-001", "macos_older_than_15");
    render(<AnswerSheet envelope={fixture.envelope} onOpenEvidence={noop} onOpenTrace={noop} onAsk={noop} />);
    expect(screen.getByText("Evaluated")).toBeInTheDocument();
    expect(screen.getByText("7 devices")).toBeInTheDocument();
    expect(screen.getByText("Excluded").nextElementSibling).toHaveTextContent(/\d+ out of scope/);
  });

  it("flags synthetic fixtures as simulated examples", () => {
    const fixture = getFixture("acme-001", "tool_error");
    render(
      <AnswerSheet
        envelope={fixture.envelope}
        note={fixture.meta.note}
        onOpenEvidence={noop}
        onOpenTrace={noop}
        onAsk={noop}
      />,
    );
    expect(screen.getByText(/simulated example/i)).toBeInTheDocument();
  });
});
