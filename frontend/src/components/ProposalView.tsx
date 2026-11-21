import * as Dialog from "@radix-ui/react-dialog";
import { Check, ClipboardCheck, FileSearch, OctagonAlert, TriangleAlert, X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { ApiError, UNREACHABLE } from "../lib/api";
import {
  actionSentence,
  EFFECT_TABLE_WORDS,
  scopeFacts,
  shortHash,
  STATUS_STYLE,
  type StatusMarkName,
} from "../lib/proposals";
import type { Transport } from "../lib/transport";
import type { ProposalCard } from "../lib/types";

export type Decide = Pick<Transport, "approve" | "reject">;

interface ProposalViewProps {
  proposal: ProposalCard;
  onOpenEvidence: (refs: string[]) => void;
  /** Without it the card is read-only: it shows the proposal and offers no decision. */
  decide?: Decide;
  /** Called with the proposal as the server now holds it, after any decision that reached the server. */
  onDecided?: (card: ProposalCard) => void;
  onSessionEnded?: () => void;
}

const MARKS: Record<StatusMarkName, ReactNode> = {
  review: <ClipboardCheck size={16} strokeWidth={2.4} aria-hidden="true" />,
  check: <Check size={16} strokeWidth={2.6} aria-hidden="true" />,
  x: <X size={16} strokeWidth={2.6} aria-hidden="true" />,
  triangle: <TriangleAlert size={16} strokeWidth={2.4} aria-hidden="true" />,
  alert: <OctagonAlert size={16} strokeWidth={2.4} fill="currentColor" fillOpacity={0.16} aria-hidden="true" />,
  hollow: (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  ),
};

/** Mark plus word for where a proposal stands. */
function StatusMark({ status }: { status: ProposalCard["status"] }) {
  const style = STATUS_STYLE[status];
  return (
    <span className="mark" data-tone={style.tone} data-status={status}>
      {MARKS[style.mark]}
      <span>{style.word}</span>
    </span>
  );
}

function Scope({ card, label }: { card: ProposalCard; label: string }) {
  return (
    <dl className="facts proposal__facts" aria-label={label}>
      {scopeFacts(card.scope).map((fact) => (
        <div key={fact.key}>
          <dt>{fact.label}</dt>
          <dd className={fact.token ? "mono" : undefined}>{fact.value}</dd>
        </div>
      ))}
      <div>
        <dt>Proposal hash</dt>
        <dd className="mono">{shortHash(card.payload_hash)}</dd>
      </div>
    </dl>
  );
}

type Outcome =
  | { kind: "busy" }
  | { kind: "text"; text: string }
  | { kind: "error"; text: string };

function describe(card: ProposalCard, result: { effect: { effect_id: number } | null; duplicate: boolean }): string {
  switch (card.status) {
    case "executed":
      if (result.duplicate) return "Already executed — no second effect.";
      return result.effect
        ? `Executed. Recorded in ${EFFECT_TABLE_WORDS[card.action_type]} as entry ${result.effect.effect_id} (simulated). Nothing left this system.`
        : "Executed (simulated). Nothing left this system.";
    case "invalidated":
      return "This proposal no longer holds on the current data. Nothing was executed.";
    case "failed":
      return "The simulated action did not run, so nothing was recorded.";
    case "rejected":
      return "Rejected. Nothing was executed.";
    default:
      return `This proposal is ${STATUS_STYLE[card.status].word.toLowerCase()}.`;
  }
}

/** Only a person decides. Approval is confirmed against the exact hash they saw; every result is labelled simulated. */
export function ProposalView({ proposal, onOpenEvidence, decide, onDecided, onSessionEnded }: ProposalViewProps) {
  const [status, setStatus] = useState<ProposalCard["status"]>(proposal.status);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  /** Set once the server has refused or answered in a way that closes the proposal to further decisions. */
  const [closed, setClosed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const sending = useRef(false);
  const cancel = useRef<HTMLButtonElement>(null);
  const result = useRef<HTMLParagraphElement>(null);
  const effectId = useId();
  const sentence = actionSentence(proposal);
  const busy = outcome?.kind === "busy";
  /** Once decided, the buttons go: a dark disabled Approve would still read as available. */
  const decided = closed || status !== "pending";
  const locked = busy || decided;

  useEffect(() => {
    if (outcome && outcome.kind !== "busy") result.current?.focus();
  }, [outcome]);

  function handled(error: unknown) {
    if (error instanceof ApiError) {
      if (error.status === 401) {
        onSessionEnded?.();
        setOutcome(null);
        return;
      }
      if (error.status === 404) {
        setClosed(true);
        setOutcome({ kind: "text", text: "This proposal is not available." });
        return;
      }
      if (error.status === 409) {
        const now = /^proposal is (\w+)$/.exec(error.detail)?.[1] as ProposalCard["status"] | undefined;
        setClosed(true);
        if (now && now in STATUS_STYLE) {
          setStatus(now);
          onDecided?.({ ...proposal, status: now });
          setOutcome({ kind: "text", text: `This proposal is already ${now}. Nothing was executed.` });
        } else {
          setOutcome({ kind: "text", text: "This proposal changed since you saw it. Nothing was executed." });
        }
        return;
      }
      setOutcome({ kind: "error", text: error.status === 0 ? UNREACHABLE : error.detail });
      return;
    }
    setOutcome({ kind: "error", text: UNREACHABLE });
  }

  async function approve() {
    if (!decide || sending.current) return;
    sending.current = true;
    setConfirming(false);
    setOutcome({ kind: "busy" });
    try {
      const done = await decide.approve(proposal.proposal_id, proposal.payload_hash);
      setStatus(done.proposal.status);
      setClosed(true);
      setOutcome({ kind: "text", text: describe(done.proposal, done) });
      onDecided?.(done.proposal);
    } catch (error) {
      handled(error);
    } finally {
      sending.current = false;
    }
  }

  async function reject() {
    if (!decide || sending.current) return;
    sending.current = true;
    setOutcome({ kind: "busy" });
    try {
      const done = await decide.reject(proposal.proposal_id);
      setStatus(done.proposal.status);
      setClosed(true);
      setOutcome({ kind: "text", text: describe(done.proposal, { effect: null, duplicate: false }) });
      onDecided?.(done.proposal);
    } catch (error) {
      handled(error);
    } finally {
      sending.current = false;
    }
  }

  return (
    <section className="proposal" aria-label="Proposed action">
      <div className="proposal__head">
        <StatusMark status={status} />
      </div>
      <div className="proposal__target">
        <span>{sentence}</span>
        <span className="token mono">{proposal.target_device_id}</span>
      </div>
      <p className="proposal__rationale">{proposal.rationale}</p>
      <p className="proposal__effect" id={effectId}>
        {proposal.expected_effect}
      </p>
      <Scope card={proposal} label="What this proposal covers" />
      <div className="sheet__foot">
        <button type="button" className="link-btn" onClick={() => onOpenEvidence(proposal.evidence_refs)}>
          <FileSearch size={15} aria-hidden="true" />
          Evidence for this proposal ({proposal.evidence_refs.length})
        </button>
      </div>

      {decide && !decided ? (
        <div className="proposal__actions">
          <Dialog.Root open={confirming} onOpenChange={setConfirming}>
            <Dialog.Trigger asChild>
              <button type="button" className="btn btn--primary" aria-describedby={effectId} disabled={locked}>
                Approve
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="overlay" />
              <Dialog.Content
                className="modal"
                onOpenAutoFocus={(event) => {
                  event.preventDefault();
                  cancel.current?.focus();
                }}
              >
                <Dialog.Title className="modal__title">Approve this exact proposal?</Dialog.Title>
                <Dialog.Description className="modal__desc">
                  Nothing runs until you confirm. The action is simulated and recorded in the audit trail.
                </Dialog.Description>
                <div className="proposal__target">
                  <span>{sentence}</span>
                  <span className="token mono">{proposal.target_device_id}</span>
                </div>
                <Scope card={proposal} label="What you are approving" />
                <div className="modal__actions">
                  <button type="button" className="btn" ref={cancel} onClick={() => setConfirming(false)}>
                    Cancel
                  </button>
                  <button type="button" className="btn btn--primary" onClick={() => void approve()}>
                    Approve and execute (simulated)
                  </button>
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
          <button type="button" className="btn" disabled={locked} onClick={() => void reject()}>
            <X size={16} aria-hidden="true" />
            Reject
          </button>
        </div>
      ) : null}

      <div className="proposal__result">
        {outcome?.kind === "busy" ? (
          <p role="status">Recording your decision…</p>
        ) : outcome?.kind === "text" ? (
          <p role="status" tabIndex={-1} ref={result}>
            {outcome.text}
          </p>
        ) : outcome?.kind === "error" ? (
          <p role="alert" tabIndex={-1} ref={result} className="form-error">
            {outcome.text} Nothing was executed. Try again.
          </p>
        ) : null}
      </div>
    </section>
  );
}
