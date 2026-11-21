import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ApiError } from "./lib/api";
import { getFixture } from "./lib/fixtures";
import type { DemoUser, Session, Transport } from "./lib/transport";

const FIXTURE = getFixture("acme-001", "low_disk");
const ENVELOPE = FIXTURE.envelope;
const STEPS = FIXTURE.trace.filter((event) => event.kind !== "respond");
const REFS = ENVELOPE.evidence.map((item) => item.ref);

const USERS: DemoUser[] = [
  { user: "admin-acme", display_name: "Acme admin (demo)", company_id: "acme-001" },
  { user: "admin-globex", display_name: "Globex admin (demo)", company_id: "globex-002" },
];
const ACME: Session = { company_id: "acme-001", display_name: "Acme admin (demo)", role: "admin" };
const OPERATOR: Session = { company_id: "operator", display_name: "Operator", role: "operator" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** A live-mode transport whose every method is a spy; anything the test does not set up throws. */
function fake(overrides: Partial<Transport> = {}): Transport {
  const unused = () => Promise.reject(new Error("unexpected transport call"));
  const base = {
    mode: "live",
    demoUsers: vi.fn(async () => USERS),
    signIn: vi.fn(async (user: string) => {
      const match = USERS.find((u) => u.user === user);
      return match ? { company_id: match.company_id, display_name: match.display_name, role: "admin" } : ACME;
    }),
    session: vi.fn(async () => null),
    signOut: vi.fn(async () => {}),
    newConversation: vi.fn(async () => ({ conversation_id: "conv-1" })),
    conversations: vi.fn(async () => []),
    ask: vi.fn(async (_id: string, _q: string, onTrace: (e: (typeof STEPS)[number]) => void) => {
      for (const event of FIXTURE.trace) onTrace(event);
      return ENVELOPE;
    }),
    evidence: vi.fn(async (ref: string) => ({
      ref,
      collected_at: "2026-06-12T08:39:00Z",
      value: { free_gb: 4, total_gb: 256 },
    })),
    trace: vi.fn(unused),
    policy: vi.fn(async () => ({ as_of: ENVELOPE.scope.as_of, dataset_sha256: "x", defaults: {} })),
    fleetSummary: vi.fn(async () => ({ as_of: ENVELOPE.scope.as_of, devices: 12, flagged: ["a", "b", "c"] })),
  };
  return { ...base, ...overrides } as unknown as Transport;
}

async function signedInApp(transport: Transport) {
  const user = userEvent.setup();
  render(<App transport={transport} />);
  await user.click(await screen.findByRole("button", { name: /Acme/ }));
  return user;
}

async function ask(user: ReturnType<typeof userEvent.setup>, question: string) {
  await user.type(screen.getByLabelText(/ask a question about your fleet/i), `${question}{Enter}`);
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("live sign-in", () => {
  it("lists the demo users from the API and signs in without storing anything in the browser", async () => {
    const transport = fake();
    render(<App transport={transport} />);
    expect(await screen.findByRole("button", { name: /Acme/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Globex/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Initech/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Globex/ }));
    expect(transport.signIn).toHaveBeenCalledWith("admin-globex");
    expect(await screen.findByLabelText("Company: Globex")).toBeInTheDocument();
    expect(window.sessionStorage.length).toBe(0);
    expect(window.localStorage.length).toBe(0);
  });

  it("restores a signed-in session from the cookie on load", async () => {
    render(<App transport={fake({ session: vi.fn(async () => ACME) })} />);
    expect(await screen.findByLabelText("Company: Acme")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Globex/ })).not.toBeInTheDocument();
  });

  it("signs in an operator with a passcode and lands on the observe route", async () => {
    const signIn = vi.fn(async () => OPERATOR);
    const observeTurns = vi.fn(async () => []);
    const user = userEvent.setup();
    render(<App transport={fake({ signIn, observeTurns })} />);
    await user.click(await screen.findByRole("button", { name: /Operator/ }));
    await user.type(screen.getByLabelText(/operator passcode/i), "open-sesame{Enter}");
    expect(signIn).toHaveBeenCalledWith("operator", "open-sesame");
    await waitFor(() => expect(window.location.hash).toBe("#/observe"));
    expect(screen.getByText("Operator")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Operator console" })).toBeInTheDocument();
    // An Operate surface: the console, not the home hero.
    expect(document.querySelector(".hero")).toBeNull();
    expect(observeTurns).toHaveBeenCalled();
    expect(screen.queryByLabelText(/ask a question about your fleet/i)).not.toBeInTheDocument();
  });

  it("shows the server's words under the passcode when operator sign-in is refused", async () => {
    const signIn = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(401, "sign-in failed"))
      .mockRejectedValueOnce(new ApiError(403, "operator sign-in is not enabled"));
    const user = userEvent.setup();
    render(<App transport={fake({ signIn })} />);
    await user.click(await screen.findByRole("button", { name: /Operator/ }));
    await user.type(screen.getByLabelText(/operator passcode/i), "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(/sign-in failed/i);
    await user.keyboard("{Enter}");
    expect(await screen.findByText(/operator sign-in is not enabled/i)).toBeInTheDocument();
    expect(window.location.hash).not.toBe("#/observe");
  });
});

describe("streamed chat", () => {
  it("shows each step as it arrives, then the reply", async () => {
    const gate = deferred<typeof ENVELOPE>();
    const transport = fake({
      ask: vi.fn(async (_id, _q, onTrace) => {
        onTrace(STEPS[0]);
        return gate.promise;
      }),
    });
    const user = await signedInApp(transport);
    await ask(user, "Which devices are low on disk space?");
    const working = await screen.findByText("Reading your question…");
    expect(screen.queryByText(STEPS[0].summary)).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    gate.resolve(ENVELOPE);
    expect(await screen.findByText(ENVELOPE.headline)).toBeInTheDocument();
    expect(working).not.toBeInTheDocument();
  });

  it("keeps suggestion chips from earlier replies disabled while the next turn runs", async () => {
    const advice = {
      ...ENVELOPE,
      suggested_actions: [
        {
          action_type: "upgrade_order",
          device_id: "MT7PJB7N5LRE",
          target_label: "acme-macbook-4",
          label: "Order a storage upgrade (512 GiB → 1 TiB)",
          reason: "2.0% free of 512.0 GiB.",
          request: "Order a storage upgrade for acme-macbook-4",
          refs: [REFS[0]],
        },
      ],
    };
    const gate = deferred<typeof ENVELOPE>();
    const ask1 = vi.fn().mockResolvedValueOnce(advice).mockImplementationOnce(() => gate.promise);
    const transport = fake({ ask: ask1 });
    const user = await signedInApp(transport);
    await ask(user, "What should I do?");
    const chip = await screen.findByRole("button", { name: /Order a storage upgrade/ });
    expect(chip).toBeEnabled();
    await user.click(chip);
    expect(ask1).toHaveBeenLastCalledWith("conv-1", "Order a storage upgrade for acme-macbook-4", expect.any(Function));
    expect(await screen.findByText("Reading your question…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Order a storage upgrade/ })).toBeDisabled();
    gate.resolve(ENVELOPE);
    await waitFor(() => expect(screen.getByRole("button", { name: /Order a storage upgrade/ })).toBeEnabled());
  });

  it("keeps one conversation per session and starts a fresh one from New chat", async () => {
    const newConversation = vi
      .fn()
      .mockResolvedValueOnce({ conversation_id: "conv-1" })
      .mockResolvedValueOnce({ conversation_id: "conv-2" });
    const transport = fake({ newConversation });
    const user = await signedInApp(transport);
    await ask(user, "Which devices are low on disk space?");
    await screen.findByText(ENVELOPE.headline);
    await ask(user, "And which have old macOS?");
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
    expect(newConversation).toHaveBeenCalledTimes(1);
    expect(transport.ask).toHaveBeenLastCalledWith("conv-1", "And which have old macOS?", expect.any(Function));

    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
    await ask(user, "Which devices are low on disk space?");
    await screen.findByText(ENVELOPE.headline);
    expect(newConversation).toHaveBeenCalledTimes(2);
    expect(transport.ask).toHaveBeenLastCalledWith("conv-2", expect.any(String), expect.any(Function));
  });

  it("loads the history after sign-in and reloads it after each answered turn", async () => {
    const summary = (n: number) => ({
      conversation_id: "conv-1",
      title: "Which devices are low on disk space?",
      updated_at: new Date().toISOString(),
      turns: n,
    });
    const conversations = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([summary(1)]);
    const transport = fake({ conversations });
    const user = await signedInApp(transport);
    expect(await screen.findByText("Your conversations will appear here.")).toBeInTheDocument();
    await ask(user, "Which devices are low on disk space?");
    const nav = screen.getByRole("navigation", { name: "Conversation history" });
    const item = await within(nav).findByRole("button", { name: "Which devices are low on disk space?" });
    expect(item).toHaveAttribute("aria-current", "true");
    expect(conversations).toHaveBeenCalledTimes(2);
  });

  it("reopens a stored conversation and sends the next question into the same conversation id", async () => {
    const stored = {
      conversation_id: "old-7",
      title: "Which devices are low on disk space?",
      turns: [
        { seq: 0, question: "Which devices are low on disk space?", envelope: ENVELOPE, trace_id: ENVELOPE.trace_id, created_at: "2026-11-20T10:00:00Z" },
      ],
    };
    const list = [{ conversation_id: "old-7", title: stored.title, updated_at: new Date().toISOString(), turns: 1 }];
    const conversation = vi.fn(async () => stored);
    const transport = fake({ conversations: vi.fn(async () => list), conversation });
    const user = await signedInApp(transport);
    const nav = screen.getByRole("navigation", { name: "Conversation history" });
    await user.click(await within(nav).findByRole("button", { name: stored.title }));
    expect(conversation).toHaveBeenCalledWith("old-7");
    expect(await screen.findByText(ENVELOPE.headline)).toBeInTheDocument();
    expect(transport.newConversation).not.toHaveBeenCalled();
    await ask(user, "And which have old macOS?");
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
    expect(transport.ask).toHaveBeenLastCalledWith("old-7", "And which have old macOS?", expect.any(Function));
    expect(transport.newConversation).not.toHaveBeenCalled();
  });

  it("says so, and refreshes the list, when a conversation to reopen is gone", async () => {
    const list = [{ conversation_id: "old-7", title: "Gone chat", updated_at: new Date().toISOString(), turns: 1 }];
    const conversations = vi.fn().mockResolvedValueOnce(list).mockResolvedValue([]);
    const conversation = vi.fn().mockRejectedValue(new ApiError(404, "not found"));
    const user = await signedInApp(fake({ conversations, conversation }));
    const nav = screen.getByRole("navigation", { name: "Conversation history" });
    await user.click(await within(nav).findByRole("button", { name: "Gone chat" }));
    expect(await screen.findByRole("status")).toHaveTextContent("That conversation is no longer available.");
    expect(await within(nav).findByText("Your conversations will appear here.")).toBeInTheDocument();
  });

  it("deletes through the transport and drops the row", async () => {
    const list = [{ conversation_id: "old-7", title: "Old chat", updated_at: new Date().toISOString(), turns: 1 }];
    const deleteConversation = vi.fn(async () => {});
    const user = await signedInApp(fake({ conversations: vi.fn(async () => list), deleteConversation }));
    const nav = screen.getByRole("navigation", { name: "Conversation history" });
    await user.click(await within(nav).findByRole("button", { name: "Delete conversation: Old chat" }));
    await user.click(within(nav).getByRole("button", { name: "Delete" }));
    expect(deleteConversation).toHaveBeenCalledWith("old-7");
    expect(await within(nav).findByText("Your conversations will appear here.")).toBeInTheDocument();
  });

  it("keeps the row and says so when the server refuses the delete", async () => {
    const list = [{ conversation_id: "old-7", title: "Old chat", updated_at: new Date().toISOString(), turns: 1 }];
    const deleteConversation = vi.fn().mockRejectedValue(new ApiError(500, "boom"));
    const user = await signedInApp(fake({ conversations: vi.fn(async () => list), deleteConversation }));
    const nav = screen.getByRole("navigation", { name: "Conversation history" });
    await user.click(await within(nav).findByRole("button", { name: "Delete conversation: Old chat" }));
    await user.click(within(nav).getByRole("button", { name: "Delete" }));
    expect(await within(nav).findByRole("alert")).toHaveTextContent("Could not delete that conversation.");
    await user.click(within(nav).getByRole("button", { name: "Cancel" }));
    expect(within(nav).getByRole("button", { name: "Old chat" })).toBeInTheDocument();
  });

  it("ends the session when the history read is refused with a 401", async () => {
    const conversations = vi.fn().mockRejectedValue(new ApiError(401, "sign in first"));
    await signedInApp(fake({ conversations }));
    expect(await screen.findByText("Your session ended. Sign in again.")).toBeInTheDocument();
  });

  it("puts a 422 under the composer in the server's words and leaves no empty turn", async () => {
    const ask422 = vi.fn().mockRejectedValue(new ApiError(422, "question is too long"));
    const user = await signedInApp(fake({ ask: ask422 }));
    await ask(user, "tell me everything");
    expect(await screen.findByRole("alert")).toHaveTextContent("question is too long");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(document.querySelector(".working")).toBeNull();
    expect(screen.getByLabelText(/ask a question about your fleet/i)).toHaveValue("tell me everything");
  });

  it("refuses a question over 2000 characters before docking, in the server's words", async () => {
    const transport = fake();
    const user = await signedInApp(transport);
    const box = screen.getByLabelText(/ask a question about your fleet/i);
    await user.click(box);
    await user.paste("x".repeat(2001));
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Questions are limited to 2000 characters.");
    expect(transport.ask).not.toHaveBeenCalled();
    expect(transport.newConversation).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "This session" })).not.toBeInTheDocument();
    expect(box).toHaveValue("x".repeat(2001));
  });

  it("accepts a question of exactly 2000 characters", async () => {
    const transport = fake();
    const user = await signedInApp(transport);
    await user.click(screen.getByLabelText(/ask a question about your fleet/i));
    await user.paste("y".repeat(2000));
    await user.keyboard("{Enter}");
    await screen.findByText(ENVELOPE.headline);
    expect(transport.ask).toHaveBeenCalledTimes(1);
  });

  it("shows a calm banner when the dataset is not loaded", async () => {
    const user = await signedInApp(fake({ ask: vi.fn().mockRejectedValue(new ApiError(503, "dataset unavailable")) }));
    await ask(user, "Which devices are low on disk space?");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "The dataset is not available. Run make load-data, then try again.",
    );
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("returns to sign-in with a notice when the session has ended", async () => {
    const user = await signedInApp(fake({ ask: vi.fn().mockRejectedValue(new ApiError(401, "sign in first")) }));
    await ask(user, "Which devices are low on disk space?");
    expect(await screen.findByText("Your session ended. Sign in again.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Globex/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Company: Acme")).not.toBeInTheDocument();
  });

  it("answers a network failure with a lookup-failed reply, not a raw error", async () => {
    const user = await signedInApp(fake({ ask: vi.fn().mockRejectedValue(new ApiError(0, "The copilot could not be reached.")) }));
    await ask(user, "Which devices are low on disk space?");
    const reply = (await screen.findByText("The copilot could not be reached.")).closest("article") as HTMLElement;
    expect(within(reply).getByText("Lookup failed")).toBeInTheDocument();
    expect(reply.querySelector("svg")).not.toBeNull();
  });
});

describe("evidence from the API", () => {
  it("fetches each cited reading once and renders the raw value as plain key/value text", async () => {
    const transport = fake();
    const user = await signedInApp(transport);
    await ask(user, "Which devices are low on disk space?");
    await user.click(await screen.findByRole("button", { name: "Details" }));
    await user.click(await screen.findByRole("button", { name: /^evidence \(/i }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(transport.evidence).toHaveBeenCalledTimes(REFS.length));
    for (const ref of REFS) expect(transport.evidence).toHaveBeenCalledWith(ref);
    const first = within(dialog).getAllByRole("listitem")[0];
    expect(await within(first).findByText("free_gb")).toBeInTheDocument();
    expect(within(first).getByText("256")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("tab", { name: /trace/i }));
    await user.click(within(dialog).getByRole("tab", { name: /evidence/i }));
    expect(transport.evidence).toHaveBeenCalledTimes(REFS.length);
  });

  it("never renders a reading as markup", async () => {
    const transport = fake({
      evidence: vi.fn(async (ref: string) => ({ ref, collected_at: "", value: "<img src=x onerror=alert(1)>" })),
    });
    const user = await signedInApp(transport);
    await ask(user, "Which devices are low on disk space?");
    await user.click(await screen.findByRole("button", { name: "Details" }));
    await user.click(await screen.findByRole("button", { name: /^evidence \(/i }));
    const dialog = await screen.findByRole("dialog");
    expect((await within(dialog).findAllByText("<img src=x onerror=alert(1)>")).length).toBeGreaterThan(0);
    expect(dialog.querySelector("img")).toBeNull();
  });

  it("shows the streamed steps in the trace tab without another request", async () => {
    const transport = fake();
    const user = await signedInApp(transport);
    await ask(user, "Which devices are low on disk space?");
    await user.click(await screen.findByRole("button", { name: "Details" }));
    await user.click(await screen.findByRole("button", { name: "Show work" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("list", { name: "Steps taken" })).toBeInTheDocument();
    expect(within(dialog).getByText("Calling low_disk_devices")).toBeInTheDocument();
    expect(transport.trace).not.toHaveBeenCalled();
  });
});

describe("the home fleet pulse in live mode", () => {
  it("draws the API's fleet summary on sign-in, with no answer needed", async () => {
    const transport = fake();
    await signedInApp(transport);
    expect(await screen.findByRole("img", { name: "12 devices in your fleet, 3 need attention" })).toBeInTheDocument();
    expect(document.querySelectorAll(".pulse__dot")).toHaveLength(12);
    expect(document.querySelectorAll(".pulse__dot[data-lit]")).toHaveLength(3);
    expect(document.querySelector(".pulse__label")).toHaveTextContent("12 devices · 3 need attention");
    expect(transport.fleetSummary).toHaveBeenCalledTimes(1);
  });

  it("reserves the block while the summary loads, then fills it without adding another", async () => {
    const pending = deferred<{ as_of: string; devices: number; flagged: string[] }>();
    await signedInApp(fake({ fleetSummary: vi.fn(() => pending.promise) }));
    const reserved = document.querySelector(".pulse");
    expect(reserved).toHaveAttribute("data-loading");
    expect(reserved).toBeEmptyDOMElement();
    expect(document.querySelector(".pulse__dot")).toBeNull();
    pending.resolve({ as_of: "x", devices: 4, flagged: [] });
    await waitFor(() => expect(document.querySelectorAll(".pulse__dot")).toHaveLength(4));
    expect(document.querySelectorAll(".pulse")).toHaveLength(1);
    expect(document.querySelector(".pulse")).not.toHaveAttribute("data-loading");
  });

  it("renders nothing when the summary fails", async () => {
    const fleetSummary = vi.fn(() => Promise.reject(new ApiError(500, "boom")));
    await signedInApp(fake({ fleetSummary }));
    await waitFor(() => expect(fleetSummary).toHaveBeenCalled());
    await waitFor(() => expect(document.querySelector(".pulse")).toBeNull());
    expect(screen.getByRole("heading", { name: "Ask your fleet anything." })).toBeInTheDocument();
  });
});
