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

describe("chooseTransport", () => {
  it("serves fixtures only when VITE_FLEET_DATA says so", () => {
    vi.stubEnv("VITE_FLEET_DATA", "fixtures");
    expect(chooseTransport().mode).toBe("fixtures");
    vi.stubEnv("VITE_FLEET_DATA", "");
    expect(chooseTransport()).toBe(liveTransport);
    vi.unstubAllEnvs();
  });
});
