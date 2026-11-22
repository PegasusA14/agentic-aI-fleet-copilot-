import { describe, expect, it } from "vitest";
import {
  axisLabels,
  linePath,
  nearestIndex,
  prepareSeries,
  shortDate,
  sparkLayout,
  sparkSummary,
  stepIndex,
  valueText,
} from "./sparkline";

const points = [
  { at: "2026-05-14T10:00:00Z", value: 100 },
  { at: "2026-05-24T10:00:00Z", value: 98 },
  { at: "2026-06-03T10:00:00Z", value: 96 },
  { at: "2026-06-12T10:00:00Z", value: 95 },
];
const threshold = { value: 90, label: "Our service threshold (90% of its first reading)", source: "policy" as const };
const projection = { estimate: "2026-07-21", earliest: "2026-07-21", latest: "2026-07-22", status: "projected" as const };
const SIZE = { width: 300, height: 96 };

describe("shortDate", () => {
  it("reads a date as month and day, in UTC", () => {
    expect(shortDate("2026-06-12T23:30:00Z")).toBe("Jun 12");
    expect(shortDate("2026-07-21")).toBe("Jul 21");
  });
});

describe("prepareSeries and sparkLayout", () => {
  const prep = prepareSeries(points, threshold, projection)!;
  const lay = sparkLayout(prep, SIZE);

  it("keeps one coordinate per snapshot, left to right, higher values higher up", () => {
    expect(lay.coords).toHaveLength(4);
    const xs = lay.coords.map((c) => c[0]);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    const ys = lay.coords.map((c) => c[1]);
    expect(ys[0]).toBeLessThan(ys[3]);
  });

  it("ends the projection segment at the estimate date, on the threshold line", () => {
    expect(lay.projection).not.toBeNull();
    const [x0, y0] = lay.coords[3];
    expect(lay.projection!.from).toEqual([x0, y0]);
    expect(lay.projection!.to[1]).toBeCloseTo(lay.thresholdY!, 5);
    expect(lay.projection!.to[0]).toBeCloseTo(lay.x(Date.UTC(2026, 6, 21, 12)), 5);
    // the estimate is the right-most thing on the chart
    expect(lay.projection!.to[0]).toBeGreaterThan(x0);
    expect(lay.projection!.to[0]).toBeLessThanOrEqual(SIZE.width);
  });

  it("puts the threshold below the whole series on the page when the series sits above it, its name above the line", () => {
    expect(lay.thresholdY!).toBeGreaterThan(Math.max(...lay.coords.map((c) => c[1])));
    expect(lay.labelSide).toBe("above");
  });

  it("moves the threshold's name under its line when the series runs through the space above it", () => {
    const near = prepareSeries(
      [
        { at: "2026-05-14T10:00:00Z", value: 100 },
        { at: "2026-05-24T10:00:00Z", value: 90.6 },
        { at: "2026-06-03T10:00:00Z", value: 90.4 },
        { at: "2026-06-12T10:00:00Z", value: 100 },
      ],
      threshold,
      null,
    )!;
    const moved = sparkLayout(near, SIZE);
    expect(moved.labelSide).toBe("below");
    expect(moved.thresholdY!).toBeLessThan(SIZE.height - 18 - 13);
  });

  it("keeps everything inside the drawing area", () => {
    for (const [x, y] of lay.coords) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(SIZE.width);
      expect(y).toBeGreaterThan(0);
      expect(y).toBeLessThanOrEqual(lay.plotBottom);
    }
  });

  it("pads the y domain so a threshold outside the data still shows", () => {
    expect(lay.domain.min).toBeLessThanOrEqual(90);
    expect(lay.domain.max).toBeGreaterThanOrEqual(100);
  });

  it("names the line above it when the series sits below the threshold", () => {
    const low = prepareSeries(
      points.map((p) => ({ ...p, value: p.value - 95 })),
      { ...threshold, value: 10 },
      null,
    )!;
    expect(sparkLayout(low, SIZE).labelSide).toBe("above");
  });

  it("draws no projection when none is given, and has no threshold line without one", () => {
    const bare = sparkLayout(prepareSeries(points, null, null)!, SIZE);
    expect(bare.projection).toBeNull();
    expect(bare.thresholdY).toBeNull();
  });

  it("needs at least two snapshots to draw anything", () => {
    expect(prepareSeries(points.slice(0, 1), threshold, projection)).toBeNull();
    expect(prepareSeries([], null, null)).toBeNull();
  });

  it("ignores a projection that is not an estimate", () => {
    const none = { estimate: null, earliest: null, latest: null, status: "none" as const };
    expect(sparkLayout(prepareSeries(points, threshold, none)!, SIZE).projection).toBeNull();
  });
});

describe("linePath", () => {
  it("writes one move and a line to each further point, to one decimal", () => {
    expect(
      linePath([
        [0, 10],
        [5.04, 20.26],
        [10, 30],
      ]),
    ).toBe("M0 10L5 20.3L10 30");
    expect(linePath([])).toBe("");
  });
});

describe("nearestIndex", () => {
  const xs = [10, 100, 190, 280];
  it("snaps to the closest snapshot, not the line", () => {
    expect(nearestIndex(xs, 0)).toBe(0);
    expect(nearestIndex(xs, 54)).toBe(0);
    expect(nearestIndex(xs, 56)).toBe(1);
    expect(nearestIndex(xs, 150)).toBe(2);
    expect(nearestIndex(xs, 999)).toBe(3);
  });
});

describe("stepIndex", () => {
  it("moves by arrow, Home and End, and stays inside the series", () => {
    expect(stepIndex(2, "ArrowLeft", 5)).toBe(1);
    expect(stepIndex(0, "ArrowLeft", 5)).toBe(0);
    expect(stepIndex(4, "ArrowRight", 5)).toBe(4);
    expect(stepIndex(1, "ArrowRight", 5)).toBe(2);
    expect(stepIndex(3, "Home", 5)).toBe(0);
    expect(stepIndex(1, "End", 5)).toBe(4);
    expect(stepIndex(1, "a", 5)).toBeNull();
  });
});

describe("axisLabels", () => {
  const prep = prepareSeries(points, threshold, projection)!;
  const lay = sparkLayout(prep, SIZE);
  it("names only the first date, today and the estimate", () => {
    const labels = axisLabels(lay, prep);
    expect(labels.map((l) => l.text)).toEqual(["May 14", "Today, Jun 12", "Jul 21 (est.)"]);
  });
  it("drops the first date, then today, before they run into each other", () => {
    const tight = sparkLayout(prep, { width: 150, height: 96 });
    const labels = axisLabels(tight, prep);
    expect(labels.map((l) => l.text)).toContain("Jul 21 (est.)");
    expect(labels.length).toBeLessThan(3);
  });
  it("has no estimate label without a projection", () => {
    const bare = prepareSeries(points, threshold, null)!;
    expect(axisLabels(sparkLayout(bare, SIZE), bare).map((l) => l.text)).toEqual(["May 14", "Today, Jun 12"]);
  });
});

describe("valueText and sparkSummary", () => {
  it("says the value, with a percent sign when the unit is a percentage", () => {
    expect(valueText(95.77, "Free disk space (% of the primary volume)")).toBe("95.8%");
    expect(valueText(95.77, "Capacity index (the battery's first reading in the window = 100)")).toBe("95.8");
  });
  it("summarises the line, the threshold and the estimate in words", () => {
    const text = sparkSummary({
      label: "acme-thinkpad-8",
      unit: "Capacity index (the battery's first reading in the window = 100)",
      points,
      threshold,
      projection,
    });
    expect(text).toContain("acme-thinkpad-8");
    expect(text).toContain("100 on May 14");
    expect(text).toContain("95 on Jun 12");
    expect(text).toContain("Our service threshold");
    expect(text).toContain("Jul 21");
    expect(text).toContain("estimate");
  });
});
