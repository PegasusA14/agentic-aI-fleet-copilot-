import { Building2, Eye, LogOut, SquarePen } from "lucide-react";
import { formatAsOf } from "../lib/format";

interface TopBarProps {
  companyName?: string;
  /** The operator has no company: the bar says so instead. */
  operator?: boolean;
  asOf: string | null;
  /** "fixtures" answers are replayed from stored output; the bar says so. */
  replay?: boolean;
  onSignOut?: () => void;
  /** Resets the thread and starts a fresh conversation. */
  onNewConversation?: () => void;
  newConversationDisabled?: boolean;
}

export function TopBar({
  companyName,
  operator = false,
  asOf,
  replay = false,
  onSignOut,
  onNewConversation,
  newConversationDisabled = false,
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
          Fleet Copilot
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
        {asOf ? <span className="pill pill--quiet">As of {formatAsOf(asOf)}</span> : null}
        {onNewConversation ? (
          <button
            type="button"
            className="pill"
            aria-label="New conversation"
            onClick={onNewConversation}
            disabled={newConversationDisabled}
          >
            <SquarePen size={16} aria-hidden="true" />
            <span className="pill--hide-sm">New conversation</span>
          </button>
        ) : null}
        {onSignOut ? (
          <button type="button" className="pill" onClick={onSignOut}>
            <LogOut size={16} aria-hidden="true" />
            <span className="pill--hide-sm">{operator ? "Sign out" : "Switch company"}</span>
            <span className="sr-only">Sign out</span>
          </button>
        ) : null}
      </div>
    </header>
  );
}
