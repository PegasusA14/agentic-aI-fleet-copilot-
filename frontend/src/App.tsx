import { MotionConfig, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Aurora } from "./components/Aurora";
import { Composer } from "./components/Composer";
import { EvidenceDrawer, type DrawerTab } from "./components/EvidenceDrawer";
import { Hero, Suggestions } from "./components/Home";
import { Rail } from "./components/Rail";
import { SignIn } from "./components/SignIn";
import { TopBar } from "./components/TopBar";
import { Thread, type TurnData } from "./components/Thread";
import { ApiError, UNREACHABLE } from "./lib/api";
import { datasetAsOf, fixturesFor, tenantName } from "./lib/fixtures";
import { chooseTransport, type Session, type Transport } from "./lib/transport";
import type { AnswerEnvelope } from "./lib/types";

const DOCK = { duration: 0.9, ease: [0.16, 1, 0.3, 1] as const };
const OBSERVE_HASH = "#/observe";
const SESSION_ENDED = "Your session ended. Sign in again.";
const DATASET_UNAVAILABLE = "The dataset is not available. Run make load-data, then try again.";

interface DrawerState {
  turnId: string;
  tab: DrawerTab;
  refs: string[];
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

function clearObserveHash() {
  if (window.location.hash === OBSERVE_HASH) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
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

  const epoch = useRef(0);
  const conversation = useRef<string | null>(null);
  const turnSeq = useRef(0);
  const inFlight = useRef(false);

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
      clearObserveHash();
      setSession(null);
      setNotice(why);
    },
    [resetConversation],
  );

  const patchTurn = useCallback((id: string, change: (turn: TurnData) => TurnData) => {
    setTurns((prev) => prev.map((turn) => (turn.id === id ? change(turn) : turn)));
  }, []);

  const ask = useCallback(
    (question: string) => {
      if (session?.role !== "admin" || inFlight.current) return;
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
    [session, transport, patchTurn, endSession],
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
        <Aurora docked={false} streaming={false} />
        <div className="app">
          <TopBar operator asOf={asOf} replay={transport.mode === "fixtures"} onSignOut={signOut} />
          <main className="stage" data-view="home">
            <p className="fine">The operator console is not connected yet.</p>
          </main>
        </div>
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <Aurora docked={docked} streaming={running} />
      <div className="app" data-rail={docked}>
        <TopBar
          companyName={tenantName(session.company_id)}
          asOf={asOf}
          replay={transport.mode === "fixtures"}
          onSignOut={signOut}
          onNewConversation={resetConversation}
          newConversationDisabled={!docked}
        />
        {docked ? <Rail turns={turns} onNew={resetConversation} /> : null}
        <main className="stage" data-view={docked ? "thread" : "home"}>
          <div className="stage__main">
            {docked ? (
              <Thread
                turns={turns}
                onAsk={ask}
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
      </div>
      <EvidenceDrawer
        key={generation}
        open={drawer !== null}
        tab={drawer?.tab ?? "evidence"}
        focusRefs={drawer?.refs ?? []}
        envelope={drawerTurn?.envelope ?? null}
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
