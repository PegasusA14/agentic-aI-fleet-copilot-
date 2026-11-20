import { Building2, LogOut } from "lucide-react";
import { formatAsOf } from "../lib/format";

interface TopBarProps {
  companyName?: string;
  asOf: string;
  onSignOut?: () => void;
}

export function TopBar({ companyName, asOf, onSignOut }: TopBarProps) {
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
      </div>
      <div className="topbar__right">
        <span className="pill pill--quiet pill--hide-sm" title="Answers are replayed from real composer output; no live model yet.">
          Replay
        </span>
        <span className="pill pill--quiet">As of {formatAsOf(asOf)}</span>
        {onSignOut ? (
          <button type="button" className="pill" onClick={onSignOut}>
            <LogOut size={16} aria-hidden="true" />
            <span className="pill--hide-sm">Switch company</span>
            <span className="sr-only">Sign out</span>
          </button>
        ) : null}
      </div>
    </header>
  );
}
