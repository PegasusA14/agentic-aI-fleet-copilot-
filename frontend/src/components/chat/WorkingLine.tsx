import { motion } from "motion/react";
import { CROSSFADE, usePrefersReducedMotion } from "../../lib/motion";
import type { TraceEvent } from "../../lib/types";
import { DotMatrix } from "../DotMatrix";

const START = "Reading your question…";

/** What each step means to the person waiting. The raw step summary is not for this line; it stays under Details, Show work. */
const STEP_WORDS: Partial<Record<TraceEvent["kind"], string>> = {
  scope: START,
  plan: START,
  tool_call: "Checking the telemetry…",
  tool_result: "Reading the results…",
  compose: "Writing the answer…",
  explain: "Writing the answer…",
  verify: "Checking every number…",
  retry: "Trying a different lookup…",
};

/** One plain line while a turn runs. A step with no words of its own keeps the line before it. */
export function WorkingLine({ events }: { events: TraceEvent[] }) {
  const reduce = usePrefersReducedMotion();
  let words = START;
  for (let i = events.length - 1; i >= 0; i--) {
    const found = STEP_WORDS[events[i].kind];
    if (found) {
      words = found;
      break;
    }
  }
  return (
    <motion.p
      className="working"
      role="status"
      initial={reduce ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={reduce ? undefined : { opacity: 0 }}
      transition={CROSSFADE}
    >
      <DotMatrix still={reduce} />
      <span key={words} className="working__step">
        {words}
      </span>
    </motion.p>
  );
}
