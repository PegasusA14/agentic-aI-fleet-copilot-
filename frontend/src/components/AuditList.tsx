import { Check, FileSearch, Lock, OctagonAlert, ClipboardCheck, TriangleAlert, X } from "lucide-react";
import type { ReactNode } from "react";
import { actionWords, type AuditMarkName, eventStyle, reasonWords } from "../lib/audit";
import { formatAsOf, plural } from "../lib/format";
import { tenantName } from "../lib/fixtures";
import type { AuditEntry } from "../lib/transport";

const MARKS: Record<AuditMarkName, ReactNode> = {
  review: <ClipboardCheck size={16} strokeWidth={2.4} aria-hidden="true" />,
  check: <Check size={16} strokeWidth={2.6} aria-hidden="true" />,
  x: <X size={16} strokeWidth={2.6} aria-hidden="true" />,
  triangle: <TriangleAlert size={16} strokeWidth={2.4} aria-hidden="true" />,
  alert: <OctagonAlert size={16} strokeWidth={2.4} fill="currentColor" fillOpacity={0.16} aria-hidden="true" />,
  lock: <Lock size={16} strokeWidth={2.4} aria-hidden="true" />,
  hollow: (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  ),
};

/** Mark plus word for what happened. */
function EventMark({ event }: { event: string }) {
  const style = eventStyle(event);
  return (
    <span className="mark" data-tone={style.tone} data-event={event}>
      {MARKS[style.mark]}
      <span>{style.word}</span>
    </span>
  );
}

interface AuditListProps {
  /** Newest first, as the server returns them. */
  entries: AuditEntry[];
  /** The operator's list spans tenants, so each row also says whose it is and who acted. */
  showWho?: boolean;
  /** When given, a proposal id is a button that narrows the list to that proposal. */
  onFilterProposal?: (proposalId: string) => void;
  /** When given, the references count opens the cited readings; `subject` says which entry they belong to. */
  onOpenRefs?: (refs: string[], subject: string) => void;
}

/** One audit trail, used by the tenant's Audit view and the operator console alike. Codes become words here. */
export function AuditList({ entries, showWho = false, onFilterProposal, onOpenRefs }: AuditListProps) {
  return (
    <ol className="audit" aria-label="Audit entries">
      {entries.map((entry) => {
        const style = eventStyle(entry.event);
        const action = actionWords(entry.action_type);
        const reason = reasonWords(entry.detail);
        const when = formatAsOf(entry.at);
        const subject = [style.word, action, when].filter(Boolean).join(" · ");
        return (
          <li key={entry.seq} className="audit__row">
            <time className="audit__time mono" dateTime={entry.at}>
              {when}
            </time>
            <div className="audit__what">
              <EventMark event={entry.event} />
              {reason ? <span className="audit__reason">Reason: {reason}</span> : null}
            </div>
            <div className="audit__subject">
              {action ? <span className="audit__action">{action}</span> : null}
              {entry.target_device_id ? <span className="mono">{entry.target_device_id}</span> : null}
            </div>
            {showWho ? (
              <div className="audit__who">
                <span>{tenantName(entry.company_id)}</span>
                <span className="mono">{entry.actor_id}</span>
              </div>
            ) : null}
            <div className="audit__links">
              {entry.proposal_id ? (
                onFilterProposal ? (
                  <button
                    type="button"
                    className="audit__id mono"
                    aria-label={`Show only ${entry.proposal_id}`}
                    onClick={() => onFilterProposal(entry.proposal_id as string)}
                  >
                    {entry.proposal_id}
                  </button>
                ) : (
                  <span className="mono">{entry.proposal_id}</span>
                )
              ) : null}
              {entry.refs.length === 0 ? (
                <span className="audit__none">No references</span>
              ) : onOpenRefs ? (
                <button
                  type="button"
                  className="link-btn audit__refs"
                  aria-label={plural(entry.refs.length, "evidence reference")}
                  onClick={() => onOpenRefs(entry.refs, subject)}
                >
                  <FileSearch size={15} aria-hidden="true" />
                  {plural(entry.refs.length, "reference")}
                </button>
              ) : (
                <span className="audit__none">{plural(entry.refs.length, "reference")}</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
