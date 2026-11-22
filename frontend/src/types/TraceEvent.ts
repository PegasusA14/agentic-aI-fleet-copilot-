/* Generated from contracts/*.schema.json by scripts/gen-types.mjs. Do not edit. */

export type Kind =
  | "scope"
  | "plan"
  | "reference"
  | "tool_call"
  | "tool_result"
  | "retry"
  | "compose"
  | "explain"
  | "verify"
  | "respond"
  | "memory"
  | "error";
export type LatencyMs = number | null;
export type Seq = number;
export type Summary = string;
export type Tool = string | null;
export type TraceId = string;

/**
 * One step of a turn, streamed to the UI and stored for the observability console.
 *
 * `data` is open but MUST already be redacted: counts, tool names and redacted arguments only.
 * Never raw telemetry values, employee ids or exception text.
 */
export interface TraceEvent {
  data: Data;
  kind: Kind;
  latency_ms: LatencyMs;
  seq: Seq;
  summary: Summary;
  tool: Tool;
  trace_id: TraceId;
}
export interface Data {
  [k: string]: unknown;
}
