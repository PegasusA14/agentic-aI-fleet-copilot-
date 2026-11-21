import { ClipboardCheck, SquarePen } from "lucide-react";

interface RailProps {
  turns: { id: string; question: string }[];
  onNew: () => void;
  /** Proposals waiting for a decision; null until known. */
  pending?: number | null;
}

export function Rail({ turns, onNew, pending = null }: RailProps) {
  return (
    <nav className="rail" aria-label="This session">
      <button type="button" className="rail__new" onClick={onNew}>
        <SquarePen size={16} aria-hidden="true" />
        New question
      </button>
      <a className="rail__new" href="#/approvals">
        <ClipboardCheck size={16} aria-hidden="true" />
        Approvals
        {pending ? (
          <span className="pill__count">
            {pending}
            <span className="sr-only"> pending</span>
          </span>
        ) : null}
      </a>
      <p className="rail__label">This session</p>
      <ol className="rail__list">
        {turns.map((turn) => (
          <li key={turn.id}>
            <a href={`#turn-${turn.id}`}>{turn.question}</a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
