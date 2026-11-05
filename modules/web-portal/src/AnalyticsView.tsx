import type { FormEvent, RefObject } from "react";
import type { PortalSession } from "./identity.js";

export type AnalyticsState =
  | { kind: "loading" }
  | { kind: "signed-out"; message?: string }
  | { kind: "signed-in"; session: PortalSession; message?: string };

interface Props {
  state: AnalyticsState;
  busy: boolean;
  mountRef?: RefObject<HTMLDivElement | null>;
  onSignIn: (username: string, password: string) => void;
  onSignOut: () => void;
}

export function AnalyticsView({ state, busy, mountRef, onSignIn, onSignOut }: Props) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSignIn(String(form.get("username") ?? ""), String(form.get("password") ?? ""));
  };
  return (
    <main className="wide">
      <header>
        <p className="eyebrow">Realtime payment platform</p>
        <h1>Payment operations</h1>
        <p>
          Embedded Superset dashboard · Synthetic data only · Local development identity, not for
          production
        </p>
      </header>
      {state.kind === "loading" && <p role="status">Checking your session…</p>}
      {state.kind === "signed-out" && (
        <section aria-labelledby="sign-in">
          <h2 id="sign-in">Sign in</h2>
          <form onSubmit={submit}>
            <label>
              Username
              <input name="username" autoComplete="username" required />
            </label>
            <label>
              Password
              <input name="password" type="password" autoComplete="current-password" required />
            </label>
            <button type="submit" disabled={busy}>Sign in</button>
          </form>
          {state.message && <p role="alert">{state.message}</p>}
        </section>
      )}
      {state.kind === "signed-in" && (
        <section aria-labelledby="dashboard">
          <div className="toolbar">
            <h2 id="dashboard">Signed in as {state.session.username}</h2>
            <button type="button" onClick={onSignOut} disabled={busy}>Sign out</button>
          </div>
          {state.message && <p role="alert">{state.message}</p>}
          {state.session.bi ? (
            <div ref={mountRef} className="dashboard" aria-label="Payment operations dashboard" />
          ) : (
            <p role="alert">Your account does not have access to analytics dashboards.</p>
          )}
        </section>
      )}
    </main>
  );
}
