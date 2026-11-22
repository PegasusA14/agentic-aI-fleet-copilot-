import { HOME_SUGGESTIONS } from "../lib/suggestions";
import { FleetPulse } from "./FleetPulse";

/** The fleet summary as the home screen sees it: on its way, here, or failed. */
export type FleetInfo =
  | { status: "loading" }
  | { status: "ready"; size: number; flagged: string[] }
  | { status: "error" };

/** One display sentence in two tones, the clause in ink with one word in serif italic, the rest in grey. */
export function Hero({ fleet }: { fleet?: FleetInfo }) {
  return (
    <div className="hero">
      <div className="hero__display">
        <h1 className="hero__title">
          Ask your fleet <em className="hero__em">anything</em>.
        </h1>{" "}
        <p className="hero__sub">
          <span className="nowrap">Plain-English</span> answers, cited to your telemetry.
        </p>
      </div>
      {fleet?.status === "loading" ? <div className="pulse" aria-hidden="true" data-loading /> : null}
      {fleet?.status === "ready" ? <FleetPulse size={fleet.size} flagged={fleet.flagged} /> : null}
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
