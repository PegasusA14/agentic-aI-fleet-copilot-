import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, UNREACHABLE } from "../../lib/api";

export type Load<T> = { state: "loading" } | { state: "failed"; detail: string } | { state: "ready"; data: T };

/**
 * Runs `fetcher` whenever `deps` change and says where the request stands. A 401 ends the session instead of
 * showing an error; any other failure keeps the server's words, or the unreachable sentence when none arrived.
 * `retry` runs the same request again.
 */
export function useLoad<T>(fetcher: () => Promise<T>, deps: readonly unknown[], onSessionEnded: () => void) {
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const latest = useRef({ fetcher, onSessionEnded });
  latest.current = { fetcher, onSessionEnded };

  useEffect(() => {
    let alive = true;
    setLoad({ state: "loading" });
    latest.current.fetcher().then(
      (data) => alive && setLoad({ state: "ready", data }),
      (error) => {
        if (!alive) return;
        if (error instanceof ApiError && error.status === 401) {
          latest.current.onSessionEnded();
          return;
        }
        setLoad({ state: "failed", detail: error instanceof ApiError && error.status !== 0 ? error.detail : UNREACHABLE });
      },
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { load, retry };
}
