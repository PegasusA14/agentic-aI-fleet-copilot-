/** How an evaluation run reads: pass, fail and "not recorded" in words, and effect changes as plain sentences. */
import { EFFECT_TABLES } from "./audit";
import { TENANTS } from "./fixtures";
import type { EvalCase, EvalTally } from "./transport";

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
