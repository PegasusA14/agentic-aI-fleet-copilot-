/** `2026-06-12T11:00:00Z` -> `2026-06-12 11:00 UTC`. */
export function formatAsOf(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

/** `acme-001/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk` -> its parts, or null if malformed. */
export function parseRef(ref: string): {
  company: string;
  device: string;
  collectedAt: string;
  path: string;
} | null {
  const match = ref.match(/^([^/@#\s]+)\/([^/@#\s]+)@(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)#(.+)$/);
  if (!match) return null;
  return { company: match[1], device: match[2], collectedAt: match[3], path: match[4] };
}

export function plural(n: number, word: string): string {
  return `${n} ${n === 1 ? word : `${word}s`}`;
}

export const REASON_TEXT: Record<string, string> = {
  no_matching_records: "No records matched",
  insufficient_history: "Not enough history",
  not_found: "Not found in your fleet",
  off_topic: "Not about this fleet",
  unsupported_action: "Action not available",
  prompt_injection: "Instruction not followed",
  tool_failure: "Data lookup failed",
  step_limit: "Step limit reached",
  not_eligible: "Not supported by the evidence",
  insufficient_evidence: "Not enough recent evidence",
  already_done: "Already done",
  too_many_pending: "Too many proposals waiting",
};

export const EXCLUDED_TEXT: Record<string, string> = {
  out_of_scope: "out of scope",
  unknown_version: "unrecognised version",
  insufficient_history: "too few snapshots",
  no_reading: "no battery reading",
  no_readings: "no readings",
};
