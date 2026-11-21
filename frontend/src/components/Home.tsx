import { HOME_SUGGESTIONS } from "../lib/suggestions";

/** A mono label, then one display sentence in two tones: the clause in ink, the rest in grey. */
export function Hero({ asOf }: { asOf?: string | null }) {
  return (
    <div className="hero">
      <p className="hero__label">Fleet Copilot{asOf ? ` · As of ${asOf.slice(0, 10)}` : ""}</p>
      <div className="hero__display">
        <h1 className="hero__title">Ask your fleet anything.</h1>{" "}
        <p className="hero__sub">
          <span className="nowrap">Plain-English</span> answers, cited to your telemetry.
        </p>
      </div>
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
