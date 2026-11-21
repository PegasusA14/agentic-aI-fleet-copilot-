import { HOME_SUGGESTIONS } from "../lib/suggestions";

export function Hero() {
  return (
    <div className="hero">
      <h1 className="hero__title">Ask your fleet anything.</h1>
      <p className="hero__sub">Plain-English answers, cited to your telemetry.</p>
    </div>
  );
}

export function Suggestions({ onAsk }: { onAsk: (question: string) => void }) {
  return (
    <div className="suggestions" role="group" aria-label="Suggested questions">
      {HOME_SUGGESTIONS.map((question) => (
        <button key={question} type="button" className="chip" onClick={() => onAsk(question)}>
          {question}
        </button>
      ))}
    </div>
  );
}
