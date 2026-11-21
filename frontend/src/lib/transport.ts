/**
 * The only door between the UI and its data. `liveTransport` talks to the FastAPI backend on the same
 * origin; `fixtureTransport` replays the stored real answers and simulates decisions in memory, so the
 * app runs offline and under test. Nothing here knows about React.
 */
import { ApiError, del, getJson, postJson, send } from "./api";
import { routeQuestion } from "./fixtureRouter";
import { datasetAsOf, fixturesFor, TENANTS } from "./fixtures";
import { parseSse } from "./sse";
import type { AnswerEnvelope, ProposalCard, TraceEvent } from "./types";

export interface DemoUser {
  user: string;
  display_name: string;
  company_id: string;
}

export interface Session {
  company_id: string;
  display_name: string;
  role: "admin" | "operator";
}

export interface EvidenceValue {
  ref: string;
  collected_at: string;
  value: unknown;
}

export interface TraceView {
  trace_id: string;
  events: TraceEvent[];
}

export interface PolicyView {
  as_of: string;
  dataset_sha256: string;
  defaults: Record<string, number>;
}

export type ProposalStatus = ProposalCard["status"];

/** What an approved action wrote. Always simulated; the other fields depend on the action type. */
export interface Effect {
  effect_id: number;
  simulated: boolean;
  [field: string]: unknown;
}

export interface ApprovalResult {
  proposal: ProposalCard;
  /** Null when `duplicate` is true, or when the proposal was invalidated or failed on re-check. */
  effect: Effect | null;
  duplicate: boolean;
}

export interface RejectionResult {
  proposal: ProposalCard;
}

export type AuditEventName =
  | "proposal_created"
  | "proposal_refused"
  | "proposal_approved"
  | "proposal_rejected"
  | "proposal_executed"
  | "proposal_invalidated"
  | "proposal_failed"
  | "approval_refused"
  | "duplicate_approval";

export interface AuditEntry {
  seq: number;
  at: string;
  company_id: string;
  actor_id: string;
  event: AuditEventName;
  proposal_id: string | null;
  trace_id: string | null;
  action_type: string | null;
  target_device_id: string | null;
  payload_hash: string | null;
  refs: string[];
  detail: Record<string, unknown>;
}

export interface AuditFilters {
  proposal_id?: string;
  target?: string;
}

export interface ObserveAuditFilters {
  company?: string;
  actor?: string;
  target?: string;
}

export interface ObservedTurn {
  trace_id: string;
  company_id: string;
  actor_id: string;
  conversation_id: string;
  started_at: string;
  elapsed_ms: number;
  outcome: string;
  reason_code: string | null;
  model_mode: string;
}

/** Rows written per effect table (`tickets`, `replacement_flags`, ...), per company. */
export type EffectCounts = Record<string, Record<string, number>>;

export interface Transport {
  /** "live" talks to the API; "fixtures" is the offline replay with simulated decisions. */
  readonly mode: "live" | "fixtures";
  demoUsers(): Promise<DemoUser[]>;
  signIn(user: string, passcode?: string): Promise<Session>;
  /** The signed-in session, or null when nobody is signed in. */
  session(): Promise<Session | null>;
  signOut(): Promise<void>;
  newConversation(): Promise<{ conversation_id: string }>;
  /** Streams trace steps through `onTrace`, then resolves with the answer. Rejects with `ApiError`. */
  ask(
    conversationId: string,
    question: string,
    onTrace: (event: TraceEvent) => void,
  ): Promise<AnswerEnvelope>;
  evidence(ref: string): Promise<EvidenceValue>;
  trace(traceId: string): Promise<TraceView>;
  policy(): Promise<PolicyView>;
  proposals(status?: ProposalStatus): Promise<ProposalCard[]>;
  proposal(id: string): Promise<ProposalCard>;
  approve(id: string, payloadHash: string): Promise<ApprovalResult>;
  reject(id: string): Promise<RejectionResult>;
  audit(filters?: AuditFilters): Promise<AuditEntry[]>;
  observeTurns(limit?: number): Promise<ObservedTurn[]>;
  observeTrace(traceId: string): Promise<TraceView>;
  observeAudit(filters?: ObserveAuditFilters): Promise<AuditEntry[]>;
  observeEffects(): Promise<EffectCounts>;
}

const enc = encodeURIComponent;

export const liveTransport: Transport = {
  mode: "live",
  demoUsers: () => getJson("/api/demo-users"),
  signIn: (user, passcode) =>
    postJson("/api/session", passcode === undefined ? { user } : { user, passcode }),
  async session() {
    try {
      return await getJson<Session>("/api/session");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
      throw error;
    }
  },
  signOut: () => del("/api/session"),
  newConversation: () => postJson("/api/conversations"),
  async ask(conversationId, question, onTrace) {
    const response = await send("/api/chat", {
      method: "POST",
      body: { conversation_id: conversationId, question },
      headers: { accept: "text/event-stream" },
    });
    if (!response.body) throw new ApiError(0, "The answer stream could not be read.");
    let answer: AnswerEnvelope | null = null;
    try {
      for await (const { event, data } of parseSse(response.body)) {
        if (event === "trace") onTrace(data as TraceEvent);
        else if (event === "answer") answer = data as AnswerEnvelope;
        else if (event === "done") break;
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(0, "The answer stream was interrupted.");
    }
    if (answer === null) throw new ApiError(0, "The answer stream ended without an answer.");
    return answer;
  },
  evidence: (ref) => getJson("/api/evidence", { ref }),
  trace: (traceId) => getJson(`/api/traces/${enc(traceId)}`),
  policy: () => getJson("/api/policy"),
  proposals: (status) => getJson("/api/proposals", { status }),
  proposal: (id) => getJson(`/api/proposals/${enc(id)}`),
  approve: (id, payloadHash) =>
    postJson(`/api/proposals/${enc(id)}/approve`, { payload_hash: payloadHash }),
  reject: (id) => postJson(`/api/proposals/${enc(id)}/reject`),
  audit: (filters = {}) => getJson("/api/audit", { ...filters }),
  observeTurns: (limit) => getJson("/api/observe/turns", { limit: limit?.toString() }),
  observeTrace: (traceId) => getJson(`/api/observe/traces/${enc(traceId)}`),
  observeAudit: (filters = {}) => getJson("/api/observe/audit", { ...filters }),
  observeEffects: () => getJson("/api/observe/effects"),
};

// ---- fixtures ---------------------------------------------------------------------------------

const SESSION_KEY = "fleet-copilot.tenant";
const OPERATOR = "operator";
const EFFECT_TABLE: Record<string, string> = {
  remediation_ticket: "tickets",
  replacement_flag: "replacement_flags",
  upgrade_order: "upgrade_orders",
  employee_notification: "employee_notifications",
};

interface FixtureProposal {
  card: ProposalCard;
  actor_id: string;
}

function readStored(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function writeStored(user: string | null) {
  try {
    if (user) window.sessionStorage.setItem(SESSION_KEY, user);
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* session storage unavailable: a reload just signs out */
  }
}

const fixtureUsers: DemoUser[] = TENANTS.map((tenant) => ({
  user: `admin-${tenant.name.toLowerCase()}`,
  display_name: `${tenant.name} admin (demo)`,
  company_id: tenant.id,
}));

function wait(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

function notFound(): ApiError {
  return new ApiError(404, "not found");
}

function nowZ(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * The offline transport. Answers come from the stored real fixtures, trace steps replay with `stepMs`
 * between them (tests pass 0), and approve/reject are simulated in memory with the live result shapes.
 * Each instance has its own state; the signed-in user alone survives a reload via session storage, as
 * the previous fixture-only app did.
 */
export function fixtureTransport(stepMs = 380): Transport {
  let current: string | null = readStored();
  let conversations = 0;
  let effectId = 0;
  let auditSeq = 0;
  const proposals = new Map<string, FixtureProposal>();
  const audit: AuditEntry[] = [];
  const turns: ObservedTurn[] = [];
  const traces = new Map<string, TraceEvent[]>();
  const effects: EffectCounts = Object.fromEntries(
    Object.values(EFFECT_TABLE).map((table) => [table, {}]),
  );

  function sessionOf(user: string | null): Session | null {
    if (user === OPERATOR) {
      return { company_id: OPERATOR, display_name: "Operator (demo)", role: "operator" };
    }
    const match = fixtureUsers.find((u) => u.user === user);
    return match
      ? { company_id: match.company_id, display_name: match.display_name, role: "admin" }
      : null;
  }

  function who(role: "admin" | "operator" | "any" = "any"): { user: string; session: Session } {
    const session = sessionOf(current);
    if (!current || !session) throw new ApiError(401, "sign in first");
    if (role !== "any" && session.role !== role) throw new ApiError(403, "not allowed");
    return { user: current, session };
  }

  function log(
    entry: Pick<AuditEntry, "event" | "company_id" | "actor_id"> & Partial<AuditEntry>,
  ): void {
    audit.unshift({
      seq: ++auditSeq,
      at: nowZ(),
      proposal_id: null,
      trace_id: null,
      action_type: null,
      target_device_id: null,
      payload_hash: null,
      refs: [],
      detail: {},
      ...entry,
    });
  }

  function logProposal(
    event: AuditEventName,
    actor: string,
    card: ProposalCard,
    detail: Record<string, unknown> = {},
  ): void {
    log({
      event,
      company_id: card.company_id,
      actor_id: actor,
      proposal_id: card.proposal_id,
      action_type: card.action_type,
      target_device_id: card.target_device_id,
      payload_hash: card.payload_hash,
      refs: card.evidence_refs,
      detail,
    });
  }

  function decidable(id: string, actor: string): FixtureProposal {
    const { session } = who("admin");
    const found = proposals.get(id);
    if (!found || found.card.company_id !== session.company_id) {
      log({
        event: "approval_refused",
        company_id: session.company_id,
        actor_id: actor,
        proposal_id: id,
        detail: { reason: "not_found" },
      });
      throw notFound();
    }
    return found;
  }

  function effectFor(card: ProposalCard): Effect {
    const scope = card.scope;
    const fields: Record<string, unknown> =
      card.action_type === "remediation_ticket"
        ? { check_id: scope.check_id, note: card.rationale }
        : card.action_type === "replacement_flag"
          ? { reason: scope.reason }
          : card.action_type === "upgrade_order"
            ? { component: scope.component, current_spec: scope.current, target_spec: scope.target }
            : { employee_id: scope.employee, topic: scope.topic, message: scope.message };
    return { effect_id: ++effectId, simulated: true, ...fields };
  }

  function remember(envelope: AnswerEnvelope, user: string) {
    const card = envelope.proposal;
    if (!card || proposals.has(card.proposal_id)) return;
    proposals.set(card.proposal_id, { card: { ...card }, actor_id: user });
    logProposal("proposal_created", user, card);
    audit[0].trace_id = envelope.trace_id;
  }

  return {
    mode: "fixtures",
    async demoUsers() {
      return fixtureUsers.map((u) => ({ ...u }));
    },
    async signIn(user, passcode) {
      const session = sessionOf(user);
      if (!session) throw new ApiError(401, "sign-in failed");
      if (session.role === "operator" && !passcode) throw new ApiError(401, "sign-in failed");
      current = user;
      writeStored(user);
      return session;
    },
    async session() {
      return sessionOf(current);
    },
    async signOut() {
      current = null;
      writeStored(null);
    },
    async newConversation() {
      who("admin");
      return { conversation_id: `fixture-${++conversations}` };
    },
    async ask(conversationId, question, onTrace) {
      const { user, session } = who("admin");
      const started = Date.now();
      const fixture = routeQuestion(session.company_id, question);
      for (const event of fixture.trace) {
        if (event.kind !== "respond") await wait(stepMs);
        onTrace(event);
      }
      const envelope = fixture.envelope;
      traces.set(envelope.trace_id, fixture.trace);
      turns.unshift({
        trace_id: envelope.trace_id,
        company_id: session.company_id,
        actor_id: user,
        conversation_id: conversationId,
        started_at: nowZ(),
        elapsed_ms: Date.now() - started,
        outcome: envelope.outcome,
        reason_code: envelope.reason_code,
        model_mode: "fixtures",
      });
      remember(envelope, user);
      return envelope;
    },
    async evidence(ref) {
      const { session } = who("admin");
      for (const fixture of fixturesFor(session.company_id)) {
        const item = fixture.envelope.evidence.find((e) => e.ref === ref);
        if (item) {
          return { ref, collected_at: /@([^#]+)#/.exec(ref)?.[1] ?? "", value: item.value };
        }
      }
      throw notFound();
    },
    async trace(traceId) {
      const { session } = who("admin");
      for (const fixture of fixturesFor(session.company_id)) {
        if (fixture.envelope.trace_id === traceId) {
          return { trace_id: traceId, events: fixture.trace };
        }
      }
      throw notFound();
    },
    async policy() {
      who();
      return { as_of: datasetAsOf(), dataset_sha256: "fixtures", defaults: {} };
    },
    async proposals(status) {
      const { session } = who("admin");
      return [...proposals.values()]
        .map((p) => p.card)
        .filter((c) => c.company_id === session.company_id && (!status || c.status === status))
        .map((c) => ({ ...c }));
    },
    async proposal(id) {
      const { session } = who("admin");
      const found = proposals.get(id);
      if (!found || found.card.company_id !== session.company_id) throw notFound();
      return { ...found.card };
    },
    async approve(id, payloadHash) {
      const { user } = who("admin");
      const found = decidable(id, user);
      const { card } = found;
      if (card.status === "executed") {
        logProposal("duplicate_approval", user, card);
        return { proposal: { ...card }, effect: null, duplicate: true };
      }
      if (card.status !== "pending") {
        logProposal("approval_refused", user, card, {
          reason: `not_pending:${card.status}`,
          operation: "approve",
        });
        throw new ApiError(409, `proposal is ${card.status}`);
      }
      if (payloadHash !== card.payload_hash) {
        logProposal("approval_refused", user, card, {
          reason: "hash_mismatch",
          operation: "approve",
        });
        throw new ApiError(409, "proposal changed");
      }
      logProposal("proposal_approved", user, card);
      const effect = effectFor(card);
      card.status = "executed";
      const table = EFFECT_TABLE[card.action_type];
      effects[table][card.company_id] = (effects[table][card.company_id] ?? 0) + 1;
      logProposal("proposal_executed", user, card, { effect_id: effect.effect_id });
      return { proposal: { ...card }, effect, duplicate: false };
    },
    async reject(id) {
      const { user } = who("admin");
      const { card } = decidable(id, user);
      if (card.status !== "pending") {
        logProposal("approval_refused", user, card, {
          reason: `not_pending:${card.status}`,
          operation: "reject",
        });
        throw new ApiError(409, `proposal is ${card.status}`);
      }
      card.status = "rejected";
      logProposal("proposal_rejected", user, card);
      return { proposal: { ...card } };
    },
    async audit(filters = {}) {
      const { session } = who("admin");
      return audit.filter(
        (e) =>
          e.company_id === session.company_id &&
          (!filters.proposal_id || e.proposal_id === filters.proposal_id) &&
          (!filters.target || e.target_device_id === filters.target),
      );
    },
    async observeTurns(limit = 50) {
      who("operator");
      return turns.slice(0, limit);
    },
    async observeTrace(traceId) {
      who("operator");
      const events = traces.get(traceId);
      if (!events) throw notFound();
      return { trace_id: traceId, events };
    },
    async observeAudit(filters = {}) {
      who("operator");
      return audit.filter(
        (e) =>
          (!filters.company || e.company_id === filters.company) &&
          (!filters.actor || e.actor_id === filters.actor) &&
          (!filters.target || e.target_device_id === filters.target),
      );
    },
    async observeEffects() {
      who("operator");
      return structuredClone(effects);
    },
  };
}

/** `VITE_FLEET_DATA=fixtures` serves the offline replay; anything else talks to the API. */
export function chooseTransport(): Transport {
  return import.meta.env.VITE_FLEET_DATA === "fixtures" ? fixtureTransport() : liveTransport;
}
