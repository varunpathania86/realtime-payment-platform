import { embedDashboard } from "@superset-ui/embedded-sdk";
import { useEffect, useRef, useState } from "react";
import { askAssistant } from "./assistant.js";
import { AnalyticsView } from "./AnalyticsView.js";
import { AssistantView } from "./AssistantView.js";
import type { AssistantState } from "./AssistantView.js";
import type { AnalyticsState } from "./AnalyticsView.js";
import { fetchGuestToken, loadSession, signIn, signOut } from "./identity.js";

const failure = (error: unknown) => (error instanceof Error ? error.message : "Unexpected failure");

function AssistantPanel() {
  const [state, setState] = useState<AssistantState>({ kind: "idle" });
  const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => controllerRef.current?.abort(), []);
  const ask = (question: string) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "asking", question });
    askAssistant(question, fetch, controller.signal).then(
      (answer) => setState({ kind: "answered", question, answer }),
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ kind: "error", question, message: failure(error) });
      },
    );
  };
  return <AssistantView state={state} onAsk={ask} />;
}

export function AnalyticsPage() {
  const [state, setState] = useState<AnalyticsState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [oidc, setOIDC] = useState(false);
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/auth/config", { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.ok) setOIDC(((await response.json()) as { oidc?: boolean }).oidc === true);
      }).catch(() => {});
    loadSession(fetch, controller.signal).then(
      (session) => setState(session ? { kind: "signed-in", session } : { kind: "signed-out" }),
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ kind: "signed-out", message: failure(error) });
      },
    );
    return () => controller.abort();
  }, []);

  const access = state.kind === "signed-in" ? state.session.bi : undefined;
  useEffect(() => {
    const mountPoint = mountRef.current;
    if (!access || !mountPoint) return;
    let active = true;
    let unmount: (() => void) | undefined;
    // The SDK calls fetchGuestToken again before each short-lived token expires.
    embedDashboard({
      id: access.dashboardId,
      supersetDomain: access.supersetDomain,
      mountPoint,
      fetchGuestToken: () => fetchGuestToken(),
      dashboardUiConfig: { hideTitle: true, filters: { expanded: false } },
      iframeTitle: "Payment operations dashboard",
    }).then(
      (dashboard) => {
        if (active) unmount = dashboard.unmount;
        else dashboard.unmount();
      },
      (error: unknown) => {
        if (active) setState((current) => (current.kind === "signed-in" ? { ...current, message: failure(error) } : current));
      },
    );
    return () => {
      active = false;
      unmount?.();
      mountPoint.replaceChildren();
    };
  }, [access?.dashboardId, access?.supersetDomain]);

  const run = (action: () => Promise<AnalyticsState>) => {
    setBusy(true);
    action().then(setState, (error: unknown) => setState({ kind: "signed-out", message: failure(error) }))
      .finally(() => setBusy(false));
  };

  return (
    <AnalyticsView
      state={state}
      busy={busy}
      oidc={oidc}
      mountRef={mountRef}
      assistant={<AssistantPanel />}
      onSignIn={(username, password) => run(async () => ({ kind: "signed-in", session: await signIn(username, password) }))}
      onSignOut={() => run(async () => {
        await signOut();
        return { kind: "signed-out" };
      })}
    />
  );
}
