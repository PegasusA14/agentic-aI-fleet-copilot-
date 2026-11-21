import { MotionConfig, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalsView } from "./components/ApprovalsView";
import { AuditView } from "./components/AuditView";
import { Composer } from "./components/Composer";
import { EvidenceDrawer, type DrawerTab } from "./components/EvidenceDrawer";
import { Hero, Suggestions, type FleetInfo } from "./components/Home";
import { ObserveView } from "./components/observe/ObserveView";
import { Sidebar } from "./components/Sidebar";
import { SignIn } from "./components/SignIn";
import { TopBar } from "./components/TopBar";
import { Thread, type TurnData } from "./components/Thread";
import { ApiError, UNREACHABLE } from "./lib/api";
import { adviseFixtures } from "./lib/fixtureAdvise";
import { datasetAsOf, fixturesFor, tenantName } from "./lib/fixtures";
import { chooseTransport, type ConversationSummary, type Session, type StoredConversation, type Transport } from "./lib/transport";
import { useMediaQuery } from "./lib/useMediaQuery";
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
const CHAT_HASH = "#/";
/** Under this width the history sidebar becomes a drawer that the top bar's menu button opens. */
const NARROW_QUERY = "(max-width: 960px)";
const CONVERSATION_GONE = "That conversation is no longer available.";
const CONVERSATION_UNREADABLE = "That conversation could not be opened. Try again.";

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
  const company = envelope.scope.company_id;
  const match = [...fixturesFor(company), ...adviseFixtures(company)].find((f) => f.envelope.trace_id === envelope.trace_id);
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
  /** The history list for the sidebar; null until it has loaded. */
  const [history, setHistory] = useState<ConversationSummary[] | null>(null);
  const [historyFailed, setHistoryFailed] = useState(false);
  /** The conversation the thread continues, once it has an id. */
  const [activeId, setActiveId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const narrow = useMediaQuery(NARROW_QUERY);
  const hash = useHash();

  const epoch = useRef(0);
  const conversation = useRef<string | null>(null);
  const turnSeq = useRef(0);
  const inFlight = useRef(false);
  const countSeq = useRef(0);
  const historySeq = useRef(0);
  const openSeq = useRef(0);
  /** Bumped only when the session ends, so a turn that outlives "New chat" still refreshes the list but one that outlives sign-out does not. */
  const sessionEpoch = useRef(0);

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
    setActiveId(null);
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
      historySeq.current += 1;
      openSeq.current += 1;
      sessionEpoch.current += 1;
      setPending(null);
      setHistory(null);
      setHistoryFailed(false);
      setDrawerOpen(false);
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

  /** Re-reads the history list. A failed read keeps the list already shown; the sidebar only says so when it has none. */
  const refreshHistory = useCallback(() => {
    const mine = ++historySeq.current;
    void (async () => {
      try {
        const list = await transport.conversations();
        if (mine !== historySeq.current) return;
        setHistory(list);
        setHistoryFailed(false);
      } catch (error) {
        if (mine !== historySeq.current) return;
        if (error instanceof ApiError && error.status === 401) endSession(SESSION_ENDED);
        else setHistoryFailed(true);
      }
    })();
  }, [transport, endSession]);

  const isAdmin = session?.role === "admin";
  useEffect(() => {
    if (isAdmin) {
      refreshPending();
      refreshHistory();
    }
  }, [isAdmin, refreshPending, refreshHistory]);

  useEffect(() => {
    if (!narrow) setDrawerOpen(false);
  }, [narrow]);

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
      const mineSession = sessionEpoch.current;
      const live = () => epoch.current === mine;
      const id = String(++turnSeq.current);
      setBanner(null);
      setComposerError(null);
      setTurns((prev) => [...prev, { id, question, events: [], status: "working", fresh: true }]);

      const drop = () => setTurns((prev) => prev.filter((turn) => turn.id !== id));

      void (async () => {
        try {
          if (!conversation.current) {
            const created = await transport.newConversation();
            if (!live()) return;
            conversation.current = created.conversation_id;
            setActiveId(created.conversation_id);
          }
          const envelope = await transport.ask(conversation.current, question, (event) => {
            if (!live()) return;
            patchTurn(id, (turn) =>
              turn.events.some((seen) => seen.seq === event.seq)
                ? turn
                : { ...turn, events: [...turn.events, event] },
            );
          });
          // The server keeps the turn even when the reader has moved on, so the list refreshes either way.
          if (sessionEpoch.current === mineSession) refreshHistory();
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
    [session, transport, patchTurn, endSession, refreshPending, refreshHistory],
  );

  const goToChat = useCallback(() => {
    if (window.location.hash === APPROVALS_HASH || window.location.hash === AUDIT_HASH) {
      window.location.hash = CHAT_HASH;
    }
  }, []);

  const newChat = useCallback(() => {
    resetConversation();
    goToChat();
  }, [resetConversation, goToChat]);

  /** Shows a stored conversation's turns with the thread's own rendering; the next question continues it. */
  const openConversation = useCallback(
    (id: string) => {
      goToChat();
      if (id === conversation.current && turnSeq.current > 0) return;
      const mine = ++openSeq.current;
      void (async () => {
        let stored: StoredConversation;
        try {
          stored = await transport.conversation(id);
        } catch (error) {
          if (mine !== openSeq.current) return;
          const status = error instanceof ApiError ? error.status : 0;
          if (status === 401) return endSession(SESSION_ENDED);
          if (status === 404) refreshHistory();
          setBanner(status === 404 ? CONVERSATION_GONE : CONVERSATION_UNREADABLE);
          return;
        }
        if (mine !== openSeq.current) return;
        resetConversation();
        conversation.current = id;
        turnSeq.current = stored.turns.length;
        setActiveId(id);
        setTurns(
          stored.turns.map((turn) => ({
            id: String(turn.seq + 1),
            question: turn.question,
            events: [],
            status: "done" as const,
            envelope: turn.envelope,
            note: transport.mode === "fixtures" ? fixtureNote(turn.envelope) : undefined,
          })),
        );
      })();
    },
    [transport, goToChat, resetConversation, endSession, refreshHistory],
  );

  /** Rejects when the conversation could not be deleted, so the sidebar can say so. */
  const deleteConversation = useCallback(
    async (id: string) => {
      try {
        await transport.deleteConversation(id);
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        if (status === 401) return endSession(SESSION_ENDED);
        if (status !== 404) throw error; // already gone is as good as deleted
      }
      setHistory((list) => list && list.filter((item) => item.conversation_id !== id));
      if (conversation.current === id) resetConversation();
    },
    [transport, endSession, resetConversation],
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
  const openProposalEvidence = useCallback(
    (refs: string[], subject: string) => setDrawer({ turnId: "", tab: "evidence", refs, proposal: { subject } }),
    [],
  );
  /** The home pulse comes from the API's fleet summary (the replay's own numbers in fixture mode), fetched once per sign-in. */
  const company = session?.company_id ?? null;
  const isTenantAdmin = session?.role === "admin";
  const [summary, setSummary] = useState<{ company: string; info: FleetInfo } | null>(null);
  useEffect(() => {
    if (!company || !isTenantAdmin) return;
    let current = true;
    transport.fleetSummary().then(
      (got) => current && setSummary({ company, info: { status: "ready", size: got.devices, flagged: got.flagged } }),
      () => current && setSummary({ company, info: { status: "error" } }),
    );
    return () => {
      current = false;
    };
  }, [company, isTenantAdmin, transport]);
  const fleet: FleetInfo = summary && summary.company === company ? summary.info : { status: "loading" };
  const running = turns.some((turn) => turn.status === "working");
  const docked = turns.length > 0;

  if (booting) {
    return (
      <MotionConfig reducedMotion="user">
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
      <div className="app" data-sidebar="true">
        <TopBar
          companyName={tenantName(session.company_id)}
          asOf={asOf}
          replay={transport.mode === "fixtures"}
          onSignOut={signOut}
          onMenu={narrow ? () => setDrawerOpen(true) : undefined}
        />
        <Sidebar
          conversations={history}
          loadFailed={historyFailed}
          activeId={activeId}
          pending={pending}
          view={onApprovals ? "approvals" : onAudit ? "audit" : "chat"}
          narrow={narrow}
          drawerOpen={drawerOpen}
          onDrawerChange={setDrawerOpen}
          onNew={newChat}
          onOpen={openConversation}
          onDelete={deleteConversation}
          onRetry={refreshHistory}
        />
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
              <Hero asOf={asOf} fleet={fleet} />
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
