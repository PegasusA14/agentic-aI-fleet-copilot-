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
  evalFigures,
  SUITE_ORDER,
  suiteLabel,
  tallyState,
} from "../../lib/evals";
import type { EvalCase, EvalRun, EvalTally, Transport } from "../../lib/transport";
import { Tally } from "../Count";
import { MarkDot } from "../StateMark";
import { TraceTimeline } from "./TraceTimeline";
import { useLoad } from "./useLoad";

interface EvalsViewProps {
  transport: Pick<Transport, "observeEvals" | "observeEval" | "observeTrace">;
  /** Bumped by the console's Refresh button. */
  nonce: number;
  onSessionEnded: () => void;
}

/** The state's own shape: tick, alert octagon, hollow ring. Shape differs per state, so it never relies on colour. */
function EvalIcon({ state }: { state: EvalState }) {
  if (state === "passed") return <Check size={16} strokeWidth={2.6} aria-hidden="true" />;
  if (state === "failed") {
    return <OctagonAlert size={16} strokeWidth={2.4} fill="currentColor" fillOpacity={0.16} aria-hidden="true" />;
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

/** Mark plus word. "Not recorded" has a hollow mark of its own so it never reads as a failure. */
function EvalMark({ state, word }: { state: EvalState; word?: string }) {
  const style = EVAL_STATE[state];
  return (
    <span className="mark" data-tone={style.tone} data-state={state}>
      <MarkDot hollow={state === "not_recorded" || state === "none"} />
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
          <Tally passed={tally.passed} total={tally.total} />
        </span>
      </span>
    </td>
  );
}

/** A suite's result in the narrow runs table: the state mark and passed/total, spelled out in the name and title. */
function SuiteCell({ suite, tally }: { suite: string; tally: EvalTally | undefined }) {
  if (!tally) return <td>—</td>;
  const state = tallyState(tally);
  const style = EVAL_STATE[state];
  const name = `${suiteLabel(suite)}: ${style.word.toLowerCase()}, ${tally.passed} of ${tally.total}`;
  return (
    <td>
      <span className="evals__tally" role="img" aria-label={name} title={name}>
        <span className="mark evals__glyph" data-tone={style.tone} data-state={state}>
          <EvalIcon state={state} />
        </span>
        <span className="evals__count">
          <Tally passed={tally.passed} total={tally.total} />
        </span>
      </span>
    </td>
  );
}

/**
 * The aggregate figures of one run: the two hard gates with a mark and a word, then the rates and the cost. Runs
 * stored before these figures existed have none and show nothing.
 */
function RunFigures({ run }: { run: EvalRun }) {
  const figures = evalFigures(run.totals, run.model_mode);
  if (!figures) return null;
  const started = formatAsOf(run.started_at);
  return (
    <section className="evals__figures" aria-label={`Metrics of the run started ${started}`}>
      <p className="label">Metrics · {started}</p>
      <dl className="evals__figure-row">
        {figures.map((figure) => (
          <div key={figure.id} className="evals__figure" data-gate={figure.gate}>
            <dt>{figure.label}</dt>
            <dd className="evals__figure-value">{figure.value}</dd>
            {figure.gate ? (
              <dd className="evals__figure-mark">
                <EvalMark
                  state={figure.gate === "clear" ? "passed" : "failed"}
                  word={figure.gate === "clear" ? "None" : "Fails the run"}
                />
              </dd>
            ) : null}
            <dd className="evals__figure-note">{figure.note}</dd>
          </div>
        ))}
      </dl>
    </section>
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
      <td className="obs__wrap evals__case">
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
  const [failingOnly, setFailingOnly] = useState(false);

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
  const all = load.data.cases;
  // A case that was not recorded is not a pass, so it stays when only failures are wanted.
  const shown = failingOnly ? all.filter((c) => !c.passed) : all;
  return (
    <>
      <div className="evals__filter">
        <label className="evals__toggle">
          <input type="checkbox" checked={failingOnly} onChange={(event) => setFailingOnly(event.target.checked)} />
          <span>Failing only</span>
        </label>
        <p className="evals__shown" role="status">
          Showing {shown.length} of {all.length} {all.length === 1 ? "case" : "cases"}
        </p>
      </div>
      {shown.length === 0 ? (
        <p className="approvals__empty">No failing cases in this run.</p>
      ) : (
        <div className="obs__scroll" role="region" aria-label={`Cases in run ${runId}, scrollable`} tabIndex={0}>
          <table className="obs__table evals__table" aria-label={`Cases in run ${runId}`}>
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
              {shown.map((c) => (
                <CaseRow key={c.instance_id} c={c} open={trace} onTrace={(id) => setTrace(trace === id ? null : id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
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
  // The chosen run's figures, else the newest run that has any (a run from before the metrics has none).
  const figured = [runs.find((run) => run.run_id === selected), ...runs].find(
    (run) => run && evalFigures(run.totals, run.model_mode) !== null,
  );

  return (
    <>
      {runs.some((run) => run.model_mode === "fixtures") ? (
        <p className="obs__note">
          This is an example run for the offline replay, not a stored evaluation. Run make eval for real results.
        </p>
      ) : null}
      <div className="obs__scroll" role="region" aria-label="Evaluation runs, scrollable" tabIndex={0}>
        <table className="obs__table evals__table" aria-label="Evaluation runs">
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
                    <SuiteCell key={name} suite={name} tally={run.totals.suites?.[name]} />
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
      {figured ? <RunFigures run={figured} /> : null}
      {selected ? (
        <RunCases key={selected} transport={transport} runId={selected} nonce={nonce} onSessionEnded={onSessionEnded} />
      ) : null}
    </>
  );
}
