import { OUTCOMES } from "../lib/outcomes";
import type { Outcome } from "../lib/types";

/** The state's dot: filled for a result, a ring for "nothing to show". Its colour is set by the label's tone. */
export function MarkDot({ hollow = false }: { hollow?: boolean }) {
  return (
    <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true" data-mark={hollow ? "ring" : "dot"}>
      {hollow ? (
        <circle cx="4" cy="4" r="3.1" fill="none" stroke="currentColor" strokeWidth="1.4" />
      ) : (
        <circle cx="4" cy="4" r="4" fill="currentColor" />
      )}
    </svg>
  );
}

/** Mono label plus a coloured dot: a state is never carried by colour alone. */
export function StateMark({ outcome }: { outcome: Outcome }) {
  const style = OUTCOMES[outcome];
  return (
    <span className="mark" data-tone={style.tone} data-outcome={outcome}>
      <MarkDot hollow={style.mark === "hollow"} />
      <span>{style.word}</span>
    </span>
  );
}
