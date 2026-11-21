import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, UNREACHABLE } from "../lib/api";
import type { AuditEntry, Transport } from "../lib/transport";
import { AuditFilters, type FilterField, useAuditFilters } from "./AuditFilters";
import { AuditList } from "./AuditList";

interface AuditViewProps {
  transport: Pick<Transport, "audit">;
  /** Opens the evidence drawer on an entry's references; `subject` says which entry they belong to. */
  onOpenEvidence: (refs: string[], subject: string) => void;
  onSessionEnded: () => void;
}

type Load =
  | { state: "loading" }
  | { state: "failed"; detail: string }
  | { state: "ready"; entries: AuditEntry[] };

const FIELDS: FilterField[] = [
  { name: "target", label: "Device id" },
  { name: "proposal_id", label: "Proposal id" },
];

/** This company's trail, newest first: every proposal, decision and refusal, in words. */
export function AuditView({ transport, onOpenEvidence, onSessionEnded }: AuditViewProps) {
  const filters = useAuditFilters();
  const { applied } = filters;
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    transport.audit(applied).then(
      (entries) => alive && setLoad({ state: "ready", entries }),
      (error) => {
        if (!alive) return;
        if (error instanceof ApiError && error.status === 401) {
          onSessionEnded();
          return;
        }
        setLoad({ state: "failed", detail: error instanceof ApiError && error.status !== 0 ? error.detail : UNREACHABLE });
      },
    );
    return () => {
      alive = false;
    };
  }, [transport, applied, attempt, onSessionEnded]);

  return (
    <div className="audit-view">
      <div className="approvals__head">
        <h1 className="approvals__title">Audit</h1>
        <a className="link-btn" href="#/">
          <ArrowLeft size={15} aria-hidden="true" />
          Back to chat
        </a>
      </div>

      <AuditFilters
        label="Filter the audit trail"
        fields={FIELDS}
        draft={filters.draft}
        canClear={filters.active || Object.values(filters.draft).some((v) => v !== "")}
        onChange={filters.change}
        onApply={filters.apply}
        onClear={filters.clear}
      />

      {load.state === "loading" ? (
        <p className="fine" role="status">
          Loading the audit trail…
        </p>
      ) : load.state === "failed" ? (
        <div className="approvals__empty">
          <p role="alert" className="form-error">
            The audit trail could not be loaded. {load.detail}
          </p>
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              setLoad({ state: "loading" });
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </button>
        </div>
      ) : load.entries.length === 0 ? (
        <p className="approvals__empty">
          {filters.active
            ? "No entries match these filters."
            : "No audit entries yet. Proposals, decisions and refusals appear here."}
        </p>
      ) : (
        <AuditList
          entries={load.entries}
          onFilterProposal={(id) => filters.choose("proposal_id", id)}
          onOpenRefs={onOpenEvidence}
        />
      )}
    </div>
  );
}
