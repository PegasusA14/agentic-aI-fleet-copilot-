import { Check } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { GLIDE, usePrefersReducedMotion } from "../../lib/motion";
import type { SuggestedAction } from "../../types/AnswerEnvelope";

/**
 * After a pick the composer takes focus, so a disabled chip does not drop focus onto the page. Only where a keyboard is
 * already physical: on a touch screen, focusing the input would raise the on-screen keyboard over the answer.
 */
export function focusComposer() {
  if (typeof window.matchMedia !== "function" || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  document.getElementById("ask-input")?.focus();
}

interface SuggestionChipsProps {
  actions: SuggestedAction[];
  /** A turn is running: chips wait, like the composer's send button. */
  busy: boolean;
  /** Sends the suggestion's own request as the next user message. */
  onAsk: (request: string) => void;
  /** Say why under each action. Left off when the reply's own explanation already does. */
  withReason?: boolean;
}

const keyOf = (action: SuggestedAction) => `${action.action_type}:${action.device_id}`;

export function SuggestionChips({ actions, busy, onAsk, withReason = false }: SuggestionChipsProps) {
  /** The suggestions already asked for, so a person can see where they have been. They stay reachable. */
  const reduce = usePrefersReducedMotion();
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  if (actions.length === 0) return null;
  return (
    <div className="chips" role="group" aria-label="Suggested actions">
      {actions.map((action) => {
        const used = picked.has(keyOf(action));
        return (
          <motion.button
            key={keyOf(action)}
            // The chips glide when one is added, removed or grows a tick; a press scales them (the transform is Motion's).
            layout={reduce ? false : "position"}
            transition={GLIDE}
            whileTap={reduce ? undefined : { scale: 0.98 }}
            type="button"
            className="chip chip--action"
            disabled={busy}
            data-asked={used || undefined}
            title={action.reason}
            onClick={() => {
              setPicked((now) => new Set(now).add(keyOf(action)));
              onAsk(action.request);
              focusComposer();
            }}
          >
            <span className="chip__label">
              {used ? <Check size={14} strokeWidth={2.6} aria-hidden="true" /> : null}
              {action.label}
              {used ? <span className="sr-only"> (asked)</span> : null}
            </span>
            <span className="chip__device">{action.target_label}</span>
            {withReason ? <span className="chip__reason">{action.reason}</span> : null}
          </motion.button>
        );
      })}
    </div>
  );
}
