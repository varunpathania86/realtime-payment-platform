import type { StatusState } from "./status.js";

interface Props {
  state: StatusState;
  onRetry: () => void;
  uiVersion: string;
  uiCommit: string;
}

export function StatusPage({ state, onRetry, uiVersion, uiCommit }: Props) {
  return (
    <main>
      <header>
        <p className="eyebrow">Realtime payment platform</p>
        <h1>Platform status</h1>
        <p>Stage 1 foundation · Synthetic data only</p>
      </header>
      <section aria-labelledby="api-status" aria-busy={state.kind === "loading"}>
        <h2 id="api-status">Payment API</h2>
        <div role="status" aria-live="polite">
          {state.kind === "loading" && <p>Loading API status…</p>}
          {state.kind === "success" && (
            <>
              <p className="healthy">API is healthy and ready.</p>
              <dl>
                <dt>Health</dt><dd>{state.data.health}</dd>
                <dt>Readiness</dt><dd>{state.data.ready}</dd>
                <dt>API version</dt><dd>{state.data.version}</dd>
                <dt>API commit</dt><dd><code>{state.data.commit}</code></dd>
              </dl>
            </>
          )}
        </div>
        {state.kind === "error" && (
          <div role="alert">
            <p>Unable to load API status.</p>
            <p>{state.message}</p>
          </div>
        )}
        <button type="button" onClick={onRetry} disabled={state.kind === "loading"}>
          Retry status check
        </button>
      </section>
      <footer>
        <h2>Portal build</h2>
        <p>UI version: <strong>{uiVersion}</strong></p>
        <p>UI commit: <code>{uiCommit}</code></p>
      </footer>
    </main>
  );
}
