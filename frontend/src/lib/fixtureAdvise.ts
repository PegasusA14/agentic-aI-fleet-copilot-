/**
 * Replay-mode stand-ins for the advise flow. The live agent recommends actions by running the eligibility rules;
 * the offline replay has no rules engine, so these answers are built from the stored real low-disk answer and
 * labelled as fixtures. They exist only so the chat can be reviewed without a backend.
 */
import type { SuggestedAction } from "../types/AnswerEnvelope";
import type { AnswerEnvelope, EntityList, ProposalCard, TraceEvent } from "./types";
import { type Fixture, getFixture } from "./fixtures";

const NOTE =
  "Built from the stored low-disk answer so the chat can be reviewed offline. The live copilot checks each action's eligibility rules instead.";

const ADVISE_ASK = /\b(what (should|can|do) (i|we) do|what do i do|recommend\w*|next steps?|how (do|can) i fix|any advice)\b/i;
const UPGRADE_ASK = /\border a storage upgrade for\s+(\S+)/i;

/** True for "what should I do?" and its everyday wordings. */
export function asksForAdvice(question: string): boolean {
  return ADVISE_ASK.test(question);
}

/** The device named by a storage-upgrade request, or null. */
export function upgradeTarget(question: string): string | null {
  return UPGRADE_ASK.exec(question)?.[1].replace(/[.?!]+$/, "") ?? null;
}

interface LowDisk {
  device_id: string;
  label: string;
  ref: string;
  /** `10.2 GiB free of 512.0 GiB (2.0% free)` */
  reading: string;
  current: string;
  target: string;
}

const GIB_PER_UNIT: Record<string, number> = { GiB: 1, TiB: 1024 };

function size(gib: number): string {
  return gib >= 1024 ? `${gib / 1024} TiB` : `${gib} GiB`;
}

function lowDiskDevices(tenantId: string): { devices: LowDisk[]; base: AnswerEnvelope } {
  const base = getFixture(tenantId, "low_disk").envelope;
  const items = base.entity_lists[0]?.items ?? [];
  const devices: LowDisk[] = [];
  for (const item of items) {
    const evidence = base.evidence.find((e) => e.ref.includes(`/${item.device_id}@`));
    const total = evidence ? /of ([\d.]+) (GiB|TiB)/.exec(evidence.value) : null;
    if (!evidence || !total) continue;
    const gib = Math.round(Number(total[1]) * GIB_PER_UNIT[total[2]]);
    devices.push({
      device_id: item.device_id,
      label: item.label,
      ref: evidence.ref,
      reading: evidence.value,
      current: size(gib),
      target: size(gib * 2),
    });
  }
  return { devices, base };
}

/** A stable stand-in for the payload hash; real hashes come from the server. */
function fixtureHash(text: string): string {
  let hash = 2166136261;
  let out = "";
  for (let round = 0; out.length < 64; round++) {
    for (const ch of `${round}:${text}`) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0;
    out += hash.toString(16).padStart(8, "0");
  }
  return out.slice(0, 64);
}

function trace(id: string, intent: string, tool: string, claims: number): TraceEvent[] {
  const at = (seq: number, kind: TraceEvent["kind"], summary: string, data: TraceEvent["data"], t: string | null = null) => ({
    seq,
    kind,
    summary,
    data,
    tool: t,
    latency_ms: null,
    trace_id: id,
  });
  return [
    at(0, "scope", "Turn started", {}),
    at(1, "plan", "Planned the turn", { intent }),
    at(2, "tool_call", `Calling ${tool}`, { args: {} }, tool),
    at(3, "tool_result", `${tool} returned`, { status: "ok" }, tool),
    at(4, "compose", "Composed the reply from tool records", { claims }),
    at(5, "respond", "Answer ready", { claims }),
  ];
}

/** Advice for every device in the tenant's stored low-disk answer: one storage upgrade each. */
export function adviseFixture(tenantId: string): Fixture | null {
  const { devices, base } = lowDiskDevices(tenantId);
  if (devices.length === 0) return null;
  const traceId = `replay-${tenantId}-advise`;
  const suggested = devices.map((d) => ({
    action_type: "upgrade_order" as const,
    device_id: d.device_id,
    target_label: d.label,
    label: `Order a storage upgrade (${d.current} → ${d.target})`,
    reason: `${d.reading} in the latest reading.`,
    request: `Order a storage upgrade for ${d.label}`,
    refs: [d.ref] as SuggestedAction["refs"],
  }));
  const envelope: AnswerEnvelope = {
    alternatives: [],
    follow_ups: [],
    repeat_of: null,
    scope_line: null,
    summary: "",
    table: null,
    claims: devices.map((d, i) => ({
      count: 1,
      device_ids: [d.device_id],
      refs: [d.ref],
      text: `${d.label} (${d.device_id}): ${suggested[i].label}. ${suggested[i].reason}`,
    })),
    clarification: null,
    coverage: base.coverage,
    entity_lists: [
      {
        list_id: `${traceId}/suggested_actions-1`,
        title: "Suggested actions",
        items: devices.map((d, i) => ({ device_id: d.device_id, label: d.label, rank: i + 1 })) as EntityList["items"],
      },
    ],
    evidence: base.evidence.filter((e) => devices.some((d) => d.ref === e.ref)),
    explanation: null,
    headline: `${devices.length} ${devices.length === 1 ? "action is" : "actions are"} supported by the evidence`,
    notes: [],
    outcome: "answer",
    proposal: null,
    reason_code: null,
    scope: {
      ...base.scope,
      rule: "A storage upgrade is offered for a device with under 10% disk space free at the latest snapshot",
      calculation: null,
    },
    suggested_actions: suggested,
    trace_id: traceId,
  };
  return {
    meta: {
      scenario: "advise",
      tenant: tenantId,
      question: "What should I do?",
      keywords: [],
      synthetic: true,
      note: NOTE,
    },
    envelope,
    trace: trace(traceId, "advise", "recommend_actions", devices.length),
  };
}

/** The proposal a storage-upgrade chip leads to, for one of the devices the advice named. */
export function upgradeFixture(tenantId: string, name: string): Fixture | null {
  const { devices, base } = lowDiskDevices(tenantId);
  const d = devices.find((x) => x.label.toLowerCase() === name.toLowerCase() || x.device_id === name);
  if (!d) return null;
  const traceId = `replay-${tenantId}-upgrade-${d.device_id}`;
  const scope = { device: d.device_id, component: "storage", current: d.current, target: d.target };
  const proposal: ProposalCard = {
    action_type: "upgrade_order",
    company_id: tenantId,
    evidence_refs: [d.ref],
    expected_effect: `A storage upgrade order from ${d.current} to ${d.target} for ${d.label} would be recorded (simulated)`,
    payload_hash: fixtureHash(JSON.stringify(scope) + tenantId),
    proposal_id: `prop-${traceId}`,
    rationale: `${d.reading} in the latest reading.`,
    scope,
    status: "pending",
    target_device_id: d.device_id,
    target_label: d.label,
  };
  const envelope: AnswerEnvelope = {
    alternatives: [],
    follow_ups: [],
    repeat_of: null,
    scope_line: null,
    summary: "",
    table: null,
    claims: [],
    clarification: null,
    coverage: null,
    entity_lists: [],
    evidence: base.evidence.filter((e) => e.ref === d.ref),
    explanation: null,
    headline: `Proposed: order a storage upgrade for ${d.label}. It waits for your approval; nothing has changed yet.`,
    notes: [],
    outcome: "proposal",
    proposal,
    reason_code: null,
    scope: { ...base.scope, rule: null, calculation: null, population: null },
    suggested_actions: [],
    trace_id: traceId,
  };
  return {
    meta: {
      scenario: "upgrade",
      tenant: tenantId,
      question: `Order a storage upgrade for ${d.label}`,
      keywords: [],
      synthetic: true,
      note: NOTE,
    },
    envelope,
    trace: trace(traceId, "propose", "propose_action", 0),
  };
}

/** Every stand-in this module can produce for a tenant, so evidence and trace lookups can find them. */
export function adviseFixtures(tenantId: string): Fixture[] {
  const advise = adviseFixture(tenantId);
  if (!advise) return [];
  const upgrades = (advise.envelope.suggested_actions ?? [])
    .map((s) => upgradeFixture(tenantId, s.target_label))
    .filter((f): f is Fixture => f !== null);
  return [advise, ...upgrades];
}
