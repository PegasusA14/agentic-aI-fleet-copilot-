import { Ban, LogOut, Lock, TriangleAlert } from "lucide-react";
import { useId } from "react";
import type { RefusalKind } from "../../lib/refusal";
import type { AnswerEnvelope } from "../../types/AnswerEnvelope";
import { AlternativeChips } from "./SuggestionChips";

interface RefusalCardProps {
  envelope: AnswerEnvelope;
  kind: RefusalKind;
  /** A turn is running: every chip waits. */
  busy: boolean;
  onAsk: (request: string) => void;
  /** Ends the session at the company picker, as the top bar's button does. Without it the Switch company chip is not offered. */
  onSwitchCompany?: () => void;
}

const MARKS = { evidence: TriangleAlert, company: Lock, uniform: Lock, neutral: Ban } as const;

/**
 * A refusal, as a bordered card: what the copilot will not do, in the composer's words, then what to do about it.
 * The state's mark and word sit above it, as on every reply; the ring here repeats it and is never the only signal.
 * A "not found" is one answer for every missing thing, so it carries no chips and nothing to tell one case from another.
 */
export function RefusalCard({ envelope, kind, busy, onAsk, onSwitchCompany }: RefusalCardProps) {
  const headingId = useId();
  const Mark = kind === "scope" ? null : MARKS[kind];
  const uniform = kind === "uniform";
  const needs = uniform ? [] : envelope.needs;
  const switchable = !uniform && envelope.offer_switch_company && Boolean(onSwitchCompany);
  const alternatives = uniform ? [] : envelope.alternatives;
  const needsId = `${headingId}-needs`;

  return (
    <section className="rcard" data-kind={kind} aria-labelledby={headingId}>
      <div className="rcard__head">
        {Mark ? (
          <span className="rcard__mark" aria-hidden="true">
            <Mark size={16} strokeWidth={1.75} />
          </span>
        ) : null}
        <p className="rcard__headline msg__lead" id={headingId}>
          {envelope.headline}
        </p>
      </div>

      {envelope.notes.map((note) => (
        <p key={note} className="rcard__note">
          {note}
        </p>
      ))}

      {needs.length > 0 ? (
        <div className="rcard__needs">
          <p className="rcard__label" id={needsId}>
            What I'd need to see
          </p>
          <ul aria-labelledby={needsId}>
            {needs.map((need) => (
              <li key={need}>{need}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <AlternativeChips
        alternatives={alternatives}
        busy={busy}
        onAsk={onAsk}
        label={switchable && alternatives.length === 0 ? "Next step" : "You could ask"}
        extra={
          switchable ? (
            <button
              type="button"
              className="chip chip--with-icon"
              onClick={onSwitchCompany}
            >
              <LogOut size={15} strokeWidth={1.75} aria-hidden="true" />
              Switch company
            </button>
          ) : null
        }
      />
    </section>
  );
}
