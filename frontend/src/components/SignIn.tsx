import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { ApiError, UNREACHABLE } from "../lib/api";
import { TENANTS, fleetSize, tenantName } from "../lib/fixtures";
import type { DemoUser, Session, Transport } from "../lib/transport";

const OPERATOR = "operator";

interface SignInProps {
  transport: Transport;
  onSignedIn: (session: Session) => void;
  /** Why the person is back here, e.g. an ended session. */
  notice?: string | null;
}

function failureWords(error: unknown): string {
  if (error instanceof ApiError && error.status !== 0) {
    const detail = error.detail.trim();
    return detail ? detail.charAt(0).toUpperCase() + detail.slice(1) : "Sign-in failed";
  }
  return UNREACHABLE;
}

function companyMeta(user: DemoUser, transport: Transport): string {
  if (transport.mode === "fixtures" && TENANTS.some((tenant) => tenant.id === user.company_id)) {
    const size = fleetSize(user.company_id);
    if (size !== null) return `Admin · ${size} devices`;
  }
  return user.display_name;
}

export function SignIn({ transport, onSignedIn, notice }: SignInProps) {
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [operatorOpen, setOperatorOpen] = useState(false);
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const passcodeInput = useRef<HTMLInputElement>(null);
  const errorId = useId();

  useEffect(() => {
    let alive = true;
    setLoadError(null);
    transport.demoUsers().then(
      (list) => alive && setUsers(list),
      (reason) => alive && setLoadError(failureWords(reason)),
    );
    return () => {
      alive = false;
    };
  }, [transport, attempt]);

  useEffect(() => {
    if (operatorOpen) passcodeInput.current?.focus();
  }, [operatorOpen]);

  async function signIn(user: string, code?: string) {
    setBusy(true);
    setError(null);
    try {
      const session = code === undefined ? await transport.signIn(user) : await transport.signIn(user, code);
      onSignedIn(session);
    } catch (reason) {
      setError(failureWords(reason));
      setBusy(false);
    }
  }

  function submitOperator(event: FormEvent) {
    event.preventDefault();
    if (!busy) void signIn(OPERATOR, passcode);
  }

  return (
    <section className="signin" aria-labelledby="signin-title">
      <div className="hero">
        <p className="hero__label">Fleet Copilot · Demo sign-in</p>
        <div className="hero__display">
          <h1 className="hero__title" id="signin-title">
            Whose fleet are you looking after?
          </h1>{" "}
          <p className="hero__sub">Each admin sees one company only.</p>
        </div>
      </div>
      {notice ? (
        <p className="signin__notice" role="status">
          {notice}
        </p>
      ) : null}
      <div className="signin__list" role="group" aria-label="Demo companies">
        {(users ?? []).map((user) => (
          <button
            key={user.user}
            type="button"
            className="company"
            disabled={busy}
            onClick={() => void signIn(user.user)}
          >
            <span className="company__name">{tenantName(user.company_id)}</span>
            <span className="company__meta">{companyMeta(user, transport)}</span>
          </button>
        ))}
        {users ? (
          <button
            type="button"
            className="company"
            aria-expanded={operatorOpen}
            disabled={busy}
            onClick={() => setOperatorOpen((open) => !open)}
          >
            <span className="company__name">Operator</span>
            <span className="company__meta">All companies · read-only</span>
          </button>
        ) : null}
      </div>
      {loadError ? (
        <p className="form-error" role="alert">
          {loadError}{" "}
          <button type="button" className="link-btn" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </p>
      ) : null}
      {operatorOpen ? (
        <form className="signin__operator" onSubmit={submitOperator}>
          <label htmlFor="operator-passcode">Operator passcode</label>
          <div className="signin__row">
            <input
              id="operator-passcode"
              ref={passcodeInput}
              className="signin__input"
              type="password"
              autoComplete="off"
              value={passcode}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
              onChange={(event) => setPasscode(event.target.value)}
            />
            <button type="submit" className="btn btn--primary" disabled={busy || passcode.length === 0}>
              Sign in as operator
            </button>
          </div>
        </form>
      ) : null}
      {error ? (
        <p className="form-error" role="alert" id={errorId}>
          {error}
        </p>
      ) : null}
    </section>
  );
}
