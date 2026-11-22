import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import {
  axisLabels,
  linePath,
  nearestIndex,
  prepareSeries,
  shortDate,
  sparkLayout,
  sparkSummary,
  stepIndex,
  thresholdName,
  unitName,
  valueText,
} from "../../lib/sparkline";
import type { InsightProjection, InsightSeries } from "../../types/AnswerEnvelope";

/** The drawing's height: the plot and its time axis. The width follows the column. */
export const SPARK_HEIGHT = 96;
const FALLBACK_WIDTH = 280;
const TIP_HEIGHT = 46;

interface SparklineProps {
  /** The device the line is about; named in the text summary. */
  label: string;
  series: InsightSeries;
  projection: InsightProjection | null;
  /** The line draws itself in. Only a reply that just arrived does; a reopened one is simply there. */
  animate: boolean;
}

/** The drawing's width, kept in step with its column. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const next = Math.round(el.getBoundingClientRect().width);
      if (next > 0) setWidth(next);
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * One series as a line: the readings in the accent, a dashed estimate to where it reaches our threshold, the threshold
 * as a dashed amber line named in ink. A crosshair snaps to the nearest snapshot by pointer or by arrow key and says
 * its value first. Hand-drawn SVG: one series needs no chart library.
 */
export function Sparkline({ label, series, projection, animate }: SparklineProps) {
  const [frame, width] = useWidth();
  const prepared = useMemo(
    () => prepareSeries(series.points, series.threshold, projection),
    [series.points, series.threshold, projection],
  );
  const layout = useMemo(() => (prepared ? sparkLayout(prepared, { width, height: SPARK_HEIGHT }) : null), [prepared, width]);
  const [active, setActive] = useState<number | null>(null);
  const pressed = useRef(false);

  const summary = useMemo(
    () => sparkSummary({ label, unit: series.unit, points: series.points, threshold: series.threshold, projection }),
    [label, series, projection],
  );

  const at = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!layout) return;
      const box = event.currentTarget.getBoundingClientRect();
      setActive(nearestIndex(layout.coords.map((c) => c[0]), event.clientX - box.left));
    },
    [layout],
  );

  if (!prepared || !layout) return null;
  const count = prepared.v.length;
  const last = count - 1;
  const [lastX, lastY] = layout.coords[last];
  const shown = active === null ? null : { x: layout.coords[active][0], y: layout.coords[active][1] };
  const labels = axisLabels(layout, prepared);
  const projected = layout.projection;
  const sideBelow = layout.labelSide === "below";
  const spoken = active === null ? "" : `${valueText(prepared.v[active], series.unit)} on ${shortDate(series.points[active].at)}`;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      setActive(null);
      return;
    }
    const next = stepIndex(active ?? last, event.key, count);
    if (next === null) return;
    event.preventDefault();
    setActive(next);
  }

  // The tip rides on the crosshair above the drawing, clear of the line, its threshold and the dates; it leans away from the nearer edge.
  const lean = shown ? (shown.x < 56 ? "start" : shown.x > width - 56 ? "end" : "middle") : "middle";

  return (
    <div className="spark" ref={frame} data-animate={animate || undefined}>
      <div
        className="spark__chart"
        role="img"
        aria-label={summary}
        tabIndex={0}
        style={{ height: SPARK_HEIGHT }}
        onPointerDown={() => {
          pressed.current = true;
        }}
        onPointerMove={at}
        onPointerLeave={() => {
          pressed.current = false;
          setActive(null);
        }}
        onFocus={() => {
          if (!pressed.current) setActive(last);
        }}
        onBlur={() => setActive(null)}
        onKeyDown={onKeyDown}
      >
        <svg width={width} height={SPARK_HEIGHT} viewBox={`0 0 ${width} ${SPARK_HEIGHT}`} aria-hidden="true" focusable="false">
          {layout.thresholdY !== null && series.threshold ? (
            <g className="spark__threshold">
              <line x1={layout.left} x2={layout.right} y1={layout.thresholdY} y2={layout.thresholdY} />
              <text x={layout.left} y={sideBelow ? layout.thresholdY + 13 : layout.thresholdY - 5}>
                {thresholdName(series.threshold.label)}
              </text>
            </g>
          ) : null}

          <g className="spark__draw" data-draw={animate || undefined}>
            <path className="spark__line" d={linePath(layout.coords)} />
          </g>

          {projected ? (
            <g className="spark__estimate" data-late={animate || undefined}>
              <path className="spark__proj" d={linePath([projected.from, projected.to])} />
            </g>
          ) : null}

          {shown ? <line className="spark__cross" x1={shown.x} x2={shown.x} y1={layout.plotTop - 2} y2={layout.plotBottom + 2} /> : null}

          <g className="spark__now" data-late={animate || undefined}>
            <circle cx={lastX} cy={lastY} r={4} />
          </g>
          {shown && active !== last ? <circle className="spark__pick" cx={shown.x} cy={shown.y} r={4} /> : null}

          {labels.map((l) => (
            <text key={l.text} className="spark__axis" x={l.x} y={layout.axisY} textAnchor={l.anchor}>
              {l.text}
            </text>
          ))}
        </svg>

        {shown && active !== null ? (
          <div
            className="spark__tip"
            aria-hidden="true"
            data-lean={lean}
            style={{ left: shown.x, top: -TIP_HEIGHT - 4 }}
          >
            <span className="spark__tipvalue">{valueText(prepared.v[active], series.unit)}</span>
            <span className="spark__tipwhen">
              {shortDate(series.points[active].at)} · {unitName(series.unit)}
            </span>
          </div>
        ) : null}
      </div>
      <span className="sr-only" aria-live="polite">
        {spoken}
      </span>
      {series.threshold ? (
        <p className="spark__key">
          <svg width="22" height="6" viewBox="0 0 22 6" aria-hidden="true">
            <line x1="1" x2="21" y1="3" y2="3" />
          </svg>
          <span>{series.threshold.label}</span>
        </p>
      ) : null}
    </div>
  );
}
