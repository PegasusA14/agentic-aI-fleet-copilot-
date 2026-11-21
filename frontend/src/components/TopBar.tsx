import { Building2, ClipboardCheck, Eye, LogOut, ScrollText } from "lucide-react";
import { formatAsOf } from "../lib/format";

interface TopBarProps {
  companyName?: string;
  /** The operator has no company: the bar says so instead. */
  operator?: boolean;
  asOf: string | null;
  /** "fixtures" answers are replayed from stored output; the bar says so. */
  replay?: boolean;
  onSignOut?: () => void;
  /** An admin's entry to the approvals view: `pending` is null until the count is known. */
  approvals?: { pending: number | null; active: boolean };
  /** An admin's entry to the audit trail. */
  audit?: { active: boolean };
}

export function TopBar({
  companyName,
  operator = false,
  asOf,
  replay = false,
  onSignOut,
  approvals,
  audit,
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar__left">
        <span className="brand">
          <span className="brand__mark" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 32 32">
              <path
                d="M7 22c3-9 6-9 9-3s5 5 9-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="brand__word">Fleet Copilot</span>
        </span>
        {companyName ? (
          <span className="pill" aria-label={`Company: ${companyName}`}>
            <Building2 size={16} aria-hidden="true" />
            {companyName}
          </span>
        ) : null}
        {operator ? (
          <span className="pill">
            <Eye size={16} aria-hidden="true" />
            Operator
          </span>
        ) : null}
      </div>
      <div className="topbar__right">
        {replay ? (
          <span
            className="pill pill--quiet pill--hide-sm"
            title="Answers are replayed from real composer output; no live model."
          >
            Replay
          </span>
        ) : null}
        {asOf ? (
          <span className="pill pill--quiet pill--asof" title={`As of ${formatAsOf(asOf)}`}>
            <span className="asof__full">As of {formatAsOf(asOf)}</span>
            <span className="asof__short" aria-hidden="true">
              {asOf.slice(0, 10)}
            </span>
          </span>
        ) : null}
        {approvals ? (
          <a
            className="pill"
            href="#/approvals"
            aria-current={approvals.active ? "page" : undefined}
            aria-label={approvals.pending ? `Approvals, ${approvals.pending} pending` : "Approvals"}
          >
            <ClipboardCheck size={16} aria-hidden="true" />
            <span className="pill--hide-sm" aria-hidden="true">
              Approvals
            </span>
            {approvals.pending ? (
              <span className="pill__count" aria-hidden="true">
                {approvals.pending}
              </span>
            ) : null}
          </a>
        ) : null}
        {audit ? (
          <a className="pill" href="#/audit" aria-current={audit.active ? "page" : undefined} aria-label="Audit">
            <ScrollText size={16} aria-hidden="true" />
            <span className="pill--hide-sm" aria-hidden="true">
              Audit
            </span>
          </a>
        ) : null}
        {onSignOut ? (
          <button
            type="button"
            className="pill"
            aria-label={operator ? "Sign out" : "Switch company"}
            onClick={onSignOut}
          >
            <LogOut size={16} aria-hidden="true" />
            <span className="pill--hide-sm" aria-hidden="true">
              {operator ? "Sign out" : "Switch company"}
            </span>
          </button>
        ) : null}
      </div>
    </header>
  );
}
