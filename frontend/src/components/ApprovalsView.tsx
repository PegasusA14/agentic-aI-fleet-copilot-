import { ArrowLeft } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, UNREACHABLE } from "../lib/api";
import { pathWords } from "../lib/answerTable";
import { parseRef } from "../lib/format";
import { GLIDE, usePrefersReducedMotion } from "../lib/motion";
import type { Transport } from "../lib/transport";
import type { EvidenceItem, ProposalCard } from "../lib/types";
import { Count, CountedText } from "./Count";
import { ProposalView } from "./ProposalView";

interface ApprovalsViewProps {
  transport: Transport;
  /** "Edit details" on a card: takes the request to the composer in the chat. */
  onEditDetails?: (text: string) => void;
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
export function ApprovalsView({ transport, onEditDetails, onDecided, onSessionEnded }: ApprovalsViewProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  /** Status of each card as decisions came back; the list itself is only fetched once per visit. */
  const [settled, setSettled] = useState<Record<string, ProposalCard["status"]>>({});
  const [attempt, setAttempt] = useState(0);
  const reduce = usePrefersReducedMotion();
  /** The readings behind the listed cards, looked up once each so a card here shows what an inline one shows. */
  const [readings, setReadings] = useState<Record<string, EvidenceItem>>({});
  const asked = useRef(new Set<string>());

  useEffect(() => {
    if (load.state !== "ready" || typeof transport.evidence !== "function") return;
    for (const ref of new Set(load.cards.flatMap((card) => card.evidence_refs))) {
      if (asked.current.has(ref)) continue;
      asked.current.add(ref);
      transport.evidence(ref).then(
        (got) => {
          // A reading the server returns as a record is left to the card's own words for the reading it names.
          if (typeof got.value !== "string") return;
          const name = pathWords(parseRef(ref)?.path ?? "Reading");
          setReadings((prev) => ({ ...prev, [ref]: { ref, label: name, value: got.value as string } }));
        },
        () => {},
      );
    }
  }, [load, transport]);

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
                  evidence={card.evidence_refs.flatMap((ref) => (readings[ref] ? [readings[ref]] : []))}
                  decide={transport}
                  onEditDetails={onEditDetails}
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
