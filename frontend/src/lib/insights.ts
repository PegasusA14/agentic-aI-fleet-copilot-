/** What an insight card says and shows, derived from the envelope with no rendering. */
import type { AnswerEnvelope, Insight, KeyValue, SuggestedAction } from "../types/AnswerEnvelope";
import { scopeLineText } from "./answerTable";

export type Severity = Insight["severity"];

export function severityWord(severity: Severity): string {
  return severity === "act_soon" ? "Act soon" : severity === "watch" ? "Watch" : "OK";
}

const CATEGORY_WORD: Record<Insight["category"], string> = {
  battery: "Battery",
  disk: "Disk",
  memory: "Memory",
  compliance: "Compliance",
};

const kv = (insight: Insight, label: string) => insight.evidence_kv.find((item) => item.label === label)?.value;

const sentence = (text: string) => {
  const spaced = text.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};

/** "Battery · Declining": the category and what it is doing, from the readings the insight carries. */
export function categoryLabel(insight: Insight): string {
  const word = CATEGORY_WORD[insight.category];
  switch (insight.category) {
    case "battery": {
      const falling = (insight.fit?.slope_per_day ?? 0) < 0;
      if (!falling) return word;
      return insight.projection?.status === "projected" ? `${word} · Declining` : `${word} · Slow decline`;
    }
    case "disk":
    case "memory": {
      const pattern = kv(insight, "Pattern");
      return pattern ? `${word} · ${sentence(pattern)}` : word;
    }
    case "compliance": {
      const check = kv(insight, "Check");
      return check ? `${word} · ${sentence(check)}` : word;
    }
  }
}

/** A chart needs a measured series: a pass/fail check is two values, and says more as numbers. */
export function hasChart(insight: Insight): boolean {
  return insight.category !== "compliance" && insight.series.points.length >= 2;
}

/** The first card and anything that needs acting on stay in full; the quieter ones fold to a line. */
export function isCompact(insight: Insight, index: number): boolean {
  return insight.severity !== "act_soon" && index > 0;
}

/** The two readings a folded card keeps. */
export function evidenceLine(insight: Insight): KeyValue[] {
  return insight.evidence_kv.slice(0, 2);
}

const HISTORY_NOUN: Record<Insight["category"], string> = {
  battery: "battery",
  disk: "disk space",
  memory: "memory",
  compliance: "compliance",
};

/** The follow-up a "View history" chip sends: an ordinary question, answered like any other. */
export function historyRequest(insight: Insight): string {
  return `Show the 30-day ${HISTORY_NOUN[insight.category]} history of ${insight.label}`;
}

/** The approvable actions that are about this card's device. */
export function actionsFor(insight: Insight, actions: readonly SuggestedAction[]): SuggestedAction[] {
  return actions.filter((action) => action.device_id === insight.device_id);
}

/** The suggested actions no card carries; these stay under Next steps. */
export function unplacedActions(insights: readonly Insight[], actions: readonly SuggestedAction[]): SuggestedAction[] {
  const placed = new Set(insights.map((insight) => insight.device_id));
  return actions.filter((action) => !placed.has(action.device_id));
}

export interface Rest {
  /** Beside the title: "6 with no wear signal". */
  meta: string;
  counts: string;
  source: string;
}

/** The devices the cards leave out, said from the scope line; null when it names none. */
export function everythingElse(envelope: AnswerEnvelope): Rest | null {
  const line = envelope.scope_line;
  if (!line || line.ok == null || line.ok <= 0) return null;
  const { counts, source } = scopeLineText(line, envelope.table, envelope.headline);
  return { meta: `${line.ok} ${line.ok_label}`.trim(), counts, source };
}
