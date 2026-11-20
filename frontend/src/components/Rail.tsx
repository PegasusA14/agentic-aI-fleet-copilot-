import { SquarePen } from "lucide-react";

interface RailProps {
  turns: { id: string; question: string }[];
  onNew: () => void;
}

export function Rail({ turns, onNew }: RailProps) {
  return (
    <nav className="rail" aria-label="This session">
      <button type="button" className="rail__new" onClick={onNew}>
        <SquarePen size={16} aria-hidden="true" />
        New question
      </button>
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
