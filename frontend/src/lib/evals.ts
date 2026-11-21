/** How an evaluation run reads: pass, fail and "not recorded" in words, and effect changes as plain sentences. */
import { EFFECT_TABLES } from "./audit";
import { TENANTS } from "./fixtures";
import type { EvalCase, EvalTally, EvalTotals } from "./transport";

export type EvalState = "passed" | "failed" | "not_recorded" | "none";

export const EVAL_STATE: Record<EvalState, { word: string; tone: "pass" | "alert" | "neutral" }> = {
  passed: { word: "Passed", tone: "pass" },
  failed: { word: "Failed", tone: "alert" },
  not_recorded: { word: "Not recorded", tone: "neutral" },
  none: { word: "No cases", tone: "neutral" },
};

export const SUITE_ORDER = ["grounding", "trends", "actions", "guardrails"];

/** The runner reports a case it could not replay with this one check. */
export function isNotRecorded(c: EvalCase): boolean {
  return !c.passed && c.checks.some((check) => check.name === "recorded" && check.detail === "not recorded");
}

export function caseState(c: EvalCase): EvalState {
  if (c.passed) return "passed";
  return isNotRecorded(c) ? "not_recorded" : "failed";
}

/** A suite (or the whole run): failed if any case really failed, else not recorded if any was missed. */
export function tallyState(tally: EvalTally): EvalState {
  if (tally.total === 0) return "none";
  if (tally.passed === tally.total) return "passed";
  return tally.total - tally.passed - tally.not_recorded > 0 ? "failed" : "not_recorded";
}

/** `admin-acme` -> "Acme"; an alias that names no demo company stays as it is. */
export function companyOfUser(user: string): string {
  const name = user.replace(/^admin-/, "");
  return TENANTS.find((t) => t.name.toLowerCase() === name)?.name ?? user;
}

export function suiteLabel(suite: string): string {
  return suite.charAt(0).toUpperCase() + suite.slice(1).replaceAll("_", " ");
}

export function checkLabel(name: string): string {
  return name.replaceAll("_", " ");
}

/** "Tickets +1 (simulated)" for each decision that changed an effect table; "No effect change" otherwise. */
export function effectLines(c: EvalCase): string[] {
  return c.effects.map((step) => {
    const changes = Object.entries(step.delta)
      .filter(([, n]) => n !== 0)
      .map(([table, n]) => `${EFFECT_TABLES[table] ?? table.replaceAll("_", " ")} ${n > 0 ? "+" : "−"}${Math.abs(n)}`);
    return changes.length > 0 ? `${changes.join(", ")} (simulated)` : "No effect change";
  });
}

export interface EvalFigure {
  id: string;
  label: string;
  value: string;
  note: string;
  /** Set on the two hard gates: they fail a run whatever the pass rate. */
  gate?: "clear" | "failed";
}

function percent(rate: number | null): string {
  return rate === null ? "n/a" : `${(rate * 100).toFixed(1).replace(/\.0$/, "")}%`;
}

function whole(n: number): string {
  return n.toLocaleString("en-US");
}

/**
 * The aggregate figures of a run as a compact row, or null for a run stored before they existed. The two hard
 * gates (tenant leaks, approval bypasses) carry their state, so the console shows a mark and a word for them.
 */
export function evalFigures(totals: EvalTotals, mode: string): EvalFigure[] | null {
  const { tenant_leaks: leaks, approval_bypasses: bypasses } = totals;
  if (leaks === undefined || bypasses === undefined) return null;
  const gate = (n: number) => (n === 0 ? ("clear" as const) : ("failed" as const));
  const figures: EvalFigure[] = [
    {
      id: "tenant_leaks",
      label: "Tenant leaks",
      value: String(leaks),
      note: "another company's data in an answer or trace",
      gate: gate(leaks),
    },
    {
      id: "approval_bypasses",
      label: "Approval bypasses",
      value: String(bypasses),
      note: "actions run without an approval step",
      gate: gate(bypasses),
    },
  ];
  const cite = totals.citation_precision;
  if (cite) {
    figures.push({
      id: "citation_precision",
      label: "Citation precision",
      value: percent(cite.rate),
      note: cite.total > 0 ? `${whole(cite.valid)} of ${whole(cite.total)} references resolve` : "no references checked",
    });
  }
  const refuse = totals.refusal_accuracy;
  if (refuse) {
    figures.push({
      id: "refusal_accuracy",
      label: "Refusal accuracy",
      value: percent(refuse.rate),
      note: refuse.total > 0 ? `${refuse.passed} of ${refuse.total} refusals as expected` : "no refusals expected",
    });
  }
  const latency = totals.latency_ms;
  figures.push({
    id: "latency",
    label: "Latency p50 / p95",
    value: latency ? `${latency.p50} / ${latency.p95} ms` : "n/a",
    note: latency ? (mode.includes("replay") ? "replay latency, not the model's" : "live, per turn") : "no turns timed",
  });
  const tokens = totals.tokens;
  figures.push({
    id: "tokens",
    label: "Tokens",
    value: tokens ? whole(tokens.prompt + tokens.completion) : "n/a",
    note: tokens ? `${whole(tokens.prompt)} prompt, ${whole(tokens.completion)} completion` : "no usage recorded",
  });
  return figures;
}
