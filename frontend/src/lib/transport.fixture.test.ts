import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { chooseTransport, fixtureTransport, liveTransport } from "./transport";
import type { TraceEvent } from "./types";

const ask = async (t: ReturnType<typeof fixtureTransport>, q: string, onTrace: (e: TraceEvent) => void = () => {}) =>
  t.ask((await t.newConversation()).conversation_id, q, onTrace);

async function signedIn(user = "admin-acme") {
  const t = fixtureTransport(0);
  await t.signIn(user);
  return t;
}

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected the call to reject");
}

beforeEach(() => window.sessionStorage.clear());

describe("fixtureTransport", () => {
  it("lists the three demo companies and signs in", async () => {
    const t = fixtureTransport(0);
    const users = await t.demoUsers();
    expect(users.map((u) => u.company_id)).toEqual(["acme-001", "globex-002", "initech-003"]);
    expect(await t.session()).toBeNull();
    const session = await t.signIn(users[0].user);
    expect(session).toMatchObject({ company_id: "acme-001", role: "admin" });
    expect(await t.session()).toEqual(session);
    await t.signOut();
    expect(await t.session()).toBeNull();
    expect((await rejection(t.signIn("nobody"))).status).toBe(401);
  });

  it("replays the fixture trace through onTrace, then resolves with the stored answer", async () => {
    const t = await signedIn();
    const seen: TraceEvent[] = [];
    const envelope = await ask(t, "Which devices are low on disk space?", (e) => seen.push(e));
    expect(envelope.scope.company_id).toBe("acme-001");
    expect(seen.length).toBeGreaterThan(1);
    expect(seen.map((e) => e.seq)).toEqual([...seen.map((e) => e.seq)].sort((a, b) => a - b));
    expect(seen.every((e) => e.trace_id === envelope.trace_id)).toBe(true);
  });

  it("waits stepMs between replayed steps", async () => {
    vi.useFakeTimers();
    try {
      const t = fixtureTransport(380);
      await t.signIn("admin-acme");
      const seen: number[] = [];
      const done = t.ask("c", "Which devices are low on disk space?", (e) => seen.push(e.seq));
      await vi.advanceTimersByTimeAsync(0);
      expect(seen).toHaveLength(0);
      await vi.advanceTimersByTimeAsync(380);
      expect(seen).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(10_000);
      await done;
      expect(seen.length).toBeGreaterThan(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refuses to answer before sign-in", async () => {
    const t = fixtureTransport(0);
    expect((await rejection(t.newConversation())).status).toBe(401);
  });

  it("serves cited evidence from the stored answers and 404s on anything else", async () => {
    const t = await signedIn();
    const envelope = await ask(t, "Which devices are low on disk space?");
    const item = envelope.evidence[0];
    expect(await t.evidence(item.ref)).toMatchObject({ ref: item.ref, value: item.value });
    expect((await rejection(t.evidence("globex-002/X@2026-01-01T00:00:00Z#disk"))).status).toBe(404);
    const trace = await t.trace(envelope.trace_id);
    expect(trace.events.length).toBeGreaterThan(0);
  });

  it("approves a proposal with its hash, simulates the effect and refuses a second effect", async () => {
    const t = await signedIn();
    const { proposal } = await ask(t, "Open a ticket for the first one");
    expect(proposal).not.toBeNull();
    const card = proposal!;
    expect(await t.proposals("pending")).toHaveLength(1);

    const result = await t.approve(card.proposal_id, card.payload_hash);
    expect(result.duplicate).toBe(false);
    expect(result.proposal.status).toBe("executed");
    expect(result.effect).toMatchObject({ simulated: true, check_id: card.scope.check_id });
    expect(typeof result.effect?.effect_id).toBe("number");
    expect(await t.proposals("pending")).toHaveLength(0);

    const again = await t.approve(card.proposal_id, card.payload_hash);
    expect(again).toMatchObject({ duplicate: true, effect: null });
    expect(again.proposal.status).toBe("executed");
    expect((await t.reject(card.proposal_id).catch((e) => e)).detail).toBe("proposal is executed");
  });

  it("refuses a changed hash with 409 'proposal changed' and executes nothing", async () => {
    const t = await signedIn();
    const { proposal } = await ask(t, "Open a ticket for the first one");
    const error = await rejection(t.approve(proposal!.proposal_id, "not-the-hash"));
    expect(error.status).toBe(409);
    expect(error.detail).toBe("proposal changed");
    expect((await t.proposal(proposal!.proposal_id)).status).toBe("pending");
  });

  it("rejects a pending proposal once and then reports its status", async () => {
    const t = await signedIn();
    const { proposal } = await ask(t, "Open a ticket for the first one");
    const { proposal: rejected } = await t.reject(proposal!.proposal_id);
    expect(rejected.status).toBe("rejected");
    const error = await rejection(t.approve(proposal!.proposal_id, proposal!.payload_hash));
    expect(error).toMatchObject({ status: 409, detail: "proposal is rejected" });
  });

  it("404s an unknown proposal and keeps companies apart", async () => {
    const t = await signedIn();
    const { proposal } = await ask(t, "Open a ticket for the first one");
    expect((await rejection(t.approve("prp_nope", "x"))).status).toBe(404);
    await t.signIn("admin-globex");
    expect((await rejection(t.proposal(proposal!.proposal_id))).status).toBe(404);
    expect(await t.proposals()).toEqual([]);
  });

  it("records an audit trail in the live shape, newest first, filterable", async () => {
    const t = await signedIn();
    const { proposal } = await ask(t, "Open a ticket for the first one");
    await t.approve(proposal!.proposal_id, proposal!.payload_hash);
    const entries = await t.audit();
    expect(entries.map((e) => e.event)).toEqual([
      "proposal_executed",
      "proposal_approved",
      "proposal_created",
    ]);
    expect(entries[0]).toMatchObject({
      proposal_id: proposal!.proposal_id,
      target_device_id: proposal!.target_device_id,
      company_id: "acme-001",
    });
    expect(entries[0].detail).toHaveProperty("effect_id");
    expect(await t.audit({ proposal_id: "other" })).toEqual([]);
    expect(await t.audit({ target: proposal!.target_device_id })).toHaveLength(3);
  });

  it("keeps the operator console behind the operator session", async () => {
    const t = await signedIn();
    const { proposal } = await ask(t, "Open a ticket for the first one");
    await t.approve(proposal!.proposal_id, proposal!.payload_hash);
    expect((await rejection(t.observeTurns())).status).toBe(403);

    expect((await rejection(t.signIn("operator"))).status).toBe(401); // passcode required
    await t.signIn("operator", "demo");
    expect((await rejection(t.newConversation())).status).toBe(403);
    expect(await t.observeTurns()).toHaveLength(1);
    expect((await t.observeAudit({ company: "acme-001" })).length).toBe(3);
    expect(await t.observeAudit({ company: "globex-002" })).toEqual([]);
    expect((await t.observeEffects()).tickets).toEqual({ "acme-001": 1 });
    const [turn] = await t.observeTurns();
    expect((await t.observeTrace(turn.trace_id)).events.length).toBeGreaterThan(0);
  });
});

describe("the example evaluation run", () => {
  it("is labelled fixtures, operator-only and every case has a readable trace", async () => {
    const t = await signedIn();
    expect((await rejection(t.observeEvals())).status).toBe(403);
    await t.signIn("operator", "demo");
    const runs = await t.observeEvals();
    expect(runs).toHaveLength(1);
    expect(runs[0].model_mode).toBe("fixtures");
    expect(runs[0]).not.toHaveProperty("cases");
    const run = await t.observeEval(runs[0].run_id);
    expect(run.cases.map((c) => c.instance_id)).toEqual([
      "grounding.low_disk_acme#1",
      "actions.ticket_screen_lock_1lyssfd074bb#1",
      "guardrails.other_company_by_name_acme_asks_globex#1",
    ]);
    for (const c of run.cases) {
      expect((await t.observeTrace(c.trace_ids[0])).events.length).toBeGreaterThan(0);
    }
    expect((await rejection(t.observeEval("nope"))).status).toBe(404);
  });
});

describe("chooseTransport", () => {
  it("serves fixtures only when VITE_FLEET_DATA says so", () => {
    vi.stubEnv("VITE_FLEET_DATA", "fixtures");
    expect(chooseTransport().mode).toBe("fixtures");
    vi.stubEnv("VITE_FLEET_DATA", "");
    expect(chooseTransport()).toBe(liveTransport);
    vi.unstubAllEnvs();
  });
});

describe("fixtureTransport conversation history", () => {
  it("lists nothing before a first answer, then the conversation titled by its first question", async () => {
    const t = await signedIn();
    expect(await t.conversations()).toEqual([]);
    const { conversation_id } = await t.newConversation();
    expect(await t.conversations()).toEqual([]);
    await t.ask(conversation_id, "Which devices are low on disk space?", () => {});
    const listed = await t.conversations();
    expect(listed).toHaveLength(1);
    expect(listed[0]).toMatchObject({ conversation_id, title: "Which devices are low on disk space?", turns: 1 });
    expect(listed[0].updated_at).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
  });

  it("returns the stored turns in order and keeps the first question as the title", async () => {
    const t = await signedIn();
    const { conversation_id } = await t.newConversation();
    const first = await t.ask(conversation_id, "Which devices are low on disk space?", () => {});
    const second = await t.ask(conversation_id, "Which laptops show battery wear?", () => {});
    const stored = await t.conversation(conversation_id);
    expect(stored.title).toBe("Which devices are low on disk space?");
    expect(stored.turns.map((turn) => turn.seq)).toEqual([0, 1]);
    expect(stored.turns.map((turn) => turn.question)).toEqual([
      "Which devices are low on disk space?",
      "Which laptops show battery wear?",
    ]);
    expect(stored.turns[0].envelope).toEqual(first);
    expect(stored.turns[1].trace_id).toBe(second.trace_id);
    expect((await t.conversations())[0].turns).toBe(2);
  });

  it("lists the most recently active conversation first", async () => {
    const t = await signedIn();
    const a = (await t.newConversation()).conversation_id;
    const b = (await t.newConversation()).conversation_id;
    await t.ask(a, "Which devices are low on disk space?", () => {});
    await t.ask(b, "Which laptops show battery wear?", () => {});
    expect((await t.conversations()).map((c) => c.conversation_id)).toEqual([b, a]);
    await t.ask(a, "Which devices need attention?", () => {});
    expect((await t.conversations()).map((c) => c.conversation_id)).toEqual([a, b]);
    expect(await t.conversations(1)).toHaveLength(1);
  });

  it("trims a long first question to 60 characters at a word boundary", async () => {
    const t = await signedIn();
    const { conversation_id } = await t.newConversation();
    await t.ask(conversation_id, `Which devices are low on disk space ${"right now ".repeat(10)}`, () => {});
    const [listed] = await t.conversations();
    expect(listed.title.length).toBeLessThanOrEqual(60);
    expect(listed.title).toMatch(/\w$/);
  });

  it("deletes a conversation and then answers 404 for it, as for any unknown id", async () => {
    const t = await signedIn();
    const { conversation_id } = await t.newConversation();
    await t.ask(conversation_id, "Which devices are low on disk space?", () => {});
    await t.deleteConversation(conversation_id);
    expect(await t.conversations()).toEqual([]);
    expect((await rejection(t.conversation(conversation_id))).status).toBe(404);
    expect((await rejection(t.deleteConversation("never-existed"))).status).toBe(404);
  });

  it("keeps each company's history to itself", async () => {
    const t = await signedIn("admin-acme");
    const { conversation_id } = await t.newConversation();
    await t.ask(conversation_id, "Which devices are low on disk space?", () => {});
    await t.signIn("admin-globex");
    expect(await t.conversations()).toEqual([]);
    expect((await rejection(t.conversation(conversation_id))).status).toBe(404);
    expect((await rejection(t.deleteConversation(conversation_id))).status).toBe(404);
    await t.signIn("admin-acme");
    expect(await t.conversations()).toHaveLength(1);
  });

  it("refuses history to a signed-out caller and to the operator", async () => {
    const t = fixtureTransport(0);
    expect((await rejection(t.conversations())).status).toBe(401);
    await t.signIn("operator", "pass");
    expect((await rejection(t.conversations())).status).toBe(403);
  });
});
