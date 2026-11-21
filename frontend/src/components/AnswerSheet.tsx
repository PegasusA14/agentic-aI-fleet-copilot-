import { FileSearch, ListTree } from "lucide-react";
import { useState } from "react";
import { EXCLUDED_TEXT, REASON_TEXT, formatAsOf, plural } from "../lib/format";
import { tenantName } from "../lib/fixtures";
import { decidedSheet } from "../lib/proposals";
import type { AnswerEnvelope, EntityList, ProposalCard } from "../lib/types";
import { ProposalView, type Decide } from "./ProposalView";
import { StateMark } from "./StateMark";

interface AnswerSheetProps {
  envelope: AnswerEnvelope;
  note?: string;
  onOpenEvidence: (refs?: string[]) => void;
  onOpenTrace: () => void;
  onAsk: (question: string) => void;
  /** How a person decides the proposal on this answer. Without it the proposal is shown read-only. */
  decide?: Decide;
  onDecided?: (card: ProposalCard) => void;
  onSessionEnded?: () => void;
}

function OrderedList({ list }: { list: EntityList }) {
  return (
    <div>
      <p className="list-title">{list.title} — in this order</p>
      <ol className="devices" aria-label={list.title}>
        {list.items.map((item) => (
          <li key={item.device_id}>
            <span className="rank" aria-label={`Number ${item.rank}`}>
              {item.rank}
            </span>
            <span>{item.label}</span>
            <span className="token mono">{item.device_id}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Facts({ envelope }: { envelope: AnswerEnvelope }) {
  const { scope, coverage } = envelope;
  if (!scope.rule && !coverage) return null;
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
            <dd>
              {coverage.missing_latest.length === 0 ? "None" : plural(coverage.missing_latest.length, "device")}
            </dd>
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

export function AnswerSheet({
  envelope,
  note,
  onOpenEvidence,
  onOpenTrace,
  onAsk,
  decide,
  onDecided,
  onSessionEnded,
}: AnswerSheetProps) {
  const headingId = `h-${envelope.trace_id}`;
  const { outcome } = envelope;
  /** What the person decided, once they have: the sheet then stops saying the proposal waits. */
  const [decision, setDecision] = useState<ProposalCard["status"] | null>(null);
  const decided = envelope.proposal ? decidedSheet(decision ?? envelope.proposal.status, envelope.proposal) : null;
  return (
    <article className="sheet" data-outcome={outcome} aria-labelledby={headingId}>
      <div className="sheet__head">
        <StateMark outcome={outcome} as={decided ?? undefined} />
        <span className="sheet__scope">
          {tenantName(envelope.scope.company_id)} · as of {formatAsOf(envelope.scope.as_of)}
        </span>
      </div>

      <h2 className="sheet__headline" id={headingId}>
        {decided ? decided.headline : envelope.headline}
      </h2>
      {envelope.explanation ? <p className="sheet__explanation">{envelope.explanation}</p> : null}

      {envelope.clarification ? (
        <>
          <p className="question">{envelope.clarification.question}</p>
          <div className="options" role="group" aria-label="Choose one">
            {envelope.clarification.options.map((option) => (
              <button key={option} type="button" className="chip" onClick={() => onAsk(option)}>
                {option}
              </button>
            ))}
          </div>
        </>
      ) : null}

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

      {envelope.claims.length > 0 ? (
        <ul className="claims">
          {envelope.claims.map((claim) => (
            <li key={claim.text} className="claim">
              <p>{claim.text}</p>
              <div className="claim__row">
                <button type="button" className="link-btn" onClick={() => onOpenEvidence(claim.refs)}>
                  <FileSearch size={15} aria-hidden="true" />
                  Show evidence ({claim.refs.length})
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {envelope.entity_lists.map((list) => (
        <OrderedList key={list.list_id} list={list} />
      ))}

      {envelope.notes.length > 0 ? (
        <ul className="notes" aria-label="Notes">
          {envelope.notes.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      ) : null}

      <Facts envelope={envelope} />

      {note ? <p className="sheet__scope">Simulated example. {note}</p> : null}

      <div className="sheet__foot">
        {envelope.evidence.length > 0 ? (
          <button type="button" className="link-btn" onClick={() => onOpenEvidence()}>
            <FileSearch size={15} aria-hidden="true" />
            All evidence ({envelope.evidence.length})
          </button>
        ) : null}
        <button type="button" className="link-btn" onClick={onOpenTrace}>
          <ListTree size={15} aria-hidden="true" />
          Show work
        </button>
      </div>
    </article>
  );
}
