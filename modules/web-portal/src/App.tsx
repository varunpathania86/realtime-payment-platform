import { useEffect, useState } from "react";
import { AnalyticsPage } from "./AnalyticsPage.js";
import { StatusPage } from "./StatusPage.js";
import { loadStatus } from "./status.js";
import type { StatusState } from "./status.js";

function StatusApp() {
  const [state, setState] = useState<StatusState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => {
      controller.abort();
      if (active) {
        active = false;
        setState({ kind: "error", message: "Status request timed out. Please retry." });
      }
    }, 10000);
    setState({ kind: "loading" });
    loadStatus(fetch, controller.signal).then(
      (data) => {
        if (active) setState({ kind: "success", data });
      },
      (error: unknown) => {
        if (active) {
          setState({
            kind: "error",
            message: error instanceof Error ? error.message : "Unexpected status request failure",
          });
        }
      },
    ).finally(() => {
      window.clearTimeout(timeout);
      controller.abort();
    });
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  return (
    <StatusPage
      state={state}
      onRetry={() => setAttempt((value) => value + 1)}
      uiVersion={import.meta.env.VITE_APP_VERSION || "dev"}
      uiCommit={import.meta.env.VITE_APP_COMMIT || "unknown"}
    />
  );
}

export function App() {
  const analytics = window.location.pathname === "/analytics";
  return (
    <>
      <nav aria-label="Portal" className={analytics ? "wide" : undefined}>
        <a href="/" aria-current={analytics ? undefined : "page"}>Status</a>
        <a href="/analytics" aria-current={analytics ? "page" : undefined}>Analytics</a>
      </nav>
      {analytics ? <AnalyticsPage /> : <StatusApp />}
    </>
  );
}
