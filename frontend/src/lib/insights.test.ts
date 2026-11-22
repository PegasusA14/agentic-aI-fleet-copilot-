import { describe, expect, it } from "vitest";
import {
  actionsFor,
  categoryLabel,
  evidenceLine,
  everythingElse,
  hasChart,
  historyRequest,
  isCompact,
  severityWord,
  unplacedActions,
} from "./insights";
import { getFixture } from "./fixtures";

const BATTERY = getFixture("acme-001", "battery_wear").envelope;
const HEALTH = getFixture("acme-001", "fleet_health").envelope;
const B0 = BATTERY.insights[0]!;
const H1 = HEALTH.insights[1]!;
const A0 = BATTERY.suggested_actions[0]!;

describe("severityWord", () => {
  it("says each severity in words", () => {
    expect(severityWord("act_soon")).toBe("Act soon");
    expect(severityWord("watch")).toBe("Watch");
    expect(severityWord("ok")).toBe("OK");
  });
});

describe("categoryLabel", () => {
  it("names the category and what it is doing", () => {
    expect(categoryLabel(B0)).toBe("Battery · Declining");
  });
  it("uses a disk or memory insight's pattern", () => {
    const disk = HEALTH.insights.find((i) => i.category === "disk")!;
    expect(categoryLabel(disk)).toMatch(/^Disk · (Sustained|Onset|Intermittent)$/);
  });
  it("uses a compliance insight's check", () => {
    const check = HEALTH.insights.find((i) => i.category === "compliance")!;
    expect(categoryLabel(check)).toBe("Compliance · Screen lock");
  });
});

describe("hasChart", () => {
  it("charts battery, disk and memory series, and leaves a pass/fail check as numbers only", () => {
    expect(hasChart(B0)).toBe(true);
    expect(hasChart(HEALTH.insights.find((i) => i.category === "disk")!)).toBe(true);
    expect(hasChart(HEALTH.insights.find((i) => i.category === "compliance")!)).toBe(false);
    expect(hasChart({ ...B0, series: { ...B0.series, points: [] } })).toBe(false);
  });
});

describe("isCompact", () => {
  it("keeps the first card and every act-soon card in full and folds later watch cards", () => {
    const watch = HEALTH.insights.filter((i) => i.severity === "watch");
    const W0 = watch[0]!;
    expect(isCompact(W0, 0)).toBe(false);
    expect(isCompact(W0, 5)).toBe(true);
    expect(isCompact(H1, 1)).toBe(false);
  });
});

describe("evidenceLine", () => {
  it("joins the first two readings", () => {
    const first = B0;
    expect(evidenceLine(first)).toEqual([
      { label: "Capacity vs its first reading", value: "-4.2%" },
      { label: "Capacity index now", value: "95.8 (first reading = 100)" },
    ]);
  });
});

describe("historyRequest", () => {
  it("asks for the device's own history, by name", () => {
    expect(historyRequest(B0)).toBe("Show the 30-day battery history of acme-thinkpad-8");
    const disk = HEALTH.insights.find((i) => i.category === "disk")!;
    expect(historyRequest(disk)).toBe(`Show the 30-day disk space history of ${disk.label}`);
  });
});

describe("actionsFor and unplacedActions", () => {
  it("matches suggested actions to the insight's device", () => {
    const mine = actionsFor(B0, BATTERY.suggested_actions);
    expect(mine.length).toBeGreaterThan(0);
    expect(mine.every((a) => a.device_id === B0.device_id)).toBe(true);
  });
  it("leaves for the Next steps only the actions no card carries", () => {
    expect(unplacedActions(BATTERY.insights, BATTERY.suggested_actions)).toEqual([]);
    const stray = { ...A0, device_id: "OTHER" };
    expect(unplacedActions(BATTERY.insights, [...BATTERY.suggested_actions, stray])).toEqual([stray]);
  });
});

describe("everythingElse", () => {
  it("is the devices that show nothing, from the scope line", () => {
    const rest = everythingElse(BATTERY);
    expect(rest).not.toBeNull();
    expect(rest!.meta).toBe("6 with no wear signal");
    expect(rest!.counts).toBe("9 laptops checked · 6 with no wear signal");
  });
  it("is absent when nothing was left out", () => {
    expect(everythingElse({ ...BATTERY, scope_line: { ...BATTERY.scope_line!, ok: 0 } })).toBeNull();
    expect(everythingElse({ ...BATTERY, scope_line: null })).toBeNull();
  });
});
