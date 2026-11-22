import { ArrowUp, Eye } from "lucide-react";
import { formatAsOf } from "../../lib/format";

/** The words the card says, written once so the screen-reader announcement can say them too. */
export function repeatText(asOf: string, reply?: string | null): string {
  return reply?.trim() || `Same answer as above. Nothing has changed since ${formatAsOf(asOf)}.`;
}

interface RepeatCardProps {
  asOf: string;
  /** What the copilot said about the repeat, in its own words. Without it the card says the fixed sentence. */
  reply?: string | null;
  /** Scrolls to the earlier turn. Absent when that turn is not in this thread. */
  onJump?: () => void;
  onShowAgain: () => void;
}

/** A question asked twice with nothing new in the data: one line and two ways on, not the same wall again. */
export function RepeatCard({ asOf, reply, onJump, onShowAgain }: RepeatCardProps) {
  return (
    <div className="repeat" role="group" aria-label="Same answer as before">
      <p className="repeat__text">{repeatText(asOf, reply)}</p>
      <div className="repeat__actions">
        {onJump ? (
          <button type="button" className="link-btn" onClick={onJump}>
            <ArrowUp size={15} aria-hidden="true" />
            Jump to it
          </button>
        ) : null}
        <button type="button" className="link-btn" onClick={onShowAgain}>
          <Eye size={15} aria-hidden="true" />
          Show again
        </button>
      </div>
    </div>
  );
}
