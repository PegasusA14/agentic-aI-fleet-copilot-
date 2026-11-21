import { Check } from "lucide-react";
import { useState } from "react";
import type { SuggestedAction } from "../../types/AnswerEnvelope";

/** After a pick the composer takes focus, so a disabled chip does not drop the keyboard onto the page. */
export function focusComposer() {
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
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  if (actions.length === 0) return null;
  return (
    <div className="chips" role="group" aria-label="Suggested actions">
      {actions.map((action) => {
        const used = picked.has(keyOf(action));
        return (
          <button
            key={keyOf(action)}
            type="button"
            className="chip chip--action"
            disabled={busy}
            aria-pressed={used}
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
            </span>
            <span className="chip__device">{action.target_label}</span>
            {withReason ? <span className="chip__reason">{action.reason}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
