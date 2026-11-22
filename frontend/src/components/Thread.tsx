import { motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CROSSFADE, ENTER, Presence, usePrefersReducedMotion } from "../lib/motion";
import { firstSentence, replyOf } from "../lib/reply";
import { revealDelta } from "../lib/reveal";
import type { AnswerEnvelope, ProposalCard, TraceEvent } from "../lib/types";
import { AssistantMessage } from "./chat/AssistantMessage";
import { repeatText } from "./chat/RepeatCard";
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
  /** The earlier turn this reply repeats, when it is in this thread: where "Jump to it" goes. */
  earlier?: TurnData;
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
  onEditDetails?: (text: string) => void;
  /** The refusal card's "Switch company": the same action as the top bar's button. */
  onSwitchCompany?: () => void;
}

function FailureMessage({ turn, onAsk }: { turn: TurnData; onAsk: (question: string) => void }) {
  const reduce = usePrefersReducedMotion();
  return (
    <motion.article
      className="msg msg--assistant"
      data-outcome="tool_error"
      aria-label="Copilot reply"
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={ENTER}
      exit={reduce ? undefined : { opacity: 0, transition: CROSSFADE }}
    >
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
    </motion.article>
  );
}

/** Space kept between a revealed box and the scroller's edge; the arriving reply is still rising 6px. */
const REVEAL_MARGIN = 16;

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function scrollBehavior(): ScrollBehavior {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION).matches ? "auto" : "smooth";
}

/** Brings an earlier turn to the top of the thread, and hands it focus so a keyboard reader lands there too. */
function jumpTo(turnId: string) {
  const target = document.getElementById(`turn-${turnId}`);
  if (!target) return;
  target.scrollIntoView?.({ block: "start", behavior: scrollBehavior() });
  target.focus({ preventScroll: true });
}

function TurnView({ turn, earlier, last, busy, onOpenEvidence, onOpenTrace, onAsk, decide, onDecided, onSessionEnded, onEditDetails, onSwitchCompany }: TurnViewProps) {
  const root = useRef<HTMLElement>(null);
  const previous = useRef(turn.status);
  /** What a screen reader hears when the reply lands. The region stays mounted so the change is announced. */
  const [announcement, setAnnouncement] = useState("");

  /**
   * Brings a fresh turn into view: its question at the top, unless that would leave the proposal's Approve / Reject row
   * (or the reply's headline) out of sight, in which case that is seated above the docked composer instead.
   */
  const reveal = useCallback(() => {
    const section = root.current;
    const question = section?.querySelector(".msg--user");
    if (!section || !question) return;
    const scroller = section.closest<HTMLElement>(".stage__main");
    if (!scroller || typeof scroller.scrollTo !== "function") {
      question.scrollIntoView?.({ block: "start", behavior: scrollBehavior() });
      return;
    }
    const rect = (el: Element | null | undefined) => el?.getBoundingClientRect() ?? null;
    const delta = revealDelta({
      scroller: scroller.getBoundingClientRect(),
      composer: rect(document.querySelector(".stage__composer")),
      margin: REVEAL_MARGIN,
      question: question.getBoundingClientRect(),
      headline: rect(section.querySelector(".msg--assistant .msg__lead")),
      decision: rect(section.querySelector(".proposal__actions")),
    });
    scroller.scrollTo({ top: scroller.scrollTop + delta, behavior: scrollBehavior() });
  }, []);

  useEffect(() => {
    if (turn.fresh) {
      // When the turn starts, and again as the answer lands and grows it.
      reveal();
    } else if (last) {
      // A reopened conversation lands at its end, without travelling there.
      root.current?.scrollIntoView?.({ block: "end", behavior: "auto" });
    }
  }, [turn.status, turn.fresh, last, reveal]);

  useEffect(() => {
    if (previous.current === "working" && turn.status !== "working") {
      const envelope = turn.envelope;
      const words =
        turn.status === "done" && envelope
          ? envelope.repeat_of && !envelope.proposal
            ? repeatText(envelope.scope_line?.as_of ?? envelope.scope.as_of)
            : replyOf(envelope).lead
          : (turn.failure ?? "");
      setAnnouncement(`Reply ready. ${firstSentence(words)}`.trim());
    }
    previous.current = turn.status;
  }, [turn.status, turn.envelope, turn.failure]);

  return (
    <section
      className="turn"
      id={`turn-${turn.id}`}
      ref={root}
      tabIndex={-1}
      data-fresh={turn.fresh || undefined}
      aria-label={`Question: ${turn.question}`}
    >
      <UserMessage text={turn.question} fresh={turn.fresh} />
      {/* The working line fades out, then the answer arrives; each is its own keyed child so the swap is a cross-fade. */}
      <Presence mode="wait">
        {turn.status === "done" && turn.envelope ? (
          <AssistantMessage
            key="answer"
            envelope={turn.envelope}
            note={turn.note}
            busy={busy}
            onOpenEvidence={(refs) => onOpenEvidence(turn, refs)}
            onOpenTrace={() => onOpenTrace(turn)}
            onAsk={onAsk}
            question={turn.question}
            onJump={earlier ? () => jumpTo(earlier.id) : undefined}
            decide={decide}
            reopened={!turn.fresh}
            onArrive={turn.fresh ? reveal : undefined}
            onDecided={onDecided}
            onSessionEnded={onSessionEnded}
            onEditDetails={onEditDetails}
            onSwitchCompany={onSwitchCompany}
          />
        ) : turn.status === "failed" ? (
          <FailureMessage key="failed" turn={turn} onAsk={onAsk} />
        ) : (
          <WorkingLine key="working" events={turn.events} />
        )}
      </Presence>
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
      {turns.map((turn, index) => {
        const of = turn.envelope?.repeat_of;
        // The turn a repeat points at is found by trace id, among the turns before it.
        const earlier = of ? turns.slice(0, index).find((t) => t.envelope?.trace_id === of) : undefined;
        return <TurnView key={turn.id} turn={turn} earlier={earlier} last={index === turns.length - 1} busy={busy} {...rest} />;
      })}
    </div>
  );
}
