import type { TraceEvent } from "../../lib/types";

/** One line while a turn runs: the latest step. The full steps stay under Details, Show work, once the answer lands. */
export function WorkingLine({ events }: { events: TraceEvent[] }) {
  const steps = events.filter((event) => event.kind !== "respond");
  const latest = steps.length > 0 ? steps[steps.length - 1].summary : "Reading your question…";
  return (
    <p className="working" role="status" aria-label="Working">
      <span className="spinner" aria-hidden="true" />
      <span key={latest} className="working__step">
        {latest}
      </span>
    </p>
  );
}
