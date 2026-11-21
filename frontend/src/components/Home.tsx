import { HOME_SUGGESTIONS } from "../lib/suggestions";
import { FleetPulse } from "./FleetPulse";

interface FleetInfo {
  /** Devices in the signed-in tenant's fleet; null when no number is known yet. */
  size: number | null;
  /** Device ids with a current finding, when held on the client. */
  flagged?: string[];
}

/** A mono label, then one display sentence in two tones: the clause in ink, the rest in grey. */
export function Hero({ asOf, fleet }: { asOf?: string | null; fleet?: FleetInfo }) {
  return (
    <div className="hero">
      <p className="hero__label">Fleet Copilot{asOf ? ` · As of ${asOf.slice(0, 10)}` : ""}</p>
      <div className="hero__display">
        <h1 className="hero__title">Ask your fleet anything.</h1>{" "}
        <p className="hero__sub">
          <span className="nowrap">Plain-English</span> answers, cited to your telemetry.
        </p>
      </div>
      {fleet ? <FleetPulse size={fleet.size} flagged={fleet.flagged} /> : null}
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
