import indexJson from "../fixtures/index.json";
import type { AnswerEnvelope } from "../types/AnswerEnvelope";
import type { TraceEvent } from "../types/TraceEvent";

export interface FixtureMeta {
  scenario: string;
  tenant: string;
  question: string;
  keywords: string[];
  synthetic: boolean;
  note: string;
}

export interface Fixture {
  meta: FixtureMeta;
  envelope: AnswerEnvelope;
  trace: TraceEvent[];
}

export interface Tenant {
  id: string;
  name: string;
}

export const TENANTS: readonly Tenant[] = [
  { id: "acme-001", name: "Acme" },
  { id: "globex-002", name: "Globex" },
  { id: "initech-003", name: "Initech" },
];

const modules = import.meta.glob<Fixture>("../fixtures/*/*.json", {
  eager: true,
  import: "default",
});

const byKey = new Map<string, Fixture>();
for (const [path, fixture] of Object.entries(modules)) {
  const match = path.match(/fixtures\/([^/]+)\/([^/]+)\.json$/);
  if (match) byKey.set(`${match[1]}/${match[2]}`, fixture);
}

export const FIXTURE_INDEX = indexJson as unknown as Record<string, FixtureMeta[]>;

export function getFixture(tenantId: string, scenario: string): Fixture {
  const fixture = byKey.get(`${tenantId}/${scenario}`);
  if (!fixture) throw new Error(`No fixture for ${tenantId}/${scenario}`);
  return fixture;
}

export function fixturesFor(tenantId: string): Fixture[] {
  return (FIXTURE_INDEX[tenantId] ?? []).map((meta) => getFixture(tenantId, meta.scenario));
}

export function tenantName(tenantId: string): string {
  return TENANTS.find((t) => t.id === tenantId)?.name ?? tenantId;
}

/** Devices evaluated in a tenant's latest-snapshot checks, read from a real answer. */
export function fleetSize(tenantId: string): number | null {
  return getFixture(tenantId, "low_disk").envelope.coverage?.population ?? null;
}

/** The pinned "now" of the dataset, as carried by the answers themselves. */
export function datasetAsOf(): string {
  return getFixture(TENANTS[0].id, "low_disk").envelope.scope.as_of;
}
