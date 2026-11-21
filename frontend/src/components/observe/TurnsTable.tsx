import { useState } from "react";
import { tenantName } from "../../lib/fixtures";
import { formatAsOf, REASON_TEXT } from "../../lib/format";
import { OUTCOMES } from "../../lib/outcomes";
import type { Transport } from "../../lib/transport";
import type { Outcome } from "../../lib/types";
import { StateMark } from "../StateMark";
import { TraceTimeline } from "./TraceTimeline";
import { useLoad } from "./useLoad";

interface TurnsTableProps {
  transport: Pick<Transport, "observeTurns" | "observeTrace">;
  /** Bumped by the console's Refresh button. */
  nonce: number;
  onSessionEnded: () => void;
}

function reasonWords(code: string | null): string {
  if (!code) return "—";
  return REASON_TEXT[code] ?? code.replaceAll("_", " ");
}

/** Recent turns across every company; choosing one shows its stored trace below. */
export function TurnsTable({ transport, nonce, onSessionEnded }: TurnsTableProps) {
  const { load, retry } = useLoad(() => transport.observeTurns(), [transport, nonce], onSessionEnded);
  const [selected, setSelected] = useState<string | null>(null);

  if (load.state === "loading") {
    return (
      <p className="fine" role="status">
        Loading recent turns…
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <div className="approvals__empty">
        <p role="alert" className="form-error">
          The recent turns could not be loaded. {load.detail}
        </p>
        <button type="button" className="link-btn" onClick={retry}>
          Try again
        </button>
      </div>
    );
  }
  if (load.data.length === 0) {
    return <p className="approvals__empty">No turns recorded yet. Questions asked by any company appear here.</p>;
  }

  return (
    <>
      <div className="obs__scroll">
        <table className="obs__table" aria-label="Recent turns">
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Company</th>
              <th scope="col">Actor</th>
              <th scope="col">Outcome</th>
              <th scope="col">Reason</th>
              <th scope="col" className="obs__num">
                Elapsed
              </th>
              <th scope="col">Model mode</th>
            </tr>
          </thead>
          <tbody>
            {load.data.map((turn) => {
              const open = selected === turn.trace_id;
              return (
                <tr key={turn.trace_id} data-selected={open}>
                  <td>
                    <button
                      type="button"
                      className="obs__open mono"
                      aria-pressed={open}
                      onClick={() => setSelected(open ? null : turn.trace_id)}
                    >
                      {formatAsOf(turn.started_at)}
                    </button>
                  </td>
                  <td>{tenantName(turn.company_id)}</td>
                  <td className="mono">{turn.actor_id}</td>
                  <td>
                    {turn.outcome in OUTCOMES ? (
                      <StateMark outcome={turn.outcome as Outcome} />
                    ) : (
                      <span>{turn.outcome.replaceAll("_", " ")}</span>
                    )}
                  </td>
                  <td>{reasonWords(turn.reason_code)}</td>
                  <td className="obs__num">{turn.elapsed_ms} ms</td>
                  <td>{turn.model_mode}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {selected ? <TraceTimeline transport={transport} traceId={selected} onSessionEnded={onSessionEnded} /> : null}
    </>
  );
}
