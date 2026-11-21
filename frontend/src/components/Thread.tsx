import { useEffect, useRef } from "react";
import type { AnswerEnvelope, ProposalCard, TraceEvent } from "../lib/types";
import { AssistantMessage } from "./chat/AssistantMessage";
import { UserMessage } from "./chat/UserMessage";
import { WorkingLine } from "./chat/WorkingLine";
import type { Decide } from "./ProposalView";
import { StateMark } from "./StateMark";

export interface TurnData {
  id: string;
  question: string;
  /** Every trace event streamed so far, in arrival order. */
  events: TraceEvent[];
  status: "working" | "done" | "failed";
  envelope?: AnswerEnvelope;
  /** Present on replayed stand-in answers; shown under Details. */
  note?: string;
  /** Present when the turn failed before an answer: the words shown in the reply. */
  failure?: string;
}

interface TurnViewProps {
  turn: TurnData;
  /** A turn is running somewhere in the thread; chips in every reply wait. */
  busy: boolean;
  onOpenEvidence: (turn: TurnData, refs?: string[]) => void;
  onOpenTrace: (turn: TurnData) => void;
  onAsk: (question: string) => void;
  decide?: Decide;
  onDecided?: (card: ProposalCard) => void;
  onSessionEnded?: () => void;
}

function FailureMessage({ turn, onAsk }: { turn: TurnData; onAsk: (question: string) => void }) {
  return (
    <article className="msg msg--assistant" data-outcome="tool_error" aria-label="Copilot reply">
      <div className="msg__state">
        <StateMark outcome="tool_error" />
      </div>
      <p className="msg__lead">{turn.failure}</p>
      <p className="msg__muted">No answer was produced. Check the connection, then ask again.</p>
      <div className="details__actions">
        <button type="button" className="link-btn" onClick={() => onAsk(turn.question)}>
          Ask again
        </button>
      </div>
    </article>
  );
}

function TurnView({ turn, busy, onOpenEvidence, onOpenTrace, onAsk, decide, onDecided, onSessionEnded }: TurnViewProps) {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    // Bring the turn into view when it starts and again once the full answer has grown it.
    root.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [turn.status]);

  return (
    <section className="turn" id={`turn-${turn.id}`} ref={root} aria-label={`Question: ${turn.question}`}>
      <UserMessage text={turn.question} />
      {turn.status === "done" && turn.envelope ? (
        <AssistantMessage
          envelope={turn.envelope}
          note={turn.note}
          busy={busy}
          onOpenEvidence={(refs) => onOpenEvidence(turn, refs)}
          onOpenTrace={() => onOpenTrace(turn)}
          onAsk={onAsk}
          decide={decide}
          onDecided={onDecided}
          onSessionEnded={onSessionEnded}
        />
      ) : turn.status === "failed" ? (
        <FailureMessage turn={turn} onAsk={onAsk} />
      ) : (
        <WorkingLine events={turn.events} />
      )}
    </section>
  );
}

interface ThreadProps extends Omit<TurnViewProps, "turn" | "busy"> {
  turns: TurnData[];
}

export function Thread({ turns, ...rest }: ThreadProps) {
  const busy = turns.some((turn) => turn.status === "working");
  return (
    <div className="thread">
      {turns.map((turn) => (
        <TurnView key={turn.id} turn={turn} busy={busy} {...rest} />
      ))}
    </div>
  );
}
