import { useEffect, useRef, useState } from "react";
import { firstSentence, replyOf } from "../lib/reply";
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
  /** Asked in this session. A reopened turn is not fresh: it appears at once and the thread jumps to the bottom. */
  fresh?: boolean;
}

interface TurnViewProps {
  turn: TurnData;
  /** The newest turn in the thread: where a reopened conversation lands. */
  last: boolean;
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

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function scrollBehavior(): ScrollBehavior {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION).matches ? "auto" : "smooth";
}

function TurnView({ turn, last, busy, onOpenEvidence, onOpenTrace, onAsk, decide, onDecided, onSessionEnded }: TurnViewProps) {
  const root = useRef<HTMLElement>(null);
  const previous = useRef(turn.status);
  /** What a screen reader hears when the reply lands. The region stays mounted so the change is announced. */
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (turn.fresh) {
      // Bring the question into view when the turn starts and again once the full answer has grown it.
      root.current?.querySelector(".msg--user")?.scrollIntoView?.({ block: "start", behavior: scrollBehavior() });
    } else if (last) {
      // A reopened conversation lands at its end, without travelling there.
      root.current?.scrollIntoView?.({ block: "end", behavior: "auto" });
    }
  }, [turn.status, turn.fresh, last]);

  useEffect(() => {
    if (previous.current === "working" && turn.status !== "working") {
      const words = turn.status === "done" && turn.envelope ? replyOf(turn.envelope).lead : (turn.failure ?? "");
      setAnnouncement(`Reply ready. ${firstSentence(words)}`.trim());
    }
    previous.current = turn.status;
  }, [turn.status, turn.envelope, turn.failure]);

  return (
    <section
      className="turn"
      id={`turn-${turn.id}`}
      ref={root}
      data-fresh={turn.fresh || undefined}
      aria-label={`Question: ${turn.question}`}
    >
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
          reopened={!turn.fresh}
          onDecided={onDecided}
          onSessionEnded={onSessionEnded}
        />
      ) : turn.status === "failed" ? (
        <FailureMessage turn={turn} onAsk={onAsk} />
      ) : (
        <WorkingLine events={turn.events} />
      )}
      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>
    </section>
  );
}

interface ThreadProps extends Omit<TurnViewProps, "turn" | "last" | "busy"> {
  turns: TurnData[];
}

export function Thread({ turns, ...rest }: ThreadProps) {
  const busy = turns.some((turn) => turn.status === "working");
  return (
    <div className="thread">
      {turns.map((turn, index) => (
        <TurnView key={turn.id} turn={turn} last={index === turns.length - 1} busy={busy} {...rest} />
      ))}
    </div>
  );
}
