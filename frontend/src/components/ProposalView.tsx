import * as Dialog from "@radix-ui/react-dialog";
import { Check, Info, Shield, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, UNREACHABLE } from "../lib/api";
import { numberRefs, readingValue } from "../lib/answerTable";
import { auditLink } from "../lib/audit";
import {
  ACTION_TITLE,
  actionSentence,
  EFFECT_TABLE_WORDS,
  plainChecks,
  proposalFields,
  proposalResult,
  riskNote,
  scopeFacts,
  shortHash,
  STATUS_STYLE,
} from "../lib/proposals";
import type { Transport } from "../lib/transport";
import type { EvidenceItem, ProposalCard } from "../lib/types";
import { MarkDot } from "./StateMark";

/** How a person decides. `proposal`, when given, lets a reopened card ask where the proposal stands now. */
export type Decide = Pick<Transport, "approve" | "reject"> & Partial<Pick<Transport, "proposal">>;

interface ProposalViewProps {
  proposal: ProposalCard;
  /** The readings the answer holds for this card; a reference with no reading here still reads as what it names. */
  evidence?: readonly EvidenceItem[];
  /** Without it the card is read-only: it shows the proposal and offers no decision. */
  decide?: Decide;
  /** Called with the proposal as the server now holds it, after any decision that reached the server. */
  onDecided?: (card: ProposalCard) => void;
  /**
   * The card was stored with an earlier answer: ask the server once where it stands now, since it may have been
   * decided or invalidated since. Leave off for a card made in this session or listed straight from the server.
   */
  recheck?: boolean;
  /** Called when that check finds the proposal no longer pending. Nobody decided it here. */
  onSynced?: (card: ProposalCard) => void;
  onSessionEnded?: () => void;
  /** "Edit details": hands the composer a request to finish. Nothing is created until that request is sent. */
  onEditDetails?: (text: string) => void;
}

/** Mark plus word for where a proposal stands. */
function StatusMark({ status }: { status: ProposalCard["status"] }) {
  const style = STATUS_STYLE[status];
  return (
    <span className="mark" data-tone={style.tone} data-status={status}>
      <MarkDot hollow={style.mark === "hollow"} />
      <span>{style.word}</span>
    </span>
  );
}

function Scope({ card, label }: { card: ProposalCard; label: string }) {
  return (
    <dl className="facts proposal__facts" aria-label={label}>
      {scopeFacts(card.scope, card.target_device_id).map((fact) => (
        <div key={fact.key}>
          <dt>{fact.label}</dt>
          <dd className={fact.token ? "mono" : undefined}>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The short hash a person approves against: a quiet reference line, not a fact about the change. */
function ProposalHash({ card }: { card: ProposalCard }) {
  return (
    <p className="proposal__hash">
      Proposal <span className="mono">{shortHash(card.payload_hash)}</span>
    </p>
  );
}

/** The labelled facts at the top: the device with its serial, then what is specific to the action. */
function Fields({ card }: { card: ProposalCard }) {
  return (
    <dl className="pfields" aria-label="What this proposal covers">
      {proposalFields(card).map((field) => (
        <div key={field.key} className="pfields__item" data-field={field.key}>
          <dt>{field.label}</dt>
          <dd>
            <span className="pfields__value">{field.value}</span>
            {field.sub ? <span className="pfields__sub mono">{field.sub}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** The readings the proposal cites, numbered, right on the card. */
function EvidenceRows({ card, evidence }: { card: ProposalCard; evidence: readonly EvidenceItem[] }) {
  const labelId = useId();
  const items = numberRefs(card.evidence_refs, evidence);
  if (items.length === 0) return null;
  return (
    <div className="pblock">
      <p className="pblock__label" id={labelId}>
        Evidence
      </p>
      <ol className="pev" aria-labelledby={labelId}>
        {items.map((item) => (
          <li key={item.ref} className="pev__row">
            <span className="srcchip srcchip--static">{item.n}</span>
            <span className="pev__text">{plainChecks(readingValue(item.field, item.value))}</span>
            <span className="pev__when" title={item.collected ? `Collected ${item.collected}` : undefined}>
              {[item.field, item.when].filter(Boolean).join(" · ")}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

type Outcome =
  | { kind: "busy" }
  | { kind: "text"; text: string }
  | { kind: "error"; text: string };

/** What a decision that reached the server adds to the result line, when it has more to say. */
function describe(card: ProposalCard, result: { effect: { effect_id: number } | null; duplicate: boolean }): string {
  if (card.status !== "executed") return "";
  if (result.duplicate) return "Already executed — no second effect.";
  return result.effect
    ? `Recorded in ${EFFECT_TABLE_WORDS[card.action_type]} as entry ${result.effect.effect_id}. Nothing left this system.`
    : "Nothing left this system.";
}

const RESULT_ICON = { check: Check, x: X, warning: TriangleAlert } as const;

/** Only a person decides. Approval is confirmed against the exact hash they saw; every result is labelled simulated. */
export function ProposalView({
  proposal,
  evidence = [],
  decide,
  onDecided,
  recheck,
  onSynced,
  onSessionEnded,
  onEditDetails,
}: ProposalViewProps) {
  const [status, setStatus] = useState<ProposalCard["status"]>(proposal.status);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  /** The decision was made here, in this session, by the person looking at it. */
  const [byYou, setByYou] = useState(false);
  /** Set once the server has refused or answered in a way that closes the proposal to further decisions. */
  const [closed, setClosed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  /** True until a reopened pending card has heard from the server: the stored card shows, but cannot be acted on. */
  const [checking, setChecking] = useState(Boolean(recheck && proposal.status === "pending" && decide?.proposal));
  const sending = useRef(false);
  const cancel = useRef<HTMLButtonElement>(null);
  const result = useRef<HTMLElement | null>(null);
  const hold = (el: HTMLElement | null) => {
    result.current = el;
  };
  const riskId = useId();
  const sentence = actionSentence(proposal);
  const busy = outcome?.kind === "busy";
  /** Once decided, the buttons go: a dark disabled Approve would still read as available. */
  const decided = closed || status !== "pending";
  const locked = busy || decided || checking;
  const shown = proposalResult(proposal, status, byYou);

  // Once, when the card mounts: the stored card says "pending" whatever has happened since.
  useEffect(() => {
    if (!checking) return;
    let live = true;
    const lookup = decide?.proposal;
    if (!lookup) return;
    lookup(proposal.proposal_id).then(
      (current) => {
        if (!live) return;
        setChecking(false);
        if (current.status === "pending") return;
        setStatus(current.status);
        onSynced?.(current);
      },
      (error: unknown) => {
        if (!live) return;
        setChecking(false);
        if (error instanceof ApiError && error.status === 404) {
          setClosed(true);
          setOutcome({ kind: "text", text: "This proposal is not available." });
        } else if (error instanceof ApiError && error.status === 401) {
          onSessionEnded?.();
        }
        // Any other failure: the stored card stays, and the server still refuses a stale decision.
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a single look at mount, not on every render
  }, []);

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
      setByYou(true);
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
      setByYou(true);
      setClosed(true);
      setOutcome({ kind: "text", text: "" });
      onDecided?.(done.proposal);
    } catch (error) {
      handled(error);
    } finally {
      sending.current = false;
    }
  }

  const extra = outcome?.kind === "text" ? outcome.text : "";
  const Icon = shown ? RESULT_ICON[shown.icon] : null;

  return (
    <section className="proposal" aria-label="Proposed action" data-status={status}>
      <header className="proposal__bar">
        <h3 className="proposal__title">
          <Shield size={16} strokeWidth={1.75} aria-hidden="true" />
          {ACTION_TITLE[proposal.action_type]}
        </h3>
        <StatusMark status={status} />
      </header>

      <div className="proposal__body">
        <Fields card={proposal} />

        <div className="pblock">
          <p className="pblock__label">Why</p>
          <p className="proposal__rationale">{plainChecks(proposal.rationale)}</p>
        </div>

        <EvidenceRows card={proposal} evidence={evidence} />

        <p className="pnote" id={riskId}>
          <Info size={16} strokeWidth={1.75} aria-hidden="true" />
          <span>{riskNote(proposal)}</span>
        </p>
      </div>

      {decide && !decided ? (
        <div className="proposal__actions">
          {onEditDetails ? (
            <button
              type="button"
              className="proposal__edit"
              disabled={locked}
              onClick={() => onEditDetails(`Change the proposal for ${proposal.target_label}: `)}
            >
              Edit details
            </button>
          ) : (
            <span />
          )}
          <div className="proposal__decide">
            <button type="button" className="btn" disabled={locked} onClick={() => void reject()}>
              Reject
            </button>
            <Dialog.Root open={confirming} onOpenChange={setConfirming}>
              <Dialog.Trigger asChild>
                <button type="button" className="btn btn--approve" aria-describedby={riskId} disabled={locked}>
                  <Check size={16} strokeWidth={2.4} aria-hidden="true" />
                  Approve
                </button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="overlay overlay--fades" />
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
                  <ProposalHash card={proposal} />
                  <div className="modal__actions">
                    <button type="button" className="btn" ref={cancel} onClick={() => setConfirming(false)}>
                      Cancel
                    </button>
                    <button type="button" className="btn btn--approve" onClick={() => void approve()}>
                      Approve and execute (simulated)
                    </button>
                  </div>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          </div>
        </div>
      ) : null}

      {busy ? (
        <p className="proposal__busy" role="status">
          Recording your decision…
        </p>
      ) : shown && Icon ? (
        <div className="presult" data-tone={shown.tone} role="status" tabIndex={-1} ref={hold}>
          <span className="presult__icon" aria-hidden="true">
            <Icon size={16} strokeWidth={2.2} />
          </span>
          <div className="presult__text">
            <p className="presult__title">{shown.title}</p>
            <p className="presult__line">{shown.line}</p>
            {extra ? <p className="presult__line">{extra}</p> : null}
          </div>
          <a className="proposal__audit" href={auditLink(proposal.proposal_id)}>
            View in Audit
          </a>
        </div>
      ) : outcome?.kind === "text" ? (
        <p className="proposal__notice" role="status" tabIndex={-1} ref={hold}>
          {outcome.text}
        </p>
      ) : outcome?.kind === "error" ? (
        <p role="alert" tabIndex={-1} ref={hold} className="proposal__notice form-error">
          {outcome.text} Nothing was executed. Try again.
        </p>
      ) : null}
    </section>
  );
}
