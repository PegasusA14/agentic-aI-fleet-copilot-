import { FileSearch } from "lucide-react";
import { actionWords, eventStyle, reasonWords } from "../lib/audit";
import { formatAsOf, plural } from "../lib/format";
import { tenantName } from "../lib/fixtures";
import type { AuditEntry } from "../lib/transport";
import { MarkDot } from "./StateMark";

/** Mark plus word for what happened. */
function EventMark({ event }: { event: string }) {
  const style = eventStyle(event);
  return (
    <span className="mark" data-tone={style.tone} data-event={event}>
      <MarkDot hollow={style.mark === "hollow"} />
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
    <ol className="audit" aria-label="Audit entries" data-who={showWho}>
      {entries.map((entry) => {
        const style = eventStyle(entry.event);
        const action = actionWords(entry.action_type);
        const reason = reasonWords(entry.detail);
        const when = formatAsOf(entry.at);
        const subject = [style.word, action, when].filter(Boolean).join(" · ");
        return (
          <li key={entry.seq} className="audit__row">
            <time className="audit__time" dateTime={entry.at}>
              <span className="audit__when">Recorded</span> <span className="mono">{when}</span>
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
                    title={entry.proposal_id}
                    aria-label={`Show only ${entry.proposal_id}`}
                    onClick={() => onFilterProposal(entry.proposal_id as string)}
                  >
                    {entry.proposal_id}
                  </button>
                ) : (
                  <span className="audit__id audit__id--text mono" title={entry.proposal_id}>
                    {entry.proposal_id}
                  </span>
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
