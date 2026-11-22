/** Everything the list answer derives from the envelope with no rendering: sort choices, numbered sources, scope line, copy text. */
import type { AnswerEnvelope, EvidenceItem } from "../types/AnswerEnvelope";
import type { AnswerTable, Column, Row, ScopeLine } from "../types/AnswerEnvelope";
import { formatAsOf, parseRef, plural } from "./format";

export type Direction = "asc" | "desc";

export interface SortState {
  key: string;
  direction: Direction;
}

export interface SortChoice extends SortState {
  /** `key:direction`, stable for a menu item's identity. */
  id: string;
  label: string;
}

export const sortId = (sort: SortState) => `${sort.key}:${sort.direction}`;

const firstValue = (table: AnswerTable, key: string) => table.rows.map((row) => row.cells[key]?.value).find((v) => v != null);

/** Words for a sort, from the column's kind: "Oldest first" for a version or a date, a range for a number, A to Z for names. */
function wordsFor(column: Column, direction: Direction, table: AnswerTable, crowded: boolean): string {
  const suffix = crowded ? ` (${column.label})` : "";
  switch (column.kind) {
    case "version":
    case "date":
      return (direction === "asc" ? "Oldest first" : "Newest first") + suffix;
    case "device":
      return direction === "asc" ? "Name, A to Z" : "Name, Z to A";
    default: {
      const numeric = typeof firstValue(table, column.key) === "number";
      if (numeric || column.kind === "number" || column.kind === "percent") {
        return `${column.label}, ${direction === "asc" ? "low to high" : "high to low"}`;
      }
      return `${column.label}, ${direction === "asc" ? "A to Z" : "Z to A"}`;
    }
  }
}

/**
 * The orders a person can pick: the table's own first, then both directions of each column that has a natural order
 * (a version, a date, a number, a percentage, the device name). Pills and free text are offered only as the default.
 */
export function sortChoices(table: AnswerTable): SortChoice[] {
  const ordered = (c: Column) => ["version", "date", "number", "percent", "device"].includes(c.kind);
  const sortable = table.columns.filter((c) => ordered(c) || c.key === table.default_sort.key);
  const choices: SortChoice[] = [];
  const add = (column: Column, direction: Direction) => {
    const id = sortId({ key: column.key, direction });
    if (choices.some((choice) => choice.id === id)) return;
    const crowded = table.columns.filter((c) => c.kind === column.kind && ordered(c)).length > 1;
    choices.push({ key: column.key, direction, id, label: wordsFor(column, direction, table, crowded) });
  };
  const own = table.columns.find((c) => c.key === table.default_sort.key);
  if (own) {
    add(own, table.default_sort.direction);
    add(own, table.default_sort.direction === "asc" ? "desc" : "asc");
  }
  for (const column of sortable) {
    add(column, "asc");
    add(column, "desc");
  }
  return choices;
}

function compare(a: Row["cells"][string]["value"], b: Row["cells"][string]["value"]): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/** The rows in the chosen order. Empty values go last either way; ties keep the order the answer gave. */
export function sortRows(table: AnswerTable, sort: SortState): Row[] {
  const { key, direction } = sort;
  if (key === table.default_sort.key && direction === table.default_sort.direction) return [...table.rows];
  const sign = direction === "asc" ? 1 : -1;
  return table.rows
    .map((row, index) => ({ row, index }))
    .sort((x, y) => {
      const a = x.row.cells[key]?.value ?? null;
      const b = y.row.cells[key]?.value ?? null;
      if (a === null && b === null) return x.index - y.index;
      if (a === null) return 1;
      if (b === null) return -1;
      return sign * compare(a, b) || x.index - y.index;
    })
    .map(({ row }) => row);
}

export interface NumberedEvidence {
  /** 1-based, the number the Source chip and the Evidence row share. */
  n: number;
  ref: string;
  hostname: string;
  /** What was read: "OS version". */
  field: string;
  value: string;
  /** `latest snapshot` when the answer used each device's latest, else the reading's own time. */
  when: string;
  /** The reading's exact time, for a tooltip. */
  collected: string | null;
}

/** "OS version · initech-macbook-6 · 2026-06-12 10:31Z" -> the three parts, when the label has them. */
function labelParts(label: string): string[] {
  return label.split(" · ").map((part) => part.trim());
}

/**
 * The evidence as a numbered list. The rows' own sources come first, in the order the answer gave its rows, so
 * the numbers on a Source column run 1, 2, 3 down the default order; readings only a claim cites follow. Readings that
 * only a suggested action rests on are that action's, not the answer's, and are left out.
 */
export function numberEvidence(envelope: AnswerEnvelope): NumberedEvidence[] {
  const byRef = new Map(envelope.evidence.map((item) => [item.ref, item]));
  const refs: string[] = [];
  const take = (ref: string) => {
    if (byRef.has(ref) && !refs.includes(ref)) refs.push(ref);
  };
  for (const row of envelope.table?.rows ?? []) row.refs.forEach(take);
  for (const claim of envelope.claims) claim.refs.forEach(take);
  if (refs.length === 0) envelope.evidence.forEach((item) => take(item.ref));

  const names = new Map<string, string>();
  for (const row of envelope.table?.rows ?? []) names.set(row.device_id, row.label);
  for (const list of envelope.entity_lists) for (const item of list.items) names.set(item.device_id, item.label);
  for (const device of envelope.coverage?.excluded_devices ?? []) names.set(device.device_id, device.label);

  const latest = /latest snapshot/i.test(envelope.scope_line?.basis ?? "");
  return describeRefs(refs, byRef, names, latest);
}

/** "compliance[screen_lock]" -> "Compliance: screen lock", for a reading whose value this view does not hold. */
export function pathWords(path: string): string {
  const spaced = path.replace(/\[([^\]]*)\]/g, ": $1").replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function describeRefs(
  refs: string[],
  byRef: Map<string, EvidenceItem>,
  names: Map<string, string>,
  latest: boolean,
): NumberedEvidence[] {
  return refs.map((ref, index) => {
    const item = byRef.get(ref);
    const parts = labelParts(item?.label ?? "");
    const parsed = parseRef(ref);
    const hostname = (parsed && names.get(parsed.device)) || parts[1] || parsed?.device || ref;
    const collected = parsed ? formatAsOf(parsed.collectedAt) : null;
    return {
      n: index + 1,
      ref,
      hostname,
      field: item ? (parts.length > 1 ? parts[0] : item.label) : "Reading",
      value: item?.value ?? (parsed ? pathWords(parsed.path) : ""),
      when: latest ? "latest snapshot" : (collected ?? ""),
      collected,
    };
  });
}

/**
 * The given readings numbered in the order given, for a card that cites its own references. A reference this view
 * holds no value for still reads as the reading it names and when it was taken.
 */
export function numberRefs(refs: string[], evidence: readonly EvidenceItem[] = []): NumberedEvidence[] {
  return describeRefs([...new Set(refs)], new Map(evidence.map((item) => [item.ref, item])), new Map(), false);
}

/**
 * The value as it follows its label in a row. When the value opens with a product word the label already names
 * ("OS version" and "macOS 13.6") and a version follows, the repeated word is dropped: "OS version 13.6".
 */
export function readingValue(field: string, value: string): string {
  const match = /^(\S+)\s+(\d.*)$/.exec(value.trim());
  if (!match) return value;
  const word = match[1].toLowerCase();
  const names = field.toLowerCase().split(/\s+/);
  const repeated = names.some((name) => name.length >= 2 && (word === name || (word.length > name.length && word.endsWith(name))));
  return repeated ? match[2] : value;
}

/** The Source number for a row: where its first cited reading sits in the numbered list. */
export function sourceNumber(row: Row, numbered: NumberedEvidence[]): number | null {
  for (const ref of row.refs) {
    const found = numbered.find((item) => item.ref === ref);
    if (found) return found.n;
  }
  return null;
}

/** "Macs" for a macOS check, else "devices", the noun the scope line counts. */
export function checkedNoun(count: number, table: AnswerTable | null, headline: string): string {
  const mac = /\bmacOS\b/i.test(`${table?.title ?? ""} ${headline}`);
  return mac ? (count === 1 ? "Mac" : "Macs") : count === 1 ? "device" : "devices";
}

/** The two footer lines under a table. Counts that are zero or unknown are left out; the second line names the company and the moment. */
export function scopeLineText(line: ScopeLine, table: AnswerTable | null, headline: string): { counts: string; source: string } {
  const parts = [`${line.checked} ${checkedNoun(line.checked, table, headline)} checked`];
  if (line.ok != null && line.ok > 0) parts.push(`${line.ok} ${line.ok_label}`.trim());
  if (line.not_checked > 0) parts.push(`${line.not_checked} not checked`);
  return {
    counts: parts.join(" · "),
    source: [line.company_name, line.basis, `as of ${formatAsOf(line.as_of)}`].filter(Boolean).join(" · "),
  };
}

/** The table as text a person can paste anywhere: header line, then one tab-separated line per row. */
export function tableAsText(table: AnswerTable, rows: Row[] = table.rows): string {
  const lines = [
    table.columns.map((c) => c.label).join("\t"),
    ...rows.map((row) => table.columns.map((c) => row.cells[c.key]?.display ?? "").join("\t")),
  ];
  if (table.total_rows != null && table.total_rows > table.rows.length) {
    lines.push(`Showing ${table.rows.length} of ${table.total_rows}.`);
  }
  return lines.join("\n");
}

/** An insight answer as text: one line per finding, most urgent first. */
function insightsAsText(envelope: AnswerEnvelope): string {
  return envelope.insights.map((insight) => `${insight.severity === "act_soon" ? "Act soon" : insight.severity === "watch" ? "Watch" : "OK"}: ${insight.finding}`).join("\n");
}

/** What the Copy button puts on the clipboard: the headline, the supporting sentence, then the table as the person sees it. */
export function copyText(envelope: AnswerEnvelope, rows?: Row[]): string {
  const head = [envelope.headline, envelope.summary].map((s) => s?.trim()).filter(Boolean).join("\n");
  const body = envelope.insights.length > 0 ? insightsAsText(envelope) : envelope.table ? tableAsText(envelope.table, rows) : "";
  return [head, body].filter(Boolean).join("\n\n");
}

/** The count pill beside the table title: "4 devices", or "showing 50 of 120" when the table is cut short. */
export function countPill(table: AnswerTable): string {
  const total = table.total_rows;
  if (total != null && total > table.rows.length) return `showing ${table.rows.length} of ${total}`;
  // A table of devices counts devices; one with several findings per device counts its rows.
  const devices = new Set(table.rows.map((row) => row.device_id)).size;
  return devices === table.rows.length ? plural(devices, "device") : plural(table.rows.length, "row");
}

/** Delay before a row arrives, in seconds; null when rows should simply be there (reopened, or reduced motion). */
export function rowDelay(index: number, reduce: boolean, reopened: boolean): number | null {
  if (reduce || reopened) return null;
  // A little uneven, so the cascade reads natural; capped so a long table is never kept waiting.
  return Math.min(index, 10) * 0.035 + (index % 2 === 0 ? 0 : 0.004);
}

/** Notes that only repeat the Not checked list (the same devices by serial) are left out of a table answer. */
export function notesFor(envelope: AnswerEnvelope): string[] {
  const excluded = envelope.coverage?.excluded_devices ?? [];
  if (!envelope.table || excluded.length === 0) return envelope.notes;
  return envelope.notes.filter((note) => !excluded.some((device) => note.includes(device.device_id)));
}
