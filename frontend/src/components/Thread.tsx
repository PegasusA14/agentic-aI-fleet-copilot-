import { useEffect, useRef } from "react";
import type { AnswerEnvelope, TraceEvent } from "../lib/types";
import { AnswerSheet } from "./AnswerSheet";
import { StateMark } from "./StateMark";

export interface TurnData {
  id: string;
  question: string;
  /** Every trace event streamed so far, in arrival order. */
  events: TraceEvent[];
  status: "working" | "done" | "failed";
  envelope?: AnswerEnvelope;
  /** Present on replayed stand-in answers; shown under the sheet. */
  note?: string;
  /** Present when the turn failed before an answer: the words shown on the sheet. */
  failure?: string;
}

interface TurnViewProps {
  turn: TurnData;
  onOpenEvidence: (turn: TurnData, refs?: string[]) => void;
  onOpenTrace: (turn: TurnData) => void;
  onAsk: (question: string) => void;
}

function FailureSheet({ turn, onAsk }: { turn: TurnData; onAsk: (question: string) => void }) {
  const headingId = `h-failed-${turn.id}`;
  return (
    <article className="sheet" data-outcome="tool_error" aria-labelledby={headingId}>
      <div className="sheet__head">
        <StateMark outcome="tool_error" />
      </div>
      <h2 className="sheet__headline" id={headingId}>
        {turn.failure}
      </h2>
      <p className="sheet__explanation">No answer was produced. Check the connection, then ask again.</p>
      <div className="sheet__foot">
        <button type="button" className="link-btn" onClick={() => onAsk(turn.question)}>
          Ask again
        </button>
      </div>
    </article>
  );
}

function TurnView({ turn, onOpenEvidence, onOpenTrace, onAsk }: TurnViewProps) {
  const steps = turn.events.filter((event) => event.kind !== "respond");
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    // Bring the turn into view when it starts and again once the full answer has grown it.
    root.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [turn.status]);

  return (
    <section className="turn" id={`turn-${turn.id}`} ref={root} aria-label={`Question: ${turn.question}`}>
      <h2 className="turn__ask">{turn.question}</h2>
      {turn.status === "done" && turn.envelope ? (
        <AnswerSheet
          envelope={turn.envelope}
          note={turn.note}
          onOpenEvidence={(refs) => onOpenEvidence(turn, refs)}
          onOpenTrace={() => onOpenTrace(turn)}
          onAsk={onAsk}
        />
      ) : turn.status === "failed" ? (
        <FailureSheet turn={turn} onAsk={onAsk} />
      ) : (
        <ul className="working" aria-live="polite" aria-label="Working">
          {steps.map((event, index) => (
            <li key={event.seq}>
              <span className={index === steps.length - 1 ? "spinner" : "tick"} aria-hidden="true" />
              <span>{event.summary}</span>
            </li>
          ))}
          {steps.length === 0 ? (
            <li>
              <span className="spinner" aria-hidden="true" />
              <span>Reading your question…</span>
            </li>
          ) : null}
        </ul>
      )}
    </section>
  );
}

interface ThreadProps extends Omit<TurnViewProps, "turn"> {
  turns: TurnData[];
}

export function Thread({ turns, ...rest }: ThreadProps) {
  return (
    <div className="thread">
      {turns.map((turn) => (
        <TurnView key={turn.id} turn={turn} {...rest} />
      ))}
    </div>
  );
}
