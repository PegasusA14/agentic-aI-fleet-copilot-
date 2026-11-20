/* Generated from contracts/*.schema.json by scripts/gen-types.mjs. Do not edit. */

export type Kind = "plan" | "tool_call" | "tool_result" | "verify" | "respond" | "error";
export type LatencyMs = number | null;
export type Seq = number;
export type Summary = string;

export interface TraceEvent {
  data: Data;
  kind: Kind;
  latency_ms: LatencyMs;
  seq: Seq;
  summary: Summary;
}
export interface Data {
  [k: string]: unknown;
}
