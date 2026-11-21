import { EFFECT_TABLES } from "../../lib/audit";
import { TENANTS, tenantName } from "../../lib/fixtures";
import type { Transport } from "../../lib/transport";
import { Count } from "../Count";
import { useLoad } from "./useLoad";

interface EffectLedgerProps {
  transport: Pick<Transport, "observeEffects">;
  nonce: number;
  onSessionEnded: () => void;
}

/** What approved actions have written, per effect table and company. Every count is simulated. */
export function EffectLedger({ transport, nonce, onSessionEnded }: EffectLedgerProps) {
  const { load, retry } = useLoad(() => transport.observeEffects(), [transport, nonce], onSessionEnded);

  if (load.state === "loading") {
    return (
      <p className="fine" role="status">
        Loading the effect ledger…
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <div className="approvals__empty">
        <p role="alert" className="form-error">
          The effect ledger could not be loaded. {load.detail}
        </p>
        <button type="button" className="link-btn" onClick={retry}>
          Try again
        </button>
      </div>
    );
  }

  const counts = load.data;
  const known = TENANTS.map((tenant) => tenant.id);
  const extra = [...new Set(Object.values(counts).flatMap((byCompany) => Object.keys(byCompany)))].filter(
    (id) => !known.includes(id),
  );
  const companies = [...known, ...extra];
  const tables = [...Object.keys(EFFECT_TABLES), ...Object.keys(counts).filter((name) => !(name in EFFECT_TABLES))];

  return (
    <>
      <p className="obs__note">All effects are simulated. Nothing left this system.</p>
      <div className="obs__scroll">
        <table className="obs__table obs__ledger">
          <caption className="sr-only">Rows written by approved actions, per company (simulated)</caption>
          <thead>
            <tr>
              <th scope="col">Effect table</th>
              {companies.map((id) => (
                <th scope="col" key={id} className="obs__num">
                  {tenantName(id)}
                </th>
              ))}
              <th scope="col" className="obs__num">
                Total
              </th>
            </tr>
          </thead>
          <tbody>
            {tables.map((table) => {
              const row = counts[table] ?? {};
              const total = Object.values(row).reduce((sum, n) => sum + n, 0);
              return (
                <tr key={table}>
                  <th scope="row">{EFFECT_TABLES[table] ?? table.replaceAll("_", " ")}</th>
                  {companies.map((id) => (
                    <td key={id} className="obs__num">
                      <Count value={row[id] ?? 0} />
                    </td>
                  ))}
                  <td className="obs__num obs__total">
                    <Count value={total} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
