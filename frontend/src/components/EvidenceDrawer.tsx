import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import { OUTCOMES } from "../lib/outcomes";
import type { AnswerEnvelope, TraceEvent } from "../lib/types";

export type DrawerTab = "evidence" | "trace";

interface EvidenceDrawerProps {
  open: boolean;
  tab: DrawerTab;
  focusRefs: string[];
  envelope: AnswerEnvelope | null;
  trace: TraceEvent[];
  onTab: (tab: DrawerTab) => void;
  onClose: () => void;
}

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

export function EvidenceDrawer({ open, tab, focusRefs, envelope, trace, onTab, onClose }: EvidenceDrawerProps) {
  const firstFocused = useRef<HTMLLIElement | null>(null);

  useEffect(() => {
    if (open && tab === "evidence") firstFocused.current?.scrollIntoView?.({ block: "nearest" });
  }, [open, tab, focusRefs]);

  const evidence = envelope?.evidence ?? [];
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
                {envelope ? `${OUTCOMES[envelope.outcome].word} · ${envelope.trace_id}` : "Nothing selected"}
              </Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn" aria-label="Close">
              <X size={18} aria-hidden="true" />
            </Dialog.Close>
          </div>

          <div className="tabs" role="tablist" aria-label="Drawer sections">
            {(["evidence", "trace"] as const).map((name) => (
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
                {name === "evidence" ? `Evidence (${evidence.length})` : `Trace (${trace.length})`}
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
                      </li>
                    );
                  })}
                </ul>
              )
            ) : (
              <ol className="trace-list" aria-label="Steps taken">
                {trace.map((event) => (
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
