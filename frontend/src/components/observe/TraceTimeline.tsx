import { MarkDot } from "../StateMark";
import { formatValue, KIND_WORD } from "../../lib/trace";
import type { Transport } from "../../lib/transport";
import { useLoad } from "./useLoad";

interface TraceTimelineProps {
  transport: Pick<Transport, "observeTrace">;
  traceId: string;
  onSessionEnded: () => void;
}

/** One turn's stored steps, in order: what ran, with which tool, how long it took, and the redacted data. */
export function TraceTimeline({ transport, traceId, onSessionEnded }: TraceTimelineProps) {
  const { load, retry } = useLoad(() => transport.observeTrace(traceId), [transport, traceId], onSessionEnded);

  return (
    <section className="obs__trace" aria-label="Trace">
      <h2 className="obs__subhead">
        Trace <span className="mono">{traceId}</span>
      </h2>
      {load.state === "loading" ? (
        <p className="fine" role="status">
          Loading the trace…
        </p>
      ) : load.state === "failed" ? (
        <div className="approvals__empty">
          <p role="alert" className="form-error">
            The trace could not be loaded. {load.detail}
          </p>
          <button type="button" className="link-btn" onClick={retry}>
            Try again
          </button>
        </div>
      ) : load.data.events.length === 0 ? (
        <p className="approvals__empty">This turn stored no steps.</p>
      ) : (
        <ol className="timeline" aria-label="Trace steps">
          {load.data.events.map((event) => (
            <li key={event.seq} className="timeline__step" data-kind={event.kind}>
              <span className="timeline__seq mono">{event.seq}</span>
              <div className="timeline__body">
                <div className="timeline__head">
                  {event.kind === "error" ? (
                    <span className="mark" data-tone="alert">
                      <MarkDot />
                      <span>{KIND_WORD.error}</span>
                    </span>
                  ) : (
                    <span className="trace__kind">{KIND_WORD[event.kind]}</span>
                  )}
                  {event.tool ? <span className="mono timeline__tool">{event.tool}</span> : null}
                  {event.latency_ms !== null ? (
                    <span className="timeline__latency">{event.latency_ms} ms</span>
                  ) : null}
                </div>
                <p className="trace__summary">{event.summary}</p>
                {Object.keys(event.data).length > 0 ? (
                  <dl className="trace__data">
                    {Object.entries(event.data).map(([key, value]) => (
                      <div key={key} style={{ display: "contents" }}>
                        <dt>{key}</dt>
                        <dd>{formatValue(value)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
