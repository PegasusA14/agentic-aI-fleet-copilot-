import { useEffect, useRef, useState } from "react";
import type { Fixture } from "../lib/fixtures";
import { AnswerSheet } from "./AnswerSheet";

export interface TurnData {
  id: string;
  question: string;
  fixture: Fixture;
}

interface TurnViewProps {
  turn: TurnData;
  stepMs: number;
  onSettled: (id: string) => void;
  onOpenEvidence: (turn: TurnData, refs?: string[]) => void;
  onOpenTrace: (turn: TurnData) => void;
  onAsk: (question: string) => void;
}

function TurnView({ turn, stepMs, onSettled, onOpenEvidence, onOpenTrace, onAsk }: TurnViewProps) {
  const steps = turn.fixture.trace.filter((event) => event.kind !== "respond");
  const [shown, setShown] = useState(0);
  const done = shown >= steps.length;
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    // Bring the turn into view when it starts and again once the full answer has grown it.
    root.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [done]);

  useEffect(() => {
    if (done) {
      onSettled(turn.id);
      return;
    }
    const timer = window.setTimeout(() => setShown((n) => n + 1), stepMs);
    return () => window.clearTimeout(timer);
  }, [done, shown, stepMs, turn.id, onSettled]);

  return (
    <section className="turn" id={`turn-${turn.id}`} ref={root} aria-label={`Question: ${turn.question}`}>
      <h2 className="turn__ask">{turn.question}</h2>
      {done ? (
        <AnswerSheet
          envelope={turn.fixture.envelope}
          note={turn.fixture.meta.synthetic ? turn.fixture.meta.note : undefined}
          onOpenEvidence={(refs) => onOpenEvidence(turn, refs)}
          onOpenTrace={() => onOpenTrace(turn)}
          onAsk={onAsk}
        />
      ) : (
        <ul className="working" aria-live="polite" aria-label="Working">
          {steps.slice(0, shown).map((event, index) => (
            <li key={event.seq}>
              <span className={index === shown - 1 ? "spinner" : "tick"} aria-hidden="true" />
              <span>{event.summary}</span>
            </li>
          ))}
          {shown === 0 ? (
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
