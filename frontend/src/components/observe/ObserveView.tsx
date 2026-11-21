import { RefreshCw } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import type { Transport } from "../../lib/transport";
import { AuditTable } from "./AuditTable";
import { EffectLedger } from "./EffectLedger";
import { EvalsView } from "./EvalsView";
import { TurnsTable } from "./TurnsTable";

type ObserveTransport = Pick<
  Transport,
  "observeTurns" | "observeTrace" | "observeAudit" | "observeEffects" | "observeEvals" | "observeEval"
>;

interface ObserveViewProps {
  transport: ObserveTransport;
  onSessionEnded: () => void;
}

const TABS = [
  { id: "traces", label: "Traces" },
  { id: "audit", label: "Audit" },
  { id: "effects", label: "Effect ledger" },
  { id: "evals", label: "Evaluations" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/** The operator's read-only console: turns and their traces, every company's audit trail, the effect ledger and the evaluation runs. */
export function ObserveView({ transport, onSessionEnded }: ObserveViewProps) {
  const [tab, setTab] = useState<TabId>("traces");
  const [nonce, setNonce] = useState(0);
  const buttons = useRef<Record<TabId, HTMLButtonElement | null>>({
    traces: null,
    audit: null,
    effects: null,
    evals: null,
  });

  function move(event: KeyboardEvent<HTMLDivElement>) {
    const at = TABS.findIndex((t) => t.id === tab);
    let next: number;
    switch (event.key) {
      case "ArrowRight":
        next = (at + 1) % TABS.length;
        break;
      case "ArrowLeft":
        next = (at - 1 + TABS.length) % TABS.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = TABS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    setTab(TABS[next].id);
    buttons.current[TABS[next].id]?.focus();
  }

  return (
    <div className="obs">
      <div className="approvals__head">
        <h1 className="approvals__title">Operator console</h1>
        <button type="button" className="link-btn" onClick={() => setNonce((n) => n + 1)}>
          <RefreshCw size={15} aria-hidden="true" />
          Refresh
        </button>
      </div>

      <div className="tabs tabs--page" role="tablist" aria-label="Console sections" onKeyDown={move}>
        {TABS.map((t) => (
          <button
            key={t.id}
            ref={(node) => {
              buttons.current[t.id] = node;
            }}
            id={`observe-tab-${t.id}`}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === t.id}
            aria-controls={`observe-panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`observe-panel-${tab}`}
        aria-labelledby={`observe-tab-${tab}`}
        tabIndex={0}
        className="obs__panel"
      >
        {tab === "traces" ? (
          <TurnsTable transport={transport} nonce={nonce} onSessionEnded={onSessionEnded} />
        ) : tab === "audit" ? (
          <AuditTable transport={transport} nonce={nonce} onSessionEnded={onSessionEnded} />
        ) : tab === "effects" ? (
          <EffectLedger transport={transport} nonce={nonce} onSessionEnded={onSessionEnded} />
        ) : (
          <EvalsView transport={transport} nonce={nonce} onSessionEnded={onSessionEnded} />
        )}
      </div>
    </div>
  );
}
