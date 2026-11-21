import { ChevronDown, FileSearch, ListTree } from "lucide-react";
import { useId, useState } from "react";
import { EXCLUDED_TEXT, REASON_TEXT, plural } from "../../lib/format";
import { decidedSheet } from "../../lib/proposals";
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
  onSessionEnded?: () => void;
}

function DeviceChips({ list }: { list: EntityList }) {
  const titleId = useId();
  return (
    <div className="devlist">
      <p className="devlist__title" id={titleId}>
        {list.title} — in this order
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
  onSessionEnded,
}: AssistantMessageProps) {
  const detailsId = useId();
  const [open, setOpen] = useState(false);
  /** What the person decided, once they have: the reply then stops saying the proposal waits. */
  const [decision, setDecision] = useState<ProposalCard["status"] | null>(null);
  const { outcome } = envelope;
  const decided = envelope.proposal ? decidedSheet(decision ?? envelope.proposal.status, envelope.proposal) : null;
  const clarification = envelope.clarification;
  const explanation = !decided && !clarification ? envelope.explanation : null;
  const lead = decided ? decided.headline : clarification ? clarification.question : (explanation ?? envelope.headline);
  const claimsInReply = !explanation && !decided && envelope.claims.length > 0;
  const suggestions = envelope.suggested_actions ?? [];
  const lists = suggestions.length > 0 ? [] : envelope.entity_lists;
  const markShown = outcome !== "answer";

  function pick(question: string) {
    onAsk(question);
    focusComposer();
  }

  return (
    <article className="msg msg--assistant" data-outcome={outcome} aria-label="Copilot reply">
      {markShown ? (
        <div className="msg__state">
          <StateMark outcome={outcome} as={decided ?? undefined} />
        </div>
      ) : null}

      <p className="msg__lead" data-with-list={claimsInReply || undefined}>
        {lead}
      </p>

      {claimsInReply ? (
        <ul className="msg__claims">
          {envelope.claims.map((claim) => (
            <li key={claim.text}>{claim.text}</li>
          ))}
        </ul>
      ) : null}

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

      <SuggestionChips actions={suggestions} busy={busy} onAsk={onAsk} />

      {envelope.proposal ? (
        <ProposalView
          proposal={envelope.proposal}
          onOpenEvidence={onOpenEvidence}
          decide={decide}
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
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronDown size={15} aria-hidden="true" />
          Details
        </button>
        {open ? (
          <div className="details__body" id={detailsId}>
            {explanation && envelope.claims.length > 0 ? (
              <ul className="msg__claims">
                {envelope.claims.map((claim) => (
                  <li key={claim.text}>{claim.text}</li>
                ))}
              </ul>
            ) : null}
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
    </article>
  );
}
