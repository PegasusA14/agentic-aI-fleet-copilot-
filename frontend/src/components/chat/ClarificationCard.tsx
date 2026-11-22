import { useId } from "react";
import type { Alternative, Clarification } from "../../types/AnswerEnvelope";
import { RequestChips } from "./SuggestionChips";

/** The choice that covers every match; the code-built choices always spell it this way. */
const ALL_OF_THEM = /^all of them$/i;

/** What each chip sends: its own request. An older envelope has only option text, which then is the request. */
export function choicesOf(clarification: Clarification): Alternative[] {
  const choices = clarification.choices ?? [];
  if (choices.length > 0) return choices;
  return clarification.options.map((option) => ({ label: option, request: option }));
}

interface ClarificationCardProps {
  clarification: Clarification;
  busy: boolean;
  onAsk: (request: string) => void;
}

/** A question the copilot needs answered first: the question in bold, and the answers as chips that each send a full request. */
export function ClarificationCard({ clarification, busy, onAsk }: ClarificationCardProps) {
  const headingId = useId();
  return (
    <section className="rcard rcard--ask" data-kind="clarification" aria-labelledby={headingId}>
      <p className="rcard__headline msg__lead" id={headingId}>
        {clarification.question}
      </p>
      <RequestChips
        alternatives={choicesOf(clarification)}
        busy={busy}
        onAsk={onAsk}
        label="Choose one"
        strong={(choice) => ALL_OF_THEM.test(choice.label.trim())}
      />
    </section>
  );
}
