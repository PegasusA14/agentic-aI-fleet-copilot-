import { describe, expect, it } from "vitest";
import {
  copyText,
  countPill,
  notesFor,
  numberEvidence,
  rowDelay,
  scopeLineText,
  sortChoices,
  sortRows,
  sourceNumber,
  tableAsText,
} from "./answerTable";
import { getFixture } from "./fixtures";

const MACOS = getFixture("initech-003", "macos_older_than_15").envelope;
const LOW_DISK = getFixture("acme-001", "low_disk").envelope;
const COMPLIANCE = getFixture("acme-001", "compliance_failures").envelope;
const table = MACOS.table!;

describe("sortChoices", () => {
  it("offers the table's own order first, worded from the column kind", () => {
    const choices = sortChoices(table);
    expect(choices[0]).toMatchObject({ key: "macos", direction: "asc", label: "Oldest first" });
    expect(choices.map((c) => c.label)).toEqual(["Oldest first", "Newest first", "Name, A to Z", "Name, Z to A"]);
  });

  it("words percentages as ranges and offers a pill column only when it is the default", () => {
    expect(sortChoices(LOW_DISK.table!).map((c) => c.label)).toEqual([
      "Free, low to high",
      "Free, high to low",
      "Name, A to Z",
      "Name, Z to A",
    ]);
    const labels = sortChoices(COMPLIANCE.table!).map((c) => c.label);
    expect(labels[0]).toBe("Severity, high to low");
    expect(labels).toContain("Oldest first");
    expect(labels).not.toContain("Check, A to Z");
  });
});

describe("sortRows", () => {
  it("keeps the answer's own order for the default sort", () => {
    expect(sortRows(table, table.default_sort).map((r) => r.label)).toEqual(table.rows.map((r) => r.label));
  });

  it("reorders by version, newest first", () => {
    const rows = sortRows(table, { key: "macos", direction: "desc" });
    expect(rows.map((r) => r.cells.macos.display)).toEqual(["14.5", "14.5", "13.6", "13.6"]);
  });

  it("reorders by name and keeps ties in the answer's order", () => {
    const byName = sortRows(table, { key: "device", direction: "asc" }).map((r) => r.label);
    expect(byName).toEqual([...byName].sort());
    const ties = sortRows(table, { key: "behind", direction: "desc" }).map((r) => r.label);
    expect(ties.slice(0, 2)).toEqual(["initech-macbook-2", "initech-macbook-7"]);
  });

  it("puts empty values last in either direction", () => {
    const gappy = {
      ...table,
      rows: table.rows.map((r, i) => (i === 0 ? { ...r, cells: { ...r.cells, behind: { ...r.cells.behind, value: null } } } : r)),
    };
    expect(sortRows(gappy, { key: "behind", direction: "asc" }).at(-1)?.label).toBe(table.rows[0].label);
    expect(sortRows(gappy, { key: "behind", direction: "desc" }).at(-1)?.label).toBe(table.rows[0].label);
  });
});

describe("numberEvidence", () => {
  const numbered = numberEvidence(MACOS);

  it("numbers the readings in the table's row order, not the evidence array's", () => {
    expect(numbered.map((e) => [e.n, e.hostname])).toEqual([
      [1, "initech-macbook-2"],
      [2, "initech-macbook-7"],
      [3, "initech-macbook-6"],
      [4, "initech-mac-4"],
    ]);
    expect(MACOS.evidence[0].ref).not.toBe(numbered[0].ref);
  });

  it("reads the field and value, and says 'latest snapshot' when that is the basis", () => {
    expect(numbered[0]).toMatchObject({ field: "OS version", value: "macOS 13.6", when: "latest snapshot" });
    expect(numbered[0].collected).toBe("2026-06-12 10:13 UTC");
  });

  it("falls back to the reading's own time when the answer is not a latest snapshot", () => {
    const trend = numberEvidence({ ...MACOS, scope_line: { ...MACOS.scope_line!, basis: "last 7 days of snapshots" } });
    expect(trend[0].when).toBe("2026-06-12 10:13 UTC");
  });

  it("gives each row the number of its first reading", () => {
    expect(table.rows.map((row) => sourceNumber(row, numbered))).toEqual([1, 2, 3, 4]);
  });

  it("appends readings no row cites, after the rows' own", () => {
    const extra = { ref: "initech-003/ZZZ@2026-06-12T10:00:00Z#os", label: "OS version · zed · 2026-06-12 10:00Z", value: "macOS 12" };
    const withExtra = numberEvidence({ ...MACOS, evidence: [extra, ...MACOS.evidence] });
    expect(withExtra).toHaveLength(5);
    expect(withExtra[4].ref).toBe(extra.ref);
  });
});

describe("scopeLineText", () => {
  it("counts Macs for a macOS check and names the company, basis and moment", () => {
    expect(scopeLineText(MACOS.scope_line!, MACOS.table, MACOS.headline)).toEqual({
      counts: "5 Macs checked · 1 up to date · 2 not checked",
      source: "Initech · latest snapshot per device · as of 2026-06-12 11:00 UTC",
    });
  });

  it("says devices otherwise and leaves out zero and unknown counts", () => {
    expect(scopeLineText(LOW_DISK.scope_line!, LOW_DISK.table, LOW_DISK.headline).counts).toBe("10 devices checked · 8 healthy");
    const none = { ...LOW_DISK.scope_line!, ok: null, ok_label: "", not_checked: 0 };
    expect(scopeLineText(none, LOW_DISK.table, LOW_DISK.headline).counts).toBe("10 devices checked");
    expect(scopeLineText({ ...none, ok: 0 }, null, "x").counts).toBe("10 devices checked");
    expect(scopeLineText({ ...none, checked: 1 }, null, "x").counts).toBe("1 device checked");
  });
});

describe("copy text", () => {
  it("is the headline, the summary, then the table tab-separated", () => {
    const lines = copyText(MACOS).split("\n");
    expect(lines[0]).toBe(MACOS.headline);
    expect(lines[1]).toBe(MACOS.summary);
    expect(lines[2]).toBe("");
    expect(lines[3]).toBe("Device\tmacOS\tBehind 15");
    expect(lines[4]).toBe("initech-macbook-2\t13.6\t2 major behind");
    expect(lines).toHaveLength(8);
  });

  it("follows the order the person sees", () => {
    const rows = sortRows(table, { key: "macos", direction: "desc" });
    expect(tableAsText(table, rows).split("\n")[1]).toBe("initech-macbook-6\t14.5\t1 major behind");
  });

  it("says when the table is cut short", () => {
    expect(tableAsText({ ...table, total_rows: 120 }).split("\n").at(-1)).toBe("Showing 4 of 120.");
  });

  it("is only the headline and summary for an answer with no table", () => {
    expect(copyText({ ...MACOS, table: null })).toBe(`${MACOS.headline}\n${MACOS.summary}`);
  });
});

describe("countPill", () => {
  it("counts devices, rows when a device has several findings, and says when it shows part", () => {
    expect(countPill(table)).toBe("4 devices");
    expect(countPill({ ...table, total_rows: 120 })).toBe("showing 4 of 120");
    expect(countPill(COMPLIANCE.table!)).toBe("14 rows");
  });
});

describe("rowDelay", () => {
  it("staggers 30 to 40 ms a row on first arrival", () => {
    expect(rowDelay(0, false, false)).toBe(0);
    const step = (rowDelay(2, false, false) as number) - (rowDelay(1, false, false) as number);
    expect(step).toBeGreaterThanOrEqual(0.03);
    expect(step).toBeLessThanOrEqual(0.04);
  });

  it("does not wait on a long table", () => {
    expect(rowDelay(40, false, false)).toBeLessThan(0.4);
  });

  it("is null when reopened or under reduced motion", () => {
    expect(rowDelay(1, false, true)).toBeNull();
    expect(rowDelay(1, true, false)).toBeNull();
  });
});

describe("notesFor", () => {
  it("drops the note that only repeats the not-checked devices by serial", () => {
    expect(MACOS.notes.length).toBeGreaterThan(0);
    expect(notesFor(MACOS)).toEqual([]);
  });

  it("keeps every note when there is no table, and other notes beside a table", () => {
    expect(notesFor({ ...MACOS, table: null })).toEqual(MACOS.notes);
    expect(notesFor({ ...MACOS, notes: [...MACOS.notes, "Readings are 2 days old."] })).toEqual(["Readings are 2 days old."]);
  });
});
