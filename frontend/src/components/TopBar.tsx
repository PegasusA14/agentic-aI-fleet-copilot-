import { Building2, Eye, LogOut, Menu } from "lucide-react";
import { formatAsOf } from "../lib/format";

interface TopBarProps {
  companyName?: string;
  /** The operator has no company: the bar says so instead. */
  operator?: boolean;
  asOf: string | null;
  /** "fixtures" answers are replayed from stored output; the bar says so. */
  replay?: boolean;
  onSignOut?: () => void;
  /** Under 960px the history sidebar is a drawer; this is the button that opens it. */
  onMenu?: () => void;
}

export function TopBar({
  companyName,
  operator = false,
  asOf,
  replay = false,
  onSignOut,
  onMenu,
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar__left">
        {onMenu ? (
          <button type="button" className="menu-btn" aria-label="Open menu" aria-haspopup="dialog" onClick={onMenu}>
            <Menu size={18} aria-hidden="true" />
          </button>
        ) : null}
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
