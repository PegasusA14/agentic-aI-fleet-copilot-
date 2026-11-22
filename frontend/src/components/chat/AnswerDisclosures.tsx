import { ListTree } from "lucide-react";
import { useEffect, useRef } from "react";
import { plural } from "../../lib/format";
import { usePrefersReducedMotion } from "../../lib/motion";
import type { NumberedEvidence } from "../../lib/answerTable";
import type { AnswerEnvelope } from "../../lib/types";
import { Disclosure } from "./Disclosure";

/** The reading a Source chip pointed at: its number, and a counter so pressing the same chip twice still re-highlights. */
export interface HotSource {
  n: number;
  at: number;
}

/** How long a pointed-at reading stays lit. */
export const HOT_MS = 2400;

function EvidenceRows({ items, hot, rowId }: { items: NumberedEvidence[]; hot: HotSource | null; rowId: (n: number) => string }) {
  const reduce = usePrefersReducedMotion();
  const first = useRef(true);
  useEffect(() => {
    if (!hot) return;
    const row = document.getElementById(rowId(hot.n));
    if (!row) return;
    row.focus({ preventScroll: true });
    // A fold that is still opening has no height to scroll to yet.
    const wait = first.current && !reduce ? 220 : 0;
    first.current = false;
    const timer = window.setTimeout(() => row.scrollIntoView?.({ block: "nearest", behavior: reduce ? "auto" : "smooth" }), wait);
    return () => window.clearTimeout(timer);
  }, [hot, reduce, rowId]);
  return (
    <ol className="evlist" aria-label="Readings behind this answer">
      {items.map((item) => (
        <li key={item.ref} id={rowId(item.n)} className="evlist__row" tabIndex={-1} data-hot={hot?.n === item.n || undefined}>
          <span className="srcchip srcchip--static">{item.n}</span>
          <span className="evlist__host">{item.hostname}</span>
          <span className="evlist__what">
            {item.field} {item.value}
          </span>
          {item.when ? (
            <span className="evlist__when" title={item.collected ? `Collected ${item.collected}` : undefined}>
              {item.when}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

interface AnswerDisclosuresProps {
  envelope: AnswerEnvelope;
  numbered: NumberedEvidence[];
  /** Replayed stand-in answers say so here. */
  note?: string;
  open: { evidence: boolean; notChecked: boolean; how: boolean };
  onOpen: (which: "evidence" | "notChecked" | "how", open: boolean) => void;
  hot: HotSource | null;
  rowId: (n: number) => string;
  onOpenTrace: () => void;
}

/** Evidence, Not checked and How I got this: three folds under a list answer, one hairline card. */
export function AnswerDisclosures({ envelope, numbered, note, open, onOpen, hot, rowId, onOpenTrace }: AnswerDisclosuresProps) {
  const { scope, coverage } = envelope;
  const excluded = coverage?.excluded_devices ?? [];
  const notChecked = Math.max(envelope.scope_line?.not_checked ?? 0, excluded.length);
  const stale = coverage?.stale.length ?? 0;
  const missing = coverage?.missing_latest.length ?? 0;
  return (
    <div className="discs">
      {numbered.length > 0 ? (
        <Disclosure
          title="Evidence"
          meta={plural(numbered.length, "record")}
          open={open.evidence}
          onOpenChange={(value) => onOpen("evidence", value)}
        >
          <EvidenceRows items={numbered} hot={hot} rowId={rowId} />
        </Disclosure>
      ) : null}
      {notChecked > 0 && excluded.length > 0 ? (
        <Disclosure
          title="Not checked"
          meta={plural(notChecked, "device")}
          open={open.notChecked}
          onOpenChange={(value) => onOpen("notChecked", value)}
        >
          <ul className="nclist" aria-label="Devices this check did not cover">
            {excluded.map((device) => (
              <li key={device.device_id} className="nclist__row">
                <span className="nclist__host">{device.label}</span>
                <span className="nclist__serial mono">{device.device_id}</span>
                <span className="nclist__why">{device.reason_text}</span>
              </li>
            ))}
          </ul>
          {notChecked > excluded.length ? (
            <p className="disc__more">{plural(notChecked - excluded.length, "more device")} not listed here.</p>
          ) : null}
        </Disclosure>
      ) : null}
      <Disclosure title="How I got this" open={open.how} onOpenChange={(value) => onOpen("how", value)}>
        <dl className="how">
          {scope.rule ? (
            <div>
              <dt>Rule</dt>
              <dd>{scope.rule}</dd>
            </div>
          ) : null}
          {scope.calculation ? (
            <div>
              <dt>How it was counted</dt>
              <dd>{scope.calculation}</dd>
            </div>
          ) : null}
          {stale > 0 ? (
            <div>
              <dt>Stale readings</dt>
              <dd>{plural(stale, "device")}</dd>
            </div>
          ) : null}
          {missing > 0 ? (
            <div>
              <dt>No latest reading</dt>
              <dd>{plural(missing, "device")}</dd>
            </div>
          ) : null}
        </dl>
        {note ? <p className="disc__note">Simulated example. {note}</p> : null}
        <div className="details__actions">
          <button type="button" className="link-btn" onClick={onOpenTrace}>
            <ListTree size={15} aria-hidden="true" />
            Show work
          </button>
        </div>
      </Disclosure>
    </div>
  );
}
