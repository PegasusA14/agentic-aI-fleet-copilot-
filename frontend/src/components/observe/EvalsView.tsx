import { Check, OctagonAlert } from "lucide-react";
import { useState } from "react";
import { formatAsOf } from "../../lib/format";
import {
  caseState,
  checkLabel,
  companyOfUser,
  EVAL_STATE,
  type EvalState,
  effectLines,
  SUITE_ORDER,
  suiteLabel,
  tallyState,
} from "../../lib/evals";
import type { EvalCase, EvalRun, EvalTally, Transport } from "../../lib/transport";
import { TraceTimeline } from "./TraceTimeline";
import { useLoad } from "./useLoad";

interface EvalsViewProps {
  transport: Pick<Transport, "observeEvals" | "observeEval" | "observeTrace">;
  /** Bumped by the console's Refresh button. */
  nonce: number;
  onSessionEnded: () => void;
}

/** Mark plus word. "Not recorded" has a hollow mark of its own so it never reads as a failure. */
function EvalMark({ state, word }: { state: EvalState; word?: string }) {
  const style = EVAL_STATE[state];
  return (
    <span className="mark" data-tone={style.tone} data-state={state}>
      {state === "passed" ? (
        <Check size={16} strokeWidth={2.6} aria-hidden="true" />
      ) : state === "failed" ? (
        <OctagonAlert size={16} strokeWidth={2.4} fill="currentColor" fillOpacity={0.16} aria-hidden="true" />
      ) : (
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>
      )}
      <span>{word ?? style.word}</span>
    </span>
  );
}

function TallyCell({ tally }: { tally: EvalTally | undefined }) {
  if (!tally) return <td>—</td>;
  return (
    <td>
      <span className="evals__tally">
        <EvalMark state={tallyState(tally)} />
        <span className="evals__count">
          {tally.passed}/{tally.total}
        </span>
      </span>
    </td>
  );
}

function suitesOf(runs: EvalRun[]): string[] {
  const seen = new Set(runs.flatMap((run) => Object.keys(run.totals.suites ?? {})));
  const known = SUITE_ORDER.filter((name) => seen.has(name));
  return [...known, ...[...seen].filter((name) => !SUITE_ORDER.includes(name)).sort()];
}

function failingChecks(c: EvalCase): string[] {
  return c.checks.filter((check) => !check.passed).map((check) => `${checkLabel(check.name)}: ${check.detail}`);
}

function CaseRow({ c, open, onTrace }: { c: EvalCase; open: string | null; onTrace: (traceId: string) => void }) {
  const state = caseState(c);
  const lines = effectLines(c);
  return (
    <tr>
      <td>{suiteLabel(c.suite)}</td>
      <td className="obs__wrap">
        <span className="mono">{c.case_id}</span>
        {c.questions.map((question) => (
          <span key={question} className="evals__question">
            {question}
          </span>
        ))}
      </td>
      <td className="obs__num">{c.phrasing_index}</td>
      <td>{companyOfUser(c.user)}</td>
      <td>
        <EvalMark state={state} />
      </td>
      <td className="obs__wrap">
        {state === "not_recorded" ? (
          <span className="evals__why">
            No model answer was recorded for this question. Run make eval-live to record it.
          </span>
        ) : (
          failingChecks(c).map((line) => (
            <span key={line} className="evals__why">
              {line}
            </span>
          ))
        )}
      </td>
      <td className="obs__wrap">
        {lines.map((line, index) => (
          <span key={index} className="evals__why">
            {line}
          </span>
        ))}
      </td>
      <td>
        <span className="evals__traces">
          {c.trace_ids.map((id, index) => (
            <button
              key={id}
              type="button"
              className="link-btn"
              aria-pressed={open === id}
              aria-label={`Trace${c.trace_ids.length > 1 ? ` ${index + 1}` : ""} for ${c.case_id} phrasing ${c.phrasing_index}`}
              onClick={() => onTrace(id)}
            >
              Trace{c.trace_ids.length > 1 ? ` ${index + 1}` : ""}
            </button>
          ))}
        </span>
      </td>
    </tr>
  );
}

function RunCases({
  transport,
  runId,
  nonce,
  onSessionEnded,
}: {
  transport: EvalsViewProps["transport"];
  runId: string;
  nonce: number;
  onSessionEnded: () => void;
}) {
  const { load, retry } = useLoad(() => transport.observeEval(runId), [transport, runId, nonce], onSessionEnded);
  const [trace, setTrace] = useState<string | null>(null);

  if (load.state === "loading") {
    return (
      <p className="fine" role="status">
        Loading the cases…
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <div className="approvals__empty">
        <p role="alert" className="form-error">
          The cases of this run could not be loaded. {load.detail}
        </p>
        <button type="button" className="link-btn" onClick={retry}>
          Try again
        </button>
      </div>
    );
  }
  if (load.data.cases.length === 0) {
    return <p className="approvals__empty">This run stored no cases.</p>;
  }
  return (
    <>
      <div className="obs__scroll">
        <table className="obs__table" aria-label={`Cases in run ${runId}`}>
          <thead>
            <tr>
              <th scope="col">Suite</th>
              <th scope="col">Case</th>
              <th scope="col" className="obs__num">
                Phrasing
              </th>
              <th scope="col">Company</th>
              <th scope="col">Result</th>
              <th scope="col">Details</th>
              <th scope="col">Effects</th>
              <th scope="col">
                <span className="sr-only">Trace</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {load.data.cases.map((c) => (
              <CaseRow key={c.instance_id} c={c} open={trace} onTrace={(id) => setTrace(trace === id ? null : id)} />
            ))}
          </tbody>
        </table>
      </div>
      {trace ? <TraceTimeline transport={transport} traceId={trace} onSessionEnded={onSessionEnded} /> : null}
    </>
  );
}

/** Stored evaluation runs, newest first; choosing one lists its cases, their checks, effects and traces. */
export function EvalsView({ transport, nonce, onSessionEnded }: EvalsViewProps) {
  const { load, retry } = useLoad(() => transport.observeEvals(), [transport, nonce], onSessionEnded);
  const [selected, setSelected] = useState<string | null>(null);

  if (load.state === "loading") {
    return (
      <p className="fine" role="status">
        Loading the evaluation runs…
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <div className="approvals__empty">
        <p role="alert" className="form-error">
          The evaluation runs could not be loaded. {load.detail}
        </p>
        <button type="button" className="link-btn" onClick={retry}>
          Try again
        </button>
      </div>
    );
  }
  const runs = load.data;
  if (runs.length === 0) {
    return <p className="approvals__empty">No evaluation runs yet. Run make eval.</p>;
  }
  const suites = suitesOf(runs);

  return (
    <>
      {runs.some((run) => run.model_mode === "fixtures") ? (
        <p className="obs__note">
          This is an example run for the offline replay, not a stored evaluation. Run make eval for real results.
        </p>
      ) : null}
      <div className="obs__scroll">
        <table className="obs__table" aria-label="Evaluation runs">
          <thead>
            <tr>
              <th scope="col">Started</th>
              <th scope="col">Mode</th>
              <th scope="col">Dataset</th>
              <th scope="col">As of</th>
              {suites.map((name) => (
                <th scope="col" key={name}>
                  {suiteLabel(name)}
                </th>
              ))}
              <th scope="col">Overall</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const open = selected === run.run_id;
              const overall = run.totals.overall;
              return (
                <tr key={run.run_id} data-selected={open}>
                  <td>
                    <button
                      type="button"
                      className="obs__open mono"
                      aria-pressed={open}
                      onClick={() => setSelected(open ? null : run.run_id)}
                    >
                      {formatAsOf(run.started_at)}
                    </button>
                  </td>
                  <td>{run.model_mode}</td>
                  <td className="mono" title={run.dataset_sha256}>
                    {run.dataset_sha256.slice(0, 12)}
                  </td>
                  <td className="mono">{formatAsOf(run.as_of)}</td>
                  {suites.map((name) => (
                    <TallyCell key={name} tally={run.totals.suites?.[name]} />
                  ))}
                  {overall ? (
                    <TallyCell tally={overall} />
                  ) : (
                    <td>
                      <EvalMark state="none" word="Not finished" />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {selected ? (
        <RunCases key={selected} transport={transport} runId={selected} nonce={nonce} onSessionEnded={onSessionEnded} />
      ) : null}
    </>
  );
}
