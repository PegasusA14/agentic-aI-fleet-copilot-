import { ChevronDown, FileSearch, ListTree } from "lucide-react";
import { useId, useState } from "react";
import { EXCLUDED_TEXT, REASON_TEXT, plural } from "../../lib/format";
import { replyOf } from "../../lib/reply";
import type { AnswerEnvelope, EntityList, ProposalCard } from "../../lib/types";
import { ProposalView, type Decide } from "../ProposalView";
import { StateMark } from "../StateMark";
import { SuggestionChips, focusComposer } from "./SuggestionChips";

interface AssistantMessageProps {
  envelope: AnswerEnvelope;
  /** Present on replayed stand-in answers; shown under Details. */
  note?: string;
  /** A turn is running: every chip in the message waits. */
  busy: boolean;
  onOpenEvidence: (refs?: string[]) => void;
  onOpenTrace: () => void;
  onAsk: (question: string) => void;
  /** How a person decides the proposal on this answer. Without it the proposal is shown read-only. */
  decide?: Decide;
  onDecided?: (card: ProposalCard) => void;
  /** The answer was stored in an earlier session: its proposal may have been decided since. */
  reopened?: boolean;
  onSessionEnded?: () => void;
}

/** One claim reads as a sentence; several read as a list. */
function Claims({ claims }: { claims: AnswerEnvelope["claims"] }) {
  if (claims.length === 1) return <p className="msg__claim">{claims[0].text}</p>;
  return (
    <ul className="msg__claims">
      {claims.map((claim) => (
        <li key={claim.text}>{claim.text}</li>
      ))}
    </ul>
  );
}

function DeviceChips({ list }: { list: EntityList }) {
  const titleId = useId();
  return (
    <div className="devlist">
      <p className="devlist__title" id={titleId}>
        {list.items.length > 1 ? `${list.title} — in this order` : list.title}
      </p>
      <ol className="devchips" aria-labelledby={titleId}>
        {list.items.map((item) => (
          <li key={item.device_id} className="devchip">
            <span className="devchip__rank" aria-label={`Number ${item.rank}`}>
              {item.rank}
            </span>
            <span className="devchip__name">{item.label}</span>
            <span className="devchip__id mono">{item.device_id}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Facts({ envelope }: { envelope: AnswerEnvelope }) {
  const { scope, coverage } = envelope;
  if (!scope.rule && !scope.calculation && !coverage && !envelope.reason_code) return null;
  const excluded = coverage ? Object.entries(coverage.excluded) : [];
  return (
    <dl className="facts" aria-label="Scope and coverage">
      {scope.rule ? (
        <div className="wide">
          <dt>Rule used</dt>
          <dd>{scope.rule}</dd>
        </div>
      ) : null}
      {scope.calculation ? (
        <div className="wide">
          <dt>How it was counted</dt>
          <dd>{scope.calculation}</dd>
        </div>
      ) : null}
      {coverage ? (
        <>
          <div>
            <dt>Evaluated</dt>
            <dd>{plural(coverage.population, "device")}</dd>
          </div>
          <div>
            <dt>Stale readings</dt>
            <dd>{coverage.stale.length === 0 ? "None" : plural(coverage.stale.length, "device")}</dd>
          </div>
          <div>
            <dt>No latest reading</dt>
            <dd>{coverage.missing_latest.length === 0 ? "None" : plural(coverage.missing_latest.length, "device")}</dd>
          </div>
          <div>
            <dt>Excluded</dt>
            <dd>
              {excluded.length === 0
                ? "None"
                : excluded
                    .map(([reason, ids]) => `${ids.length} ${EXCLUDED_TEXT[reason] ?? reason.replaceAll("_", " ")}`)
                    .join("; ")}
            </dd>
          </div>
        </>
      ) : null}
      {envelope.reason_code ? (
        <div>
          <dt>Reason</dt>
          <dd>{REASON_TEXT[envelope.reason_code] ?? envelope.reason_code}</dd>
        </div>
      ) : null}
    </dl>
  );
}

/** The copilot's reply: the words first, then only what the person can act on. Everything else is one click down. */
export function AssistantMessage({
  envelope,
  note,
  busy,
  onOpenEvidence,
  onOpenTrace,
  onAsk,
  decide,
  onDecided,
  reopened,
  onSessionEnded,
}: AssistantMessageProps) {
  const detailsId = useId();
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);
  /** What the person decided, once they have: the reply then stops saying the proposal waits. */
  const [decision, setDecision] = useState<ProposalCard["status"] | null>(null);
  const { outcome } = envelope;
  const status = decision ?? envelope.proposal?.status;
  const { lead, explanation } = replyOf(envelope, status);
  const clarification = envelope.clarification;
  const suggestions = envelope.suggested_actions ?? [];
  /** The reply carries the claims only when nothing else already says them: no explanation, no chips that name each action. */
  const claimsInReply = !explanation && !envelope.proposal && suggestions.length === 0 && envelope.claims.length > 0;
  const lists = suggestions.length > 0 ? [] : envelope.entity_lists;
  /** A proposal's card owns its state, so the reply carries no second mark above it. */
  const markShown = outcome !== "answer" && outcome !== "proposal";

  function pick(question: string) {
    onAsk(question);
    focusComposer();
  }

  return (
    <article className="msg msg--assistant" data-outcome={outcome} aria-label="Copilot reply">
      {markShown ? (
        <div className="msg__state">
          <StateMark outcome={outcome} />
        </div>
      ) : null}

      <p className="msg__lead" data-with-list={claimsInReply || undefined}>
        {lead}
      </p>

      {claimsInReply ? <Claims claims={envelope.claims} /> : null}

      {clarification ? (
        <div className="chips" role="group" aria-label="Choose one">
          {clarification.options.map((option) => (
            <button key={option} type="button" className="chip" disabled={busy} onClick={() => pick(option)}>
              {option}
            </button>
          ))}
        </div>
      ) : null}

      {lists.map((list) => (
        <DeviceChips key={list.list_id} list={list} />
      ))}

      <SuggestionChips actions={suggestions} busy={busy} onAsk={onAsk} withReason={!explanation} />

      {envelope.proposal ? (
        <ProposalView
          proposal={envelope.proposal}
          onOpenEvidence={onOpenEvidence}
          decide={decide}
          recheck={reopened}
          onSynced={(card) => setDecision(card.status)}
          onDecided={(card) => {
            setDecision(card.status);
            onDecided?.(card);
          }}
          onSessionEnded={onSessionEnded}
        />
      ) : null}

      {envelope.notes.length > 0 ? (
        <ul className="msg__notes" aria-label="Notes">
          {envelope.notes.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      ) : null}

      <div className="details">
        <button
          type="button"
          className="details__toggle"
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => {
            setOpened(true);
            setOpen((value) => !value);
          }}
        >
          <ChevronDown size={15} aria-hidden="true" />
          Details
        </button>
        {/* Closed, the panel is folded to nothing and inert; its content mounts on first open and stays for the close. */}
        <div className="details__panel" id={detailsId} data-open={open} inert={!open} aria-hidden={!open}>
          <div className="details__clip">
            {opened ? (
              <div className="details__body">
                {!claimsInReply && envelope.claims.length > 0 ? <Claims claims={envelope.claims} /> : null}
                <Facts envelope={envelope} />
                {note ? <p className="details__note">Simulated example. {note}</p> : null}
                <div className="details__actions">
                  {envelope.evidence.length > 0 ? (
                    <button type="button" className="link-btn" onClick={() => onOpenEvidence()}>
                      <FileSearch size={15} aria-hidden="true" />
                      Evidence ({envelope.evidence.length})
                    </button>
                  ) : null}
                  <button type="button" className="link-btn" onClick={onOpenTrace}>
                    <ListTree size={15} aria-hidden="true" />
                    Show work
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </article>
  );
}
