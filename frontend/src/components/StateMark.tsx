import { Check, CircleHelp, ClipboardCheck, Lock, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { type MarkName, OUTCOMES } from "../lib/outcomes";
import type { Outcome } from "../lib/types";

const ICONS: Record<Exclude<MarkName, "hollow">, ReactNode> = {
  check: <Check size={16} strokeWidth={2.6} aria-hidden="true" />,
  triangle: <TriangleAlert size={16} strokeWidth={2.4} aria-hidden="true" />,
  alert: <OctagonAlert size={16} strokeWidth={2.4} fill="currentColor" fillOpacity={0.16} aria-hidden="true" />,
  lock: <Lock size={16} strokeWidth={2.4} aria-hidden="true" />,
  question: <CircleHelp size={16} strokeWidth={2.4} aria-hidden="true" />,
  review: <ClipboardCheck size={16} strokeWidth={2.4} aria-hidden="true" />,
};

/** Mark plus word: a state is never carried by colour alone. */
export function StateMark({ outcome }: { outcome: Outcome }) {
  const style = OUTCOMES[outcome];
  return (
    <span className="mark" data-tone={style.tone} data-outcome={outcome}>
      {style.mark === "hollow" ? (
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>
      ) : (
        ICONS[style.mark]
      )}
      <span>{style.word}</span>
    </span>
  );
}
