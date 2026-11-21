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
}

export function SuggestionChips({ actions, busy, onAsk }: SuggestionChipsProps) {
  if (actions.length === 0) return null;
  return (
    <div className="chips" role="group" aria-label="Suggested actions">
      {actions.map((action) => (
        <button
          key={`${action.action_type}:${action.device_id}`}
          type="button"
          className="chip chip--action"
          disabled={busy}
          title={action.reason}
          onClick={() => {
            onAsk(action.request);
            focusComposer();
          }}
        >
          <span className="chip__label">{action.label}</span>
          <span className="chip__device">{action.target_label}</span>
        </button>
      ))}
    </div>
  );
}
