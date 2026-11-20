import { MotionConfig, motion } from "motion/react";
import { useCallback, useMemo, useState } from "react";
import { Aurora } from "./components/Aurora";
import { Composer } from "./components/Composer";
import { EvidenceDrawer, type DrawerTab } from "./components/EvidenceDrawer";
import { Hero, Suggestions } from "./components/Home";
import { Rail } from "./components/Rail";
import { SignIn } from "./components/SignIn";
import { TopBar } from "./components/TopBar";
import { Thread, type TurnData } from "./components/Thread";
import { datasetAsOf, tenantName } from "./lib/fixtures";
import { routeQuestion } from "./lib/fixtureRouter";

const STORAGE_KEY = "fleet-copilot.tenant";
const DOCK = { duration: 0.9, ease: [0.16, 1, 0.3, 1] as const };

interface DrawerState {
  turnId: string;
  tab: DrawerTab;
  refs: string[];
}

function readTenant(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeTenant(id: string | null) {
  try {
    if (id) window.sessionStorage.setItem(STORAGE_KEY, id);
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* session storage unavailable: the session just won't survive a reload */
  }
}

interface AppProps {
  /** Pause between replayed steps. Tests pass 0. */
  stepMs?: number;
}

export default function App({ stepMs = 380 }: AppProps) {
  const [tenant, setTenant] = useState<string | null>(readTenant);
  const [turns, setTurns] = useState<TurnData[]>([]);
  const [settled, setSettled] = useState<Set<string>>(new Set());
  const [drawer, setDrawer] = useState<DrawerState | null>(null);

  const running = turns.some((turn) => !settled.has(turn.id));
  const docked = turns.length > 0;
  const asOf = datasetAsOf();

  const ask = useCallback(
    (question: string) => {
      if (!tenant) return;
      const fixture = routeQuestion(tenant, question);
      setTurns((prev) => [...prev, { id: String(prev.length + 1), question, fixture }]);
    },
    [tenant],
  );

  const onSettled = useCallback((id: string) => {
    setSettled((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, []);

  function signIn(id: string) {
    storeTenant(id);
    setTenant(id);
  }

  function signOut() {
    storeTenant(null);
    setTenant(null);
    setTurns([]);
    setSettled(new Set());
    setDrawer(null);
  }

  function newQuestion() {
    setTurns([]);
    setSettled(new Set());
    setDrawer(null);
  }

  const drawerTurn = useMemo(() => turns.find((t) => t.id === drawer?.turnId) ?? null, [turns, drawer]);

  if (!tenant) {
    return (
      <MotionConfig reducedMotion="user">
        <Aurora docked={false} streaming={false} />
        <div className="app">
          <TopBar asOf={asOf} />
          <main className="stage" data-view="home">
            <SignIn onSignIn={signIn} />
          </main>
        </div>
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <Aurora docked={docked} streaming={running} />
      <div className="app" data-rail={docked}>
        <TopBar companyName={tenantName(tenant)} asOf={asOf} onSignOut={signOut} />
        {docked ? <Rail turns={turns} onNew={newQuestion} /> : null}
        <main className="stage" data-view={docked ? "thread" : "home"}>
          <div className="stage__main">
            {docked ? (
              <Thread
                turns={turns}
                stepMs={stepMs}
                onSettled={onSettled}
                onAsk={ask}
                onOpenEvidence={(turn, refs) =>
                  setDrawer({ turnId: turn.id, tab: "evidence", refs: refs ?? [] })
                }
                onOpenTrace={(turn) => setDrawer({ turnId: turn.id, tab: "trace", refs: [] })}
              />
            ) : (
              <Hero />
            )}
          </div>
          <motion.div layout="position" transition={DOCK} className="stage__composer">
            <Composer docked={docked} busy={running} onAsk={ask} />
          </motion.div>
          {docked ? null : <Suggestions onAsk={ask} />}
        </main>
      </div>
      <EvidenceDrawer
        open={drawer !== null}
        tab={drawer?.tab ?? "evidence"}
        focusRefs={drawer?.refs ?? []}
        envelope={drawerTurn?.fixture.envelope ?? null}
        trace={drawerTurn?.fixture.trace ?? []}
        onTab={(tab) => setDrawer((d) => (d ? { ...d, tab } : d))}
        onClose={() => setDrawer(null)}
      />
    </MotionConfig>
  );
}
