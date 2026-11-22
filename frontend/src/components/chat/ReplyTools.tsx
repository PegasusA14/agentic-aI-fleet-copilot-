import { Check, Copy, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** How long the check mark stays in place of the copy icon. */
export const COPIED_MS = 1500;

interface ReplyToolsProps {
  /** What Copy puts on the clipboard. */
  text: string;
  /** Asks the same question again. Without it the Retry button is not shown. */
  onRetry?: () => void;
  /** A turn is running: Retry waits. */
  busy: boolean;
}

/** Two quiet icon buttons under a reply: copy it as text, or ask the same question again. */
export function ReplyTools({ text, onRetry, busy }: ReplyToolsProps) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number>(0);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function copy() {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      ok = false;
    }
    setState(ok ? "copied" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), COPIED_MS);
  }

  return (
    <div className="tools" role="group" aria-label="Reply actions">
      <button
        type="button"
        className="tool"
        aria-label="Copy answer"
        data-tip="Copy answer"
        data-copied={state === "copied" || undefined}
        onClick={() => void copy()}
      >
        <Copy className="tool__icon tool__icon--copy" size={16} strokeWidth={1.75} aria-hidden="true" />
        <Check className="tool__icon tool__icon--done" size={16} strokeWidth={2.25} aria-hidden="true" />
      </button>
      {onRetry ? (
        <button type="button" className="tool" aria-label="Retry" data-tip="Ask this again" disabled={busy} onClick={onRetry}>
          <RotateCcw className="tool__icon" size={16} strokeWidth={1.75} aria-hidden="true" />
        </button>
      ) : null}
      <span className="sr-only" role="status" aria-live="polite">
        {state === "copied" ? "Copied" : state === "failed" ? "Could not copy" : ""}
      </span>
    </div>
  );
}
