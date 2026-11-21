import { MotionConfig, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalsView } from "./components/ApprovalsView";
import { AuditView } from "./components/AuditView";
import { Aurora } from "./components/Aurora";
import { Composer } from "./components/Composer";
import { EvidenceDrawer, type DrawerTab } from "./components/EvidenceDrawer";
import { Hero, Suggestions } from "./components/Home";
import { ObserveView } from "./components/observe/ObserveView";
import { Rail } from "./components/Rail";
import { SignIn } from "./components/SignIn";
import { TopBar } from "./components/TopBar";
import { Thread, type TurnData } from "./components/Thread";
import { ApiError, UNREACHABLE } from "./lib/api";
import { datasetAsOf, fixturesFor, tenantName } from "./lib/fixtures";
import { chooseTransport, type Session, type Transport } from "./lib/transport";
import type { AnswerEnvelope, EvidenceItem } from "./lib/types";
import { parseRef } from "./lib/format";

const DOCK = { duration: 0.9, ease: [0.16, 1, 0.3, 1] as const };
const OBSERVE_HASH = "#/observe";
const APPROVALS_HASH = "#/approvals";
const AUDIT_HASH = "#/audit";
const SESSION_ENDED = "Your session ended. Sign in again.";
/** The server's own limit and words for a question it would refuse; checked here so the thread only docks for one that will be sent. */
const MAX_QUESTION_CHARS = 2000;
const EMPTY_QUESTION = "Ask a question about your devices.";
const LONG_QUESTION = `Questions are limited to ${MAX_QUESTION_CHARS} characters.`;
const DATASET_UNAVAILABLE = "The dataset is not available. Run make load-data, then try again.";

interface DrawerState {
  turnId: string;
  tab: DrawerTab;
  refs: string[];
  /** Set when the drawer was opened from the approvals view, where no answer is at hand. */
  proposal?: { subject: string };
}

interface AppProps {
  /** The only source of data. Defaults to the live API, or the offline replay when built for fixtures. */
  transport?: Transport;
}

/** Replayed stand-in answers carry a note in their fixture; the envelope alone does not. */
function fixtureNote(envelope: AnswerEnvelope): string | undefined {
  const match = fixturesFor(envelope.scope.company_id).find((f) => f.envelope.trace_id === envelope.trace_id);
  return match?.meta.synthetic ? match.meta.note : undefined;
}

/** Signing out leaves no view behind: a route hash from this session must not carry to the next one. */
function clearRouteHash() {
  if ([OBSERVE_HASH, APPROVALS_HASH, AUDIT_HASH].includes(window.location.hash)) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
}

function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return hash;
}

/** A reading as the drawer lists it when only its reference is known: the check or field, and when it was taken. */
function readingFor(ref: string, known: EvidenceItem[]): EvidenceItem {
  const found = known.find((item) => item.ref === ref);
  if (found) return found;
  const parts = parseRef(ref);
  return {
    ref,
    label: parts ? parts.path.replaceAll("_", " ") : "Reading",
    value: parts ? `Taken ${parts.collectedAt.slice(0, 10)} ${parts.collectedAt.slice(11, 16)} UTC` : "Stored reading",
  };
}

export default function App({ transport: given }: AppProps) {
  const transport = useMemo(() => given ?? chooseTransport(), [given]);
  const [booting, setBooting] = useState(true);
  const [session, setSession] = useState<Session | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnData[]>([]);
  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  const [asOf, setAsOf] = useState<string | null>(transport.mode === "fixtures" ? datasetAsOf() : null);
  const [composerError, setComposerError] = useState<string | null>(null);
  const [restore, setRestore] = useState<{ text: string } | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  /** Bumped whenever the conversation is thrown away, so the drawer drops what it fetched. */
  const [generation, setGeneration] = useState(0);
  /** Proposals waiting for a decision; null until the first count arrives. */
  const [pending, setPending] = useState<number | null>(null);
  const hash = useHash();

  const epoch = useRef(0);
  const conversation = useRef<string | null>(null);
  const turnSeq = useRef(0);
  const inFlight = useRef(false);
  const countSeq = useRef(0);

  useEffect(() => {
    let alive = true;
    transport.session().then(
      (found) => alive && setSession(found),
      () => alive && setSession(null),
    ).finally(() => alive && setBooting(false));
    return () => {
      alive = false;
    };
  }, [transport]);

  const role = session?.role ?? null;

  useEffect(() => {
    if (role === "operator" && window.location.hash !== OBSERVE_HASH) window.location.hash = OBSERVE_HASH;
  }, [role]);

  useEffect(() => {
    if (!session || transport.mode === "fixtures") return;
    let alive = true;
    transport.policy().then(
      (policy) => alive && setAsOf(policy.as_of),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [session, transport]);

  /** Throw away the thread and the conversation. In-flight turns from before this are ignored. */
  const resetConversation = useCallback(() => {
    epoch.current += 1;
    conversation.current = null;
    turnSeq.current = 0;
    inFlight.current = false;
    setTurns([]);
    setDrawer(null);
    setComposerError(null);
    setRestore(null);
    setBanner(null);
    setGeneration((n) => n + 1);
  }, []);

  const endSession = useCallback(
    (why: string | null) => {
      resetConversation();
      clearRouteHash();
      countSeq.current += 1;
      setPending(null);
      setSession(null);
      setNotice(why);
    },
    [resetConversation],
  );

  /** Re-reads how many proposals wait for a decision. A failed read keeps the last count; a later one replaces it. */
  const refreshPending = useCallback(() => {
    const mine = ++countSeq.current;
    void (async () => {
      try {
        const waiting = await transport.proposals("pending");
        if (mine === countSeq.current) setPending(waiting.length);
      } catch (error) {
        if (mine === countSeq.current && error instanceof ApiError && error.status === 401) endSession(SESSION_ENDED);
      }
    })();
  }, [transport, endSession]);

  const isAdmin = session?.role === "admin";
  useEffect(() => {
    if (isAdmin) refreshPending();
  }, [isAdmin, refreshPending]);

  const patchTurn = useCallback((id: string, change: (turn: TurnData) => TurnData) => {
    setTurns((prev) => prev.map((turn) => (turn.id === id ? change(turn) : turn)));
  }, []);

  const ask = useCallback(
    (question: string) => {
      if (session?.role !== "admin" || inFlight.current) return;
      const text = question.trim();
      const refused = !text ? EMPTY_QUESTION : [...text].length > MAX_QUESTION_CHARS ? LONG_QUESTION : null;
      if (refused) {
        setComposerError(refused);
        setRestore({ text: question });
        return;
      }
      inFlight.current = true;
      const mine = epoch.current;
      const live = () => epoch.current === mine;
      const id = String(++turnSeq.current);
      setBanner(null);
      setComposerError(null);
      setTurns((prev) => [...prev, { id, question, events: [], status: "working" }]);

      const drop = () => setTurns((prev) => prev.filter((turn) => turn.id !== id));

      void (async () => {
        try {
          if (!conversation.current) {
            const created = await transport.newConversation();
            if (!live()) return;
            conversation.current = created.conversation_id;
          }
          const envelope = await transport.ask(conversation.current, question, (event) => {
            if (!live()) return;
            patchTurn(id, (turn) =>
              turn.events.some((seen) => seen.seq === event.seq)
                ? turn
                : { ...turn, events: [...turn.events, event] },
            );
          });
          if (!live()) return;
          const note = transport.mode === "fixtures" ? fixtureNote(envelope) : undefined;
          patchTurn(id, (turn) => ({ ...turn, status: "done", envelope, note }));
          if (envelope.outcome === "proposal") refreshPending();
        } catch (error) {
          if (!live()) return;
          const status = error instanceof ApiError ? error.status : 0;
          const detail = error instanceof ApiError ? error.detail : UNREACHABLE;
          if (status === 401) {
            endSession(SESSION_ENDED);
          } else if (status === 422) {
            drop();
            setComposerError(detail);
            setRestore({ text: question });
          } else if (status === 503) {
            drop();
            setBanner(DATASET_UNAVAILABLE);
          } else {
            patchTurn(id, (turn) => ({ ...turn, status: "failed", failure: status === 0 ? UNREACHABLE : detail }));
          }
        } finally {
          if (live()) inFlight.current = false;
        }
      })();
    },
    [session, transport, patchTurn, endSession, refreshPending],
  );

  async function signOut() {
    try {
      await transport.signOut();
    } catch {
      /* the cookie may already be gone; the screen goes back to sign-in either way */
    }
    endSession(null);
  }

  const loadEvidence = useMemo(
    () => (transport.mode === "live" ? (ref: string) => transport.evidence(ref) : undefined),
    [transport],
  );
  const loadTrace = useMemo(() => (traceId: string) => transport.trace(traceId), [transport]);
  const sessionEnded = useCallback(() => endSession(SESSION_ENDED), [endSession]);

  const drawerTurn = useMemo(() => turns.find((t) => t.id === drawer?.turnId) ?? null, [turns, drawer]);
  const drawerReadings = useMemo(() => {
    if (!drawer?.proposal) return undefined;
    const known = turns.flatMap((turn) => turn.envelope?.evidence ?? []);
    return drawer.refs.map((ref) => readingFor(ref, known));
  }, [turns, drawer]);
  const onApprovals = hash === APPROVALS_HASH;
  const onAudit = hash === AUDIT_HASH;
  const onSubview = onApprovals || onAudit;
  const openProposalEvidence = useCallback(
    (refs: string[], subject: string) => setDrawer({ turnId: "", tab: "evidence", refs, proposal: { subject } }),
    [],
  );
  const running = turns.some((turn) => turn.status === "working");
  const docked = turns.length > 0;

  if (booting) {
    return (
      <MotionConfig reducedMotion="user">
        <Aurora docked={false} streaming={false} />
        <div className="app" aria-busy="true">
          <TopBar asOf={asOf} replay={transport.mode === "fixtures"} />
          <main className="stage" data-view="home" />
        </div>
      </MotionConfig>
    );
  }

  if (!session) {
    return (
      <MotionConfig reducedMotion="user">
        <Aurora docked={false} streaming={false} />
        <div className="app">
          <TopBar asOf={asOf} replay={transport.mode === "fixtures"} />
          <main className="stage" data-view="home">
            <SignIn
              transport={transport}
              notice={notice}
              onSignedIn={(next) => {
                setNotice(null);
                setSession(next);
              }}
            />
          </main>
        </div>
      </MotionConfig>
    );
  }

  if (session.role === "operator") {
    return (
      <MotionConfig reducedMotion="user">
        <Aurora docked streaming={false} />
        <div className="app">
          <TopBar operator asOf={asOf} replay={transport.mode === "fixtures"} onSignOut={signOut} />
          <main className="stage" data-view="observe">
            <ObserveView transport={transport} onSessionEnded={sessionEnded} />
          </main>
        </div>
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <Aurora docked={docked || onSubview} streaming={running} />
      <div className="app" data-rail={docked && !onSubview}>
        <TopBar
          companyName={tenantName(session.company_id)}
          asOf={asOf}
          replay={transport.mode === "fixtures"}
          onSignOut={signOut}
          approvals={{ pending, active: onApprovals }}
          audit={{ active: onAudit }}
        />
        {docked && !onSubview ? <Rail turns={turns} onNew={resetConversation} /> : null}
        {onApprovals ? (
          <main className="stage" data-view="approvals">
            <ApprovalsView
              transport={transport}
              onOpenEvidence={openProposalEvidence}
              onDecided={refreshPending}
              onSessionEnded={sessionEnded}
            />
          </main>
        ) : onAudit ? (
          <main className="stage" data-view="approvals">
            <AuditView transport={transport} onOpenEvidence={openProposalEvidence} onSessionEnded={sessionEnded} />
          </main>
        ) : (
        <main className="stage" data-view={docked ? "thread" : "home"}>
          <div className="stage__main">
            {docked ? (
              <Thread
                turns={turns}
                onAsk={ask}
                decide={transport}
                onDecided={refreshPending}
                onSessionEnded={sessionEnded}
                onOpenEvidence={(turn, refs) => setDrawer({ turnId: turn.id, tab: "evidence", refs: refs ?? [] })}
                onOpenTrace={(turn) => setDrawer({ turnId: turn.id, tab: "trace", refs: [] })}
              />
            ) : (
              <Hero />
            )}
          </div>
          <motion.div layout="position" transition={DOCK} className="stage__composer">
            {banner ? (
              <p className="banner" role="status">
                {banner}
              </p>
            ) : null}
            <Composer
              docked={docked}
              busy={running}
              onAsk={ask}
              error={composerError}
              restore={restore}
              onEdit={() => setComposerError(null)}
            />
          </motion.div>
          {docked ? null : <Suggestions onAsk={ask} />}
        </main>
        )}
      </div>
      <EvidenceDrawer
        key={generation}
        open={drawer !== null}
        tab={drawer?.tab ?? "evidence"}
        focusRefs={drawer?.refs ?? []}
        envelope={drawerTurn?.envelope ?? null}
        readings={drawerReadings}
        subject={drawer?.proposal?.subject}
        trace={drawerTurn?.events ?? []}
        onTab={(tab) => setDrawer((d) => (d ? { ...d, tab } : d))}
        onClose={() => setDrawer(null)}
        loadEvidence={loadEvidence}
        loadTrace={loadTrace}
        onSessionEnded={sessionEnded}
      />
    </MotionConfig>
  );
}
