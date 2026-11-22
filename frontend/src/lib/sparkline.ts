/**
 * The sparkline's arithmetic, kept apart from the drawing so it can be tested without a browser: scales, the line and
 * projection paths, the labels on the time axis, and the nearest-snapshot lookup behind the crosshair.
 */
import type { InsightProjection, SeriesPoint, SeriesThreshold } from "../types/AnswerEnvelope";

export interface Size {
  width: number;
  height: number;
}

export type Coord = [number, number];

/** A series ready to draw: epoch milliseconds and values, the policy line, and where the estimate lands. */
export interface Prepared {
  t: number[];
  v: number[];
  threshold: number | null;
  /** The threshold's short name, written beside its line. */
  thresholdText: string | null;
  /** The estimated crossing: its day (read at noon UTC) and the value it crosses at. Null when there is no estimate. */
  projection: { t: number; v: number } | null;
  /** The ISO text of the first and last snapshot and of the estimate, for the axis. */
  firstAt: string;
  lastAt: string;
  estimateOn: string | null;
}

/** Space reserved around the plot, in px. The time axis sits under it, inside the same drawing. */
const PAD_LEFT = 8;
const PAD_RIGHT = 10;
const PAD_TOP = 8;
const AXIS = 18;
const PAD_BOTTOM = 6;
/** Room kept beside the threshold line for its name, on the side away from the data. */
const NAME_ROOM = 15;
const CHAR = 6.6;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `2026-06-12T09:02:00Z` or `2026-07-21` as "Jun 12", read in UTC so the day never shifts with the reader's zone. */
export function shortDate(iso: string): string {
  const month = Number(iso.slice(5, 7));
  const day = Number(iso.slice(8, 10));
  return `${MONTHS[month - 1] ?? ""} ${day}`;
}

/** An estimate arrives as a day; it is read at noon UTC, the middle of that day. */
function dayMiddle(day: string): number {
  return Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)), 12);
}

/** Null when there is nothing to draw: fewer than two snapshots. */
export function prepareSeries(
  points: readonly SeriesPoint[],
  threshold: SeriesThreshold | null,
  projection: InsightProjection | null,
): Prepared | null {
  if (points.length < 2) return null;
  const t = points.map((p) => Date.parse(p.at));
  const v = points.map((p) => p.value);
  const limit = threshold ? threshold.value : null;
  const estimate = projection && projection.status === "projected" && projection.estimate && limit !== null ? projection.estimate : null;
  return {
    t,
    v,
    threshold: limit,
    thresholdText: threshold ? thresholdName(threshold.label) : null,
    projection: estimate && limit !== null ? { t: dayMiddle(estimate), v: limit } : null,
    firstAt: points[0].at,
    lastAt: points[points.length - 1].at,
    estimateOn: estimate,
  };
}

export interface SparkLayout {
  size: Size;
  coords: Coord[];
  x: (t: number) => number;
  y: (v: number) => number;
  /** Where the threshold line sits, or null without one. */
  thresholdY: number | null;
  /** The threshold's name goes on the side of the line the data is not. */
  labelSide: "above" | "below";
  projection: { from: Coord; to: Coord } | null;
  domain: { min: number; max: number };
  plotTop: number;
  plotBottom: number;
  left: number;
  right: number;
  /** The baseline of the axis labels. */
  axisY: number;
}

/** The height a threshold's name takes beside its line. */
const NAME_HEIGHT = 13;

function build(series: Prepared, size: Size, side: "above" | "below", roomTop: number, roomBottom: number): SparkLayout {
  const { t, v, threshold, projection } = series;
  const plotTop = PAD_TOP + roomTop;
  const plotBottom = size.height - AXIS - PAD_BOTTOM - roomBottom;

  const all = threshold !== null ? [...v, threshold] : v;
  let min = Math.min(...all);
  let max = Math.max(...all);
  if (max - min < 1e-9) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.08;
  const domain = { min: min - pad, max: max + pad };

  const left = PAD_LEFT;
  const right = size.width - PAD_RIGHT;
  const t0 = t[0];
  const t1 = Math.max(t[t.length - 1], projection ? projection.t : -Infinity);
  const x = (time: number) => left + ((time - t0) / (t1 - t0 || 1)) * (right - left);
  const y = (value: number) => plotBottom - ((value - domain.min) / (domain.max - domain.min)) * (plotBottom - plotTop);

  const coords = t.map((time, i): Coord => [x(time), y(v[i])]);
  const last = coords[coords.length - 1];
  return {
    size,
    coords,
    x,
    y,
    thresholdY: threshold !== null ? y(threshold) : null,
    labelSide: side,
    projection: projection ? { from: last, to: [x(projection.t), y(projection.v)] } : null,
    domain,
    plotTop,
    plotBottom,
    left,
    right,
    axisY: size.height - 3,
  };
}

/** True when the threshold's name, written at the left end of its line on this side, would sit on the series. */
function crossesName(layout: SparkLayout, series: Prepared): boolean {
  const ty = layout.thresholdY;
  if (ty === null || !series.thresholdText) return false;
  const reach = layout.left + series.thresholdText.length * CHAR + 6;
  const [from, to] = layout.labelSide === "above" ? [ty - NAME_HEIGHT - 2, ty + 2] : [ty - 2, ty + NAME_HEIGHT + 2];
  const touches = (x: number, y: number) => x <= reach && y >= from && y <= to;
  if (layout.coords.some(([x, y]) => touches(x, y))) return true;
  // the line between two snapshots can cross the name without a snapshot landing in it
  for (let i = 1; i < layout.coords.length; i++) {
    const [x0, y0] = layout.coords[i - 1];
    const [x1, y1] = layout.coords[i];
    if (x0 > reach) break;
    for (let k = 1; k < 8; k++) {
      const f = k / 8;
      if (touches(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)) return true;
    }
  }
  return false;
}

/**
 * Scales and coordinates for one series in a drawing of the given size. The y domain is padded to take in the
 * threshold, and the threshold's name goes above its line unless the series runs through that spot.
 */
export function sparkLayout(series: Prepared, size: Size): SparkLayout {
  if (series.threshold === null) return build(series, size, "above", 0, 0);
  let above = build(series, size, "above", 0, 0);
  if (above.thresholdY! < NAME_HEIGHT + 2) above = build(series, size, "above", NAME_ROOM, 0);
  if (!crossesName(above, series)) return above;
  let below = build(series, size, "below", 0, 0);
  if (below.thresholdY! > size.height - AXIS - NAME_HEIGHT - 2) below = build(series, size, "below", 0, NAME_ROOM);
  return below;
}

const one = (n: number) => String(Math.round(n * 10) / 10);

/** An SVG path through the points, to one decimal. */
export function linePath(coords: readonly Coord[]): string {
  return coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${one(x)} ${one(y)}`).join("");
}

/** The index of the snapshot whose x is nearest `px`: the crosshair snaps to a reading, never to the line between two. */
export function nearestIndex(xs: readonly number[], px: number): number {
  let best = 0;
  for (let i = 1; i < xs.length; i++) {
    if (Math.abs(xs[i] - px) < Math.abs(xs[best] - px)) best = i;
  }
  return best;
}

/** Where an arrow key, Home or End takes the crosshair; null for any other key. */
export function stepIndex(current: number, key: string, count: number): number | null {
  switch (key) {
    case "ArrowLeft":
    case "ArrowDown":
      return Math.max(0, current - 1);
    case "ArrowRight":
    case "ArrowUp":
      return Math.min(count - 1, current + 1);
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

export interface AxisLabel {
  text: string;
  x: number;
  anchor: "start" | "middle" | "end";
}

/**
 * The only labels on the time axis: the first date, today, and the estimate. When two would touch, the first date goes,
 * then today: the estimate is the one a reader came for.
 */
export function axisLabels(layout: SparkLayout, series: Prepared): AxisLabel[] {
  const { size, left, right } = layout;
  const todayX = layout.coords[layout.coords.length - 1][0];
  const width = (text: string) => text.length * CHAR;

  const first: AxisLabel = { text: shortDate(series.firstAt), x: left, anchor: "start" };
  const todayText = `Today, ${shortDate(series.lastAt)}`;
  const half = width(todayText) / 2;
  const reachesRight = !series.estimateOn;
  const today: AxisLabel = reachesRight
    ? { text: todayText, x: right, anchor: "end" }
    : { text: todayText, x: Math.min(Math.max(todayX, half + 2), size.width - half - 2), anchor: "middle" };
  const est: AxisLabel | null = series.estimateOn ? { text: `${shortDate(series.estimateOn)} (est.)`, x: right, anchor: "end" } : null;

  const span = (l: AxisLabel): [number, number] => {
    const w = width(l.text);
    return l.anchor === "start" ? [l.x, l.x + w] : l.anchor === "end" ? [l.x - w, l.x] : [l.x - w / 2, l.x + w / 2];
  };
  const apart = (a: AxisLabel, b: AxisLabel) => span(a)[1] + 8 <= span(b)[0] || span(b)[1] + 8 <= span(a)[0];

  // Kept in order of importance; each must clear what is already kept.
  const kept: AxisLabel[] = [];
  for (const label of est ? [est, today, first] : [today, first]) {
    if (kept.every((other) => apart(label, other))) kept.push(label);
  }
  return [first, today, est].filter((l): l is AxisLabel => l !== null && kept.includes(l));
}

/** A number as a reader says it: one decimal at most, a percent sign when the series is a percentage. */
export function valueText(value: number, unit: string): string {
  return `${one(value)}${unit.includes("%") ? "%" : ""}`;
}

/** The unit's name without its explanation: "Capacity index (the battery's first reading ...)" -> "Capacity index". */
export function unitName(unit: string): string {
  return unit.replace(/\s*\(.*$/, "").trim() || unit;
}

/** The threshold's name without its explanation. */
export const thresholdName = unitName;

export interface SummaryInput {
  label: string;
  unit: string;
  points: readonly SeriesPoint[];
  threshold: SeriesThreshold | null;
  projection: InsightProjection | null;
}

/** The chart in words, for a screen reader and for anyone who cannot read the colours. */
export function sparkSummary({ label, unit, points, threshold, projection }: SummaryInput): string {
  const first = points[0];
  const last = points[points.length - 1];
  const parts = [
    `${label}: ${unitName(unit)} over ${points.length} snapshots, ${valueText(first.value, unit)} on ${shortDate(first.at)} to ${valueText(last.value, unit)} on ${shortDate(last.at)}.`,
  ];
  if (threshold) parts.push(`${thresholdName(threshold.label)} is ${valueText(threshold.value, unit)}.`);
  if (threshold && projection && projection.status === "projected" && projection.estimate) {
    parts.push(`A straight-line estimate reaches it around ${shortDate(projection.estimate)}.`);
  }
  return parts.join(" ");
}
