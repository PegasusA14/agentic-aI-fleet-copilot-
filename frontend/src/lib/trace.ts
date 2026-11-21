import type { TraceEvent } from "./types";

export const KIND_WORD: Record<TraceEvent["kind"], string> = {
  scope: "Scope",
  plan: "Plan",
  reference: "Reference",
  tool_call: "Tool call",
  tool_result: "Tool result",
  retry: "Retry",
  compose: "Compose",
  explain: "Explain",
  verify: "Check",
  respond: "Response",
  error: "Error",
};

/** A trace value as plain text: scalars as they are, anything nested as compact JSON. Never markup. */
export function formatValue(value: unknown): string {
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  return String(value);
}
