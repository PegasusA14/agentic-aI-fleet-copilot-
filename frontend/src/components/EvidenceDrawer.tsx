import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { OUTCOMES } from "../lib/outcomes";
import type { EvidenceValue, TraceView } from "../lib/transport";
import type { AnswerEnvelope, EvidenceItem, TraceEvent } from "../lib/types";

export type DrawerTab = "evidence" | "trace";

interface EvidenceDrawerProps {
  open: boolean;
  tab: DrawerTab;
  focusRefs: string[];
  envelope: AnswerEnvelope | null;
  /** Readings that belong to no answer (a proposal opened from the approvals view): shown instead of the envelope's, with no trace tab. */
  readings?: EvidenceItem[];
  /** What the readings are for, shown under the title when `readings` is given. */
  subject?: string;
  trace: TraceEvent[];
  onTab: (tab: DrawerTab) => void;
  onClose: () => void;
  /** When set, each cited reading's raw value is fetched once, the first time the Evidence tab opens. */
  loadEvidence?: (ref: string) => Promise<EvidenceValue>;
  /** Fetches a stored trace for an answer whose streamed steps are not at hand. */
  loadTrace?: (traceId: string) => Promise<TraceView>;
  /** Called when a request finds the session gone. */
  onSessionEnded?: () => void;
}

type Raw = { state: "loading" } | { state: "failed" } | { state: "ready"; value: unknown };

const KIND_WORD: Record<TraceEvent["kind"], string> = {
  scope: "Scope",
  plan: "Plan",
  reference: "Reference",
  tool_call: "Tool call",
  tool_result: "Tool result",
  retry: "Retry",
  compose: "Compose",
  explain: "Explain",
  verify: "Check",
  respond: "Response",
  error: "Error",
};

function formatValue(value: unknown): string {
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The stored reading as plain text: a key/value list for an object, a line for anything else. Never markup. */
function RawReading({ raw }: { raw: Raw }) {
  if (raw.state === "loading") return <p className="evidence__raw evidence__raw--wait">Loading the raw reading…</p>;
  if (raw.state === "failed") return <p className="evidence__raw evidence__raw--wait">The raw reading could not be loaded.</p>;
  if (isRecord(raw.value)) {
    return (
      <dl className="evidence__raw" aria-label="Raw reading">
        {Object.entries(raw.value).map(([key, value]) => (
          <div key={key} style={{ display: "contents" }}>
            <dt>{key}</dt>
            <dd>{formatValue(value)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <p className="evidence__raw evidence__raw--text mono">{formatValue(raw.value)}</p>;
}

export function EvidenceDrawer({
  open,
  tab,
  focusRefs,
  envelope,
  readings,
  subject,
  trace,
  onTab,
  onClose,
  loadEvidence,
  loadTrace,
  onSessionEnded,
}: EvidenceDrawerProps) {
  const firstFocused = useRef<HTMLLIElement | null>(null);
  const [raw, setRaw] = useState<Record<string, Raw>>({});
  const requested = useRef(new Set<string>());
  const [stored, setStored] = useState<Record<string, TraceEvent[]>>({});
  const traceRequested = useRef(new Set<string>());

  function fail(error: unknown) {
    if (error instanceof ApiError && error.status === 401) onSessionEnded?.();
  }

  useEffect(() => {
    const cited = readings ?? envelope?.evidence;
    if (!open || tab !== "evidence" || !loadEvidence || !cited) return;
    for (const { ref } of cited) {
      if (requested.current.has(ref)) continue;
      requested.current.add(ref);
      setRaw((prev) => ({ ...prev, [ref]: { state: "loading" } }));
      loadEvidence(ref).then(
        (reading) => setRaw((prev) => ({ ...prev, [ref]: { state: "ready", value: reading.value } })),
        (error) => {
          setRaw((prev) => ({ ...prev, [ref]: { state: "failed" } }));
          fail(error);
        },
      );
    }
  }, [open, tab, envelope, readings, loadEvidence]);

  const traceId = envelope?.trace_id;
  useEffect(() => {
    if (!open || tab !== "trace" || !loadTrace || !traceId || trace.length > 0) return;
    if (traceRequested.current.has(traceId)) return;
    traceRequested.current.add(traceId);
    loadTrace(traceId).then(
      (view) => setStored((prev) => ({ ...prev, [traceId]: view.events })),
      fail,
    );
  }, [open, tab, traceId, trace, loadTrace]);

  const steps = trace.length > 0 ? trace : ((traceId && stored[traceId]) || []);

  useEffect(() => {
    if (open && tab === "evidence") firstFocused.current?.scrollIntoView?.({ block: "nearest" });
  }, [open, tab, focusRefs]);

  const evidence = readings ?? envelope?.evidence ?? [];
  const tabs = readings ? (["evidence"] as const) : (["evidence", "trace"] as const);
  let attached = false;

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="drawer" aria-describedby="drawer-desc">
          <div className="drawer__head">
            <div>
              <Dialog.Title className="drawer__title">Evidence and work</Dialog.Title>
              <Dialog.Description className="drawer__desc" id="drawer-desc">
                {readings
                  ? (subject ?? "Cited readings")
                  : envelope
                    ? `${OUTCOMES[envelope.outcome].word} · ${envelope.trace_id}`
                    : "Nothing selected"}
              </Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn" aria-label="Close">
              <X size={18} aria-hidden="true" />
            </Dialog.Close>
          </div>

          <div className="tabs" role="tablist" aria-label="Drawer sections">
            {tabs.map((name) => (
              <button
                key={name}
                id={`tab-${name}`}
                type="button"
                role="tab"
                className="tab"
                aria-selected={tab === name}
                aria-controls={`panel-${name}`}
                onClick={() => onTab(name)}
              >
                {name === "evidence" ? `Evidence (${evidence.length})` : `Trace (${steps.length})`}
              </button>
            ))}
          </div>

          <div className="drawer__body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === "evidence" ? (
              evidence.length === 0 ? (
                <p className="empty">This answer cites no readings.</p>
              ) : (
                <ul className="evidence-list" aria-label="Cited readings">
                  {evidence.map((item) => {
                    const focused = focusRefs.includes(item.ref);
                    const attach = focused && !attached;
                    if (attach) attached = true;
                    return (
                      <li
                        key={item.ref}
                        className="evidence"
                        data-focused={focused}
                        ref={attach ? firstFocused : undefined}
                      >
                        <div className="evidence__label">{item.label}</div>
                        <div className="evidence__value">{item.value}</div>
                        <code className="evidence__ref mono">{item.ref}</code>
                        {raw[item.ref] ? <RawReading raw={raw[item.ref]} /> : null}
                      </li>
                    );
                  })}
                </ul>
              )
            ) : (
              <ol className="trace-list" aria-label="Steps taken">
                {steps.map((event) => (
                  <li key={event.seq} className="trace">
                    <span className="trace__seq">{event.seq}</span>
                    <div>
                      <span className="trace__kind">{KIND_WORD[event.kind]}</span>
                      <p className="trace__summary">{event.summary}</p>
                      {Object.keys(event.data).length > 0 ? (
                        <dl className="trace__data">
                          {Object.entries(event.data).map(([key, value]) => (
                            <div key={key} style={{ display: "contents" }}>
                              <dt>{key}</dt>
                              <dd>{formatValue(value)}</dd>
                            </div>
                          ))}
                        </dl>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
