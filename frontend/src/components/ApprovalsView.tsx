import { ArrowLeft } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { ApiError, UNREACHABLE } from "../lib/api";
import { GLIDE, usePrefersReducedMotion } from "../lib/motion";
import { actionSentence } from "../lib/proposals";
import type { Transport } from "../lib/transport";
import type { ProposalCard } from "../lib/types";
import { Count, CountedText } from "./Count";
import { ProposalView } from "./ProposalView";

interface ApprovalsViewProps {
  transport: Transport;
  /** Opens the evidence drawer on a proposal's own references; `subject` says which proposal they belong to. */
  onOpenEvidence: (refs: string[], subject: string) => void;
  /** A decision reached the server: the pending count in the navigation is stale. */
  onDecided: () => void;
  onSessionEnded: () => void;
}

type Load =
  | { state: "loading" }
  | { state: "failed"; detail: string }
  | { state: "ready"; cards: ProposalCard[] };

function count(n: number): string {
  if (n === 0) return "No proposals awaiting your decision";
  return `${n} ${n === 1 ? "proposal" : "proposals"} awaiting your decision`;
}

/** The count line; the number ticks when a decision changes it. */
function WaitingCount({ n }: { n: number }) {
  if (n === 0) return <>{count(0)}</>;
  return (
    <CountedText text={count(n)}>
      <Count value={n} /> {n === 1 ? "proposal" : "proposals"} awaiting your decision
    </CountedText>
  );
}

/** Everything in this company waiting for a person: the same card as in the thread, decided in place. */
export function ApprovalsView({ transport, onOpenEvidence, onDecided, onSessionEnded }: ApprovalsViewProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  /** Status of each card as decisions came back; the list itself is only fetched once per visit. */
  const [settled, setSettled] = useState<Record<string, ProposalCard["status"]>>({});
  const [attempt, setAttempt] = useState(0);
  const reduce = usePrefersReducedMotion();

  useEffect(() => {
    let alive = true;
    setLoad({ state: "loading" });
    transport.proposals("pending").then(
      (cards) => alive && setLoad({ state: "ready", cards }),
      (error) => {
        if (!alive) return;
        if (error instanceof ApiError && error.status === 401) {
          onSessionEnded();
          return;
        }
        setLoad({ state: "failed", detail: error instanceof ApiError && error.status !== 0 ? error.detail : UNREACHABLE });
      },
    );
    return () => {
      alive = false;
    };
  }, [transport, attempt, onSessionEnded]);

  const decided = useCallback(
    (card: ProposalCard) => {
      setSettled((prev) => ({ ...prev, [card.proposal_id]: card.status }));
      onDecided();
    },
    [onDecided],
  );

  const waiting =
    load.state === "ready"
      ? load.cards.filter((card) => (settled[card.proposal_id] ?? card.status) === "pending").length
      : 0;

  return (
    <div className="approvals">
      <div className="approvals__head">
        <h1 className="approvals__title">Approvals</h1>
        <a className="link-btn" href="#/">
          <ArrowLeft size={15} aria-hidden="true" />
          Back to chat
        </a>
      </div>

      {load.state === "loading" ? (
        <p className="fine" role="status">
          Loading proposals…
        </p>
      ) : load.state === "failed" ? (
        <div className="approvals__empty">
          <p role="alert" className="form-error">
            The proposals could not be loaded. {load.detail}
          </p>
          <button type="button" className="link-btn" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
      ) : load.cards.length === 0 ? (
        <p className="approvals__empty">
          Nothing is waiting for your decision. Proposed actions from your questions appear here.
        </p>
      ) : (
        <>
          <p className="approvals__count" aria-live="polite">
            <WaitingCount n={waiting} />
          </p>
          <ul className="approvals__list" aria-label="Pending proposals">
            {load.cards.map((card) => (
              <motion.li key={card.proposal_id} layout={reduce ? false : "position"} transition={GLIDE}>
                <ProposalView
                  proposal={card}
                  decide={transport}
                  onOpenEvidence={(refs) => onOpenEvidence(refs, actionSentence(card))}
                  onDecided={decided}
                  onSessionEnded={onSessionEnded}
                />
              </motion.li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
