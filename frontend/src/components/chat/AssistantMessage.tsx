import { FileSearch, ListTree } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { copyText, notesFor, numberEvidence, scopeLineText, sortRows, type SortState } from "../../lib/answerTable";
import { EXCLUDED_TEXT, plural, reasonWords } from "../../lib/format";
import { CROSSFADE, ENTER, FOLD_IN, FOLD_OUT, Presence, usePrefersReducedMotion } from "../../lib/motion";
import { leadCount, replyOf } from "../../lib/reply";
import type { AnswerEnvelope, EntityList, ProposalCard } from "../../lib/types";
import { Count, CountedText } from "../Count";
import { ProposalView, type Decide } from "../ProposalView";
import { StateMark } from "../StateMark";
import { AnswerDisclosures, HOT_MS, type HotSource } from "./AnswerDisclosures";
import { AnswerTable } from "./AnswerTable";
import { RepeatCard } from "./RepeatCard";
import { ReplyTools } from "./ReplyTools";
import { AlternativeChips, NextSteps, focusComposer } from "./SuggestionChips";

interface AssistantMessageProps {
  envelope: AnswerEnvelope;
  /** Present on replayed stand-in answers; shown under Details. */
  note?: string;
  /** A turn is running: every chip in the message waits. */
  busy: boolean;
  onOpenEvidence: (refs?: string[]) => void;
  onOpenTrace: () => void;
  onAsk: (question: string) => void;
  /** How a person decides the proposal on this answer. Without it the proposal is shown read-only. */
  decide?: Decide;
  onDecided?: (card: ProposalCard) => void;
  /** Called once the reply is on screen, so the thread can bring what needs a decision into view. */
  onArrive?: () => void;
  /** The answer was stored in an earlier session: its proposal may have been decided since. */
  reopened?: boolean;
  onSessionEnded?: () => void;
  /** The question this reply answers; Retry asks it again. Without it there is no Retry. */
  question?: string;
  /** Brings the earlier turn this reply repeats into view. Absent when that turn is not in the thread. */
  onJump?: () => void;
}

/**
 * The reply's opening words. When the composer wrote a count into them ("2 of 10 …", "4 of your 5 …"), the numbers
 * tick; the rest stays text. An answer's headline is the bold one-liner; any other lead stays a plain sentence.
 */
function Lead({ text, counted, withList, headline }: { text: string; counted: boolean; withList: boolean; headline: boolean }) {
  const count = counted ? leadCount(text) : null;
  return (
    <p className="msg__lead" data-with-list={withList || undefined} data-headline={headline || undefined}>
      {count ? (
        <CountedText text={text}>
          <Count value={count.n} />
          {count.kind === "of" ? (
            <>
              {count.joiner ?? " of "}
              <Count value={count.m} />
            </>
          ) : null}
          {count.rest}
        </CountedText>
      ) : (
        text
      )}
    </p>
  );
}

/** One claim reads as a sentence; several read as a list. */
function Claims({ claims }: { claims: AnswerEnvelope["claims"] }) {
  if (claims.length === 1) return <p className="msg__claim">{claims[0].text}</p>;
  return (
    <ul className="msg__claims">
      {claims.map((claim) => (
        <li key={claim.text}>{claim.text}</li>
      ))}
    </ul>
  );
}

function DeviceChips({ list }: { list: EntityList }) {
  const titleId = useId();
  return (
    <div className="devlist">
      <p className="devlist__title" id={titleId}>
        {list.items.length > 1 ? `${list.title} — in this order` : list.title}
      </p>
      <ol className="devchips" aria-labelledby={titleId}>
        {list.items.map((item) => (
          <li key={item.device_id} className="devchip">
            <span className="devchip__rank" aria-label={`Number ${item.rank}`}>
              {item.rank}
            </span>
            <span className="devchip__name">{item.label}</span>
            <span className="devchip__id mono">{item.device_id}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Facts({ envelope }: { envelope: AnswerEnvelope }) {
  const { scope, coverage } = envelope;
  if (!scope.rule && !scope.calculation && !coverage && !envelope.reason_code) return null;
  const excluded = coverage ? Object.entries(coverage.excluded) : [];
  return (
    <dl className="facts" aria-label="Scope and coverage">
      {scope.rule ? (
        <div className="wide">
          <dt>Rule used</dt>
          <dd>{scope.rule}</dd>
        </div>
      ) : null}
      {scope.calculation ? (
        <div className="wide">
          <dt>How it was counted</dt>
          <dd>{scope.calculation}</dd>
        </div>
      ) : null}
      {coverage ? (
        <>
          <div>
            <dt>Evaluated</dt>
            <dd>{plural(coverage.population, "device")}</dd>
          </div>
          <div>
            <dt>Stale readings</dt>
            <dd>{coverage.stale.length === 0 ? "None" : plural(coverage.stale.length, "device")}</dd>
          </div>
          <div>
            <dt>No latest reading</dt>
            <dd>{coverage.missing_latest.length === 0 ? "None" : plural(coverage.missing_latest.length, "device")}</dd>
          </div>
          <div>
            <dt>Excluded</dt>
            <dd>
              {excluded.length === 0
                ? "None"
                : excluded
                    .map(([reason, ids]) => `${ids.length} ${EXCLUDED_TEXT[reason] ?? reason.replaceAll("_", " ")}`)
                    .join("; ")}
            </dd>
          </div>
        </>
      ) : null}
      {envelope.reason_code ? (
        <div>
          <dt>Reason</dt>
          <dd>{reasonWords(envelope.reason_code)}</dd>
        </div>
      ) : null}
    </dl>
  );
}

/** The copilot's reply: the words first, then only what the person can act on. Everything else is one click down. */
export function AssistantMessage({
  envelope,
  note,
  busy,
  onOpenEvidence,
  onOpenTrace,
  onAsk,
  decide,
  onDecided,
  reopened,
  onSessionEnded,
  onArrive,
  question,
  onJump,
}: AssistantMessageProps) {
  const detailsId = useId();
  const rowKey = useId();
  const reduce = usePrefersReducedMotion();
  const [open, setOpen] = useState(false);
  /** What the person decided, once they have: the reply then stops saying the proposal waits. */
  const [decision, setDecision] = useState<ProposalCard["status"] | null>(null);
  const table = envelope.table;
  const [sort, setSort] = useState<SortState>(() => table?.default_sort ?? { key: "", direction: "asc" });
  const [folds, setFolds] = useState({ evidence: false, notChecked: false, how: false });
  const [hot, setHot] = useState<HotSource | null>(null);
  /** A repeat shows as a one-line card until the person asks to see it again. */
  const [expanded, setExpanded] = useState(false);
  const { outcome } = envelope;
  const status = decision ?? envelope.proposal?.status;
  const reply = replyOf(envelope, status);
  const { lead, explanation, support } = reply;
  const clarification = envelope.clarification;
  const suggestions = envelope.suggested_actions ?? [];
  const followUps = envelope.follow_ups ?? [];
  /** A list answer carries a table: the table, its scope line and the disclosures say what the claim sentence listed. */
  const hasTable = Boolean(table) && outcome === "answer";
  /** An advice reply says each action in its claims and chips. A list answer may carry suggestions too, and keeps its table. */
  const isAdvice = suggestions.length > 0 && !table;
  /** The reply carries the claims only when nothing else already says them: no table, no explanation, no chips that name each action. */
  const claimsInReply = !hasTable && !explanation && !envelope.proposal && !isAdvice && envelope.claims.length > 0;
  const lists = isAdvice || hasTable ? [] : envelope.entity_lists;
  /** A proposal's card owns its state, so the reply carries no second mark above it. */
  const markShown = outcome !== "answer" && outcome !== "proposal";
  const repeated = Boolean(envelope.repeat_of) && !envelope.proposal && !expanded;

  const numbered = useMemo(() => numberEvidence(envelope), [envelope]);
  const rows = useMemo(() => (table ? sortRows(table, sort) : []), [table, sort]);
  const scope = useMemo(
    () => (envelope.scope_line ? scopeLineText(envelope.scope_line, table, envelope.headline) : null),
    [envelope.scope_line, table, envelope.headline],
  );
  const notes = notesFor(envelope);
  const rowId = useMemo(() => (n: number) => `${rowKey}-ev-${n}`, [rowKey]);

  const arrive = useRef(onArrive);
  arrive.current = onArrive;
  useEffect(() => {
    arrive.current?.();
  }, []);

  const hotTimer = useRef<number>(0);
  useEffect(() => () => window.clearTimeout(hotTimer.current), []);

  /** A Source chip: open Evidence and light the reading it numbers, for a moment. */
  function showSource(n: number) {
    setFolds((now) => ({ ...now, evidence: true }));
    setHot({ n, at: Date.now() });
    window.clearTimeout(hotTimer.current);
    hotTimer.current = window.setTimeout(() => setHot(null), HOT_MS);
  }

  function pick(question: string) {
    onAsk(question);
    focusComposer();
  }

  const nextSteps = (
    <NextSteps
      actions={suggestions}
      followUps={followUps}
      busy={busy}
      onAsk={onAsk}
      withReason={!explanation && !hasTable}
      compact={hasTable}
    />
  );

  return (
    <motion.article
      className="msg msg--assistant"
      data-outcome={outcome}
      aria-label="Copilot reply"
      // A reply asked for in this session arrives; one reopened from history is simply there.
      initial={reopened || reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={ENTER}
      exit={reduce ? undefined : { opacity: 0, transition: CROSSFADE }}
    >
      {repeated ? (
        <>
          <RepeatCard asOf={envelope.scope_line?.as_of ?? envelope.scope.as_of} onJump={onJump} onShowAgain={() => setExpanded(true)} />
          {nextSteps}
        </>
      ) : (
        <>
          {markShown ? (
            <div className="msg__state">
              <StateMark outcome={outcome} reason={envelope.reason_code} />
            </div>
          ) : null}

          <Lead
            text={lead}
            // Only the composer's own headline is read for a count, never the model's words or a question.
            counted={reply.headline}
            withList={claimsInReply}
            headline={reply.headline && outcome === "answer"}
          />

          {support ? <p className="msg__support">{support}</p> : null}

          {claimsInReply ? <Claims claims={envelope.claims} /> : null}

          {clarification ? (
            <div className="chips" role="group" aria-label="Choose one">
              {clarification.options.map((option) => (
                <button key={option} type="button" className="chip" disabled={busy} onClick={() => pick(option)}>
                  {option}
                </button>
              ))}
            </div>
          ) : null}

          {hasTable && table ? (
            <>
              <AnswerTable
                table={table}
                sort={sort}
                onSort={setSort}
                numbered={numbered}
                onSource={showSource}
                scope={scope}
                reopened={reopened}
              />
              <AnswerDisclosures
                envelope={envelope}
                numbered={numbered}
                note={note}
                open={folds}
                onOpen={(which, value) => setFolds((now) => ({ ...now, [which]: value }))}
                hot={hot}
                rowId={rowId}
                onOpenTrace={onOpenTrace}
              />
            </>
          ) : null}

          {lists.map((list) => (
            <DeviceChips key={list.list_id} list={list} />
          ))}

          {isAdvice ? nextSteps : null}

          {envelope.proposal ? (
            <ProposalView
              proposal={envelope.proposal}
              onOpenEvidence={onOpenEvidence}
              decide={decide}
              recheck={reopened}
              onSynced={(card) => setDecision(card.status)}
              onDecided={(card) => {
                setDecision(card.status);
                onDecided?.(card);
              }}
              onSessionEnded={onSessionEnded}
            />
          ) : null}

          {notes.length > 0 ? (
            <ul className="msg__notes" aria-label="Notes">
              {notes.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          ) : null}

          <AlternativeChips alternatives={envelope.alternatives ?? []} busy={busy} onAsk={onAsk} />

          {isAdvice ? null : nextSteps}

          {hasTable ? null : (
            <div className="details">
              <button
                type="button"
                className="details__toggle"
                aria-expanded={open}
                aria-controls={detailsId}
                onClick={() => setOpen((value) => !value)}
              >
                Details
              </button>
              {/* The panel is always here for the toggle to name; its body folds in and out, inert and hidden once closed. */}
              <div className="details__panel" id={detailsId} data-open={open} inert={!open} aria-hidden={!open}>
                <Presence>
                  {open ? (
                    <motion.div
                      key="body"
                      className="details__clip"
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1, transition: FOLD_IN }}
                      exit={{ height: 0, opacity: 0, transition: FOLD_OUT }}
                    >
                      <div className="details__body">
                        {!claimsInReply && envelope.claims.length > 0 ? <Claims claims={envelope.claims} /> : null}
                        {explanation && explanation !== support ? <p className="msg__claim">{explanation}</p> : null}
                        <Facts envelope={envelope} />
                        {note ? <p className="details__note">Simulated example. {note}</p> : null}
                        <div className="details__actions">
                          {envelope.evidence.length > 0 ? (
                            <button type="button" className="link-btn" onClick={() => onOpenEvidence()}>
                              <FileSearch size={15} aria-hidden="true" />
                              Evidence ({envelope.evidence.length})
                            </button>
                          ) : null}
                          <button type="button" className="link-btn" onClick={onOpenTrace}>
                            <ListTree size={15} aria-hidden="true" />
                            Show work
                          </button>
                        </div>
                      </div>
                    </motion.div>
                  ) : null}
                </Presence>
              </div>
            </div>
          )}
        </>
      )}

      {envelope.proposal || clarification ? null : (
        <ReplyTools
          text={copyText(envelope, hasTable ? rows : undefined)}
          onRetry={question ? () => onAsk(question) : undefined}
          busy={busy}
        />
      )}
    </motion.article>
  );
}
