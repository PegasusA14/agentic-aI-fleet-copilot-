import { ChevronDown, Eye, History, Check, TriangleAlert } from "lucide-react";
import { motion } from "motion/react";
import { useId, useState } from "react";
import { actionsFor, categoryLabel, evidenceLine, hasChart, historyRequest, severityWord } from "../../lib/insights";
import { FOLD_IN, FOLD_OUT, Presence, usePrefersReducedMotion } from "../../lib/motion";
import type { Insight, SuggestedAction } from "../../types/AnswerEnvelope";
import { Sparkline } from "./Sparkline";
import { focusComposer, SuggestionChips } from "./SuggestionChips";

interface InsightCardProps {
  insight: Insight;
  /** The answer's suggested actions; the ones about this device are offered on the card. */
  actions: readonly SuggestedAction[];
  busy: boolean;
  onAsk: (request: string) => void;
  /** A quieter card folds to a line and opens on request. */
  compact?: boolean;
  /** The reply was stored in an earlier session: its chart does not draw itself in again. */
  reopened?: boolean;
}

/** The severity as a mark and a word, never a colour alone. */
function SeverityPill({ severity }: { severity: Insight["severity"] }) {
  const Icon = severity === "act_soon" ? TriangleAlert : severity === "watch" ? Eye : Check;
  return (
    <span className="insight__pill" data-severity={severity}>
      <Icon size={13} strokeWidth={2} aria-hidden="true" />
      {severityWord(severity)}
    </span>
  );
}

/** A reading longer than this sits under its label instead of beside it, so it wraps at a word and not at its edge. */
const LONG_VALUE = 20;

function Readings({ items }: { items: Insight["evidence_kv"] }) {
  return (
    <dl className="insight__kv">
      {items.map((item) => (
        <div key={item.label} data-long={item.value.length > LONG_VALUE || undefined}>
          <dt>{item.label}</dt>
          <dd className="mono">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * One finding about one device: what it is, the numbers behind it (with the line they draw), why it matters, how the
 * estimate was made, and what could be done, all of which needs a person's approval. A quieter finding folds to a line.
 */
export function InsightCard({ insight, actions, busy, onAsk, compact = false, reopened = false }: InsightCardProps) {
  const reduce = usePrefersReducedMotion();
  const bodyId = useId();
  const [open, setOpen] = useState(!compact);
  const folded = compact && !open;
  const mine = actionsFor(insight, actions);
  const chart = hasChart(insight);
  const request = historyRequest(insight);

  const body = (
    <div className="insight__body">
      <div className="insight__evidence">
        <p className="label">Evidence</p>
        <div className="insight__figures" data-chart={chart || undefined}>
          <Readings items={insight.evidence_kv} />
          {chart ? (
            <Sparkline label={insight.label} series={insight.series} projection={insight.projection} animate={!reopened && !reduce} />
          ) : null}
        </div>
      </div>

      <div className="insight__why">
        <p className="label">Why this matters</p>
        <p className="insight__text">{insight.why}</p>
      </div>

      <div className="insight__foot">
        <p className="insight__method">{insight.method}</p>
        <div className="insight__actions">
          <SuggestionChips actions={mine} busy={busy} onAsk={onAsk} compact />
          <button
            type="button"
            className="chip insight__history"
            disabled={busy}
            title={request}
            onClick={() => {
              onAsk(request);
              focusComposer();
            }}
          >
            <History size={14} aria-hidden="true" />
            View history
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <article className="insight" data-severity={insight.severity} data-compact={folded || undefined} aria-label={`${severityWord(insight.severity)}: ${insight.label}`}>
      <header className="insight__head">
        <SeverityPill severity={insight.severity} />
        <span className="insight__cat">{categoryLabel(insight)}</span>
        <span className="insight__serial mono" title="Serial number">
          {insight.device_id}
        </span>
      </header>

      <div className="insight__finding">
        {folded ? null : <p className="label">Finding</p>}
        <p className="insight__line">{insight.finding}</p>
      </div>

      {compact ? (
        <>
          <div className="insight__panel" id={bodyId} data-open={open} inert={!open} aria-hidden={!open}>
            <Presence>
              {open ? (
                <motion.div
                  key="body"
                  className="insight__clip"
                  initial={reduce ? false : { height: 0, opacity: 0, overflow: "hidden" }}
                  // Clipped while it folds; free once open, so a chart's tooltip is not cut off at the top.
                  animate={{ height: "auto", opacity: 1, overflow: "hidden", transition: FOLD_IN, transitionEnd: { overflow: "visible" } }}
                  exit={{ height: 0, opacity: 0, overflow: "hidden", transition: FOLD_OUT }}
                >
                  {body}
                </motion.div>
              ) : null}
            </Presence>
          </div>
          <div className="insight__peek">
            {folded ? (
              <p className="insight__brief">
                {evidenceLine(insight).map((item) => (
                  <span key={item.label}>
                    {item.label} <span className="mono">{item.value}</span>
                  </span>
                ))}
              </p>
            ) : null}
            <button
              type="button"
              className="insight__more"
              aria-expanded={open}
              aria-controls={bodyId}
              onClick={() => setOpen((now) => !now)}
            >
              <ChevronDown size={15} strokeWidth={2} aria-hidden="true" />
              {open ? "Show less" : "Show details"}
            </button>
          </div>
        </>
      ) : (
        body
      )}
    </article>
  );
}
