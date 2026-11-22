import { useState } from "react";
import { everythingElse, isCompact } from "../../lib/insights";
import type { NumberedEvidence } from "../../lib/answerTable";
import type { AnswerEnvelope } from "../../lib/types";
import { AnswerDisclosures, type HotSource } from "./AnswerDisclosures";
import { Disclosure } from "./Disclosure";
import { InsightCard } from "./InsightCard";

interface InsightListProps {
  envelope: AnswerEnvelope;
  numbered: NumberedEvidence[];
  note?: string;
  open: { evidence: boolean; notChecked: boolean; how: boolean };
  onOpen: (which: "evidence" | "notChecked" | "how", open: boolean) => void;
  hot: HotSource | null;
  rowId: (n: number) => string;
  onOpenTrace: () => void;
  busy: boolean;
  onAsk: (request: string) => void;
  reopened?: boolean;
}

/**
 * The cards of an insight answer in the order the answer gave them (already ranked), then one fold for everything the
 * answer left out because it shows nothing, then the shared folds. The headline and the next steps are the reply's own.
 */
export function InsightList({
  envelope,
  numbered,
  note,
  open,
  onOpen,
  hot,
  rowId,
  onOpenTrace,
  busy,
  onAsk,
  reopened,
}: InsightListProps) {
  const [restOpen, setRestOpen] = useState(false);
  const rest = everythingElse(envelope);
  const actions = envelope.suggested_actions ?? [];
  return (
    <>
      <div className="insights" role="list" aria-label="Findings, most urgent first">
        {envelope.insights.map((insight, index) => (
          <div role="listitem" key={`${insight.device_id}:${insight.category}`} className="insights__item">
            <InsightCard
              insight={insight}
              actions={actions}
              busy={busy}
              onAsk={onAsk}
              compact={isCompact(insight, index)}
              reopened={reopened}
            />
          </div>
        ))}
      </div>
      <AnswerDisclosures
        envelope={envelope}
        numbered={numbered}
        note={note}
        open={open}
        onOpen={onOpen}
        hot={hot}
        rowId={rowId}
        onOpenTrace={onOpenTrace}
        first={
          rest ? (
            <Disclosure title="Everything else" meta={rest.meta} open={restOpen} onOpenChange={setRestOpen}>
              <p className="disc__more">{rest.counts}</p>
              <p className="disc__more">{rest.source}</p>
              <p className="disc__note">None of these show a meaningful change, so none has a card.</p>
            </Disclosure>
          ) : null
        }
      />
    </>
  );
}
