import { TENANTS } from "../../lib/fixtures";
import type { ObserveAuditFilters, Transport } from "../../lib/transport";
import { AuditFilters, type FilterField, useAuditFilters } from "../AuditFilters";
import { AuditList } from "../AuditList";
import { useLoad } from "./useLoad";

interface AuditTableProps {
  transport: Pick<Transport, "observeAudit">;
  nonce: number;
  onSessionEnded: () => void;
}

const FIELDS: FilterField[] = [
  {
    name: "company",
    label: "Company",
    allLabel: "All companies",
    options: TENANTS.map((tenant) => ({ value: tenant.id, label: tenant.name })),
  },
  { name: "actor", label: "Actor" },
  { name: "target", label: "Device id" },
];

/** Every company's audit trail, narrowed by company, actor or target device. */
export function AuditTable({ transport, nonce, onSessionEnded }: AuditTableProps) {
  const filters = useAuditFilters();
  const { applied } = filters;
  const { load, retry } = useLoad(
    () => transport.observeAudit(applied as ObserveAuditFilters),
    [transport, applied, nonce],
    onSessionEnded,
  );

  return (
    <>
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
          <button type="button" className="link-btn" onClick={retry}>
            Try again
          </button>
        </div>
      ) : load.data.length === 0 ? (
        <p className="approvals__empty">
          {filters.active ? "No entries match these filters." : "No audit entries yet. Decisions from any company appear here."}
        </p>
      ) : (
        <AuditList entries={load.data} showWho />
      )}
    </>
  );
}
