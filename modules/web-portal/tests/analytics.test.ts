import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AnalyticsView } from "../src/AnalyticsView.js";
import type { AnalyticsState } from "../src/AnalyticsView.js";
import { fetchGuestToken, loadSession, signIn, signOut } from "../src/identity.js";

const viewer = {
  username: "ops-viewer",
  permissions: ["bi:view"],
  bi: { dashboardId: "bfa817e5-b670-5c83-842c-ef5d57da6f8a", supersetDomain: "http://bi.localhost:8080" },
};

test("session, sign-in, sign-out and token calls stay same-origin and uncached", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const fetcher = async (path: string, init?: RequestInit) => {
    calls.push([path, init]);
    if (path === "/bi/guest-token") return Response.json({ token: "guest-jwt" });
    if (path === "/auth/logout") return new Response(null, { status: 204 });
    return Response.json(viewer);
  };
  assert.deepEqual(await loadSession(fetcher), viewer);
  assert.deepEqual(await signIn("ops-viewer", "secret password", fetcher), viewer);
  await signOut(fetcher);
  assert.equal(await fetchGuestToken(fetcher), "guest-jwt");
  assert.deepEqual(calls.map(([path, init]) => [path, init?.method ?? "GET"]), [
    ["/auth/session", "GET"], ["/auth/login", "POST"], ["/auth/logout", "POST"], ["/bi/guest-token", "POST"],
  ]);
  for (const [, init] of calls) {
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "same-origin");
  }
  assert.deepEqual(JSON.parse(String(calls[1][1]?.body)), { username: "ops-viewer", password: "secret password" });
});

test("signed-out, denied and failing responses are explicit", async () => {
  assert.equal(await loadSession(async () => new Response("", { status: 401 })), null);
  assert.equal(await loadSession(async () => new Response("", { status: 404 })), null);
  await assert.rejects(loadSession(async () => new Response("", { status: 500 })), /HTTP 500/);
  await assert.rejects(loadSession(async () => Response.json({ username: 1 })), /invalid session/);
  await assert.rejects(
    loadSession(async () => Response.json({ ...viewer, bi: { dashboardId: 1 } })), /invalid dashboard settings/,
  );
  await assert.rejects(signIn("a", "b", async () => new Response("", { status: 401 })), /Invalid username or password/);
  await assert.rejects(fetchGuestToken(async () => new Response("", { status: 403 })), /HTTP 403/);
  await assert.rejects(fetchGuestToken(async () => Response.json({ token: "" })), /no token/);
});

test("renders sign-in, dashboard and no-access states without secrets", () => {
  const render = (state: AnalyticsState) => renderToStaticMarkup(createElement(AnalyticsView, {
    state, busy: false, onSignIn: () => {}, onSignOut: () => {},
  }));
  const signedOut = render({ kind: "signed-out", message: "Invalid username or password" });
  assert.match(signedOut, /<h2 id="sign-in">Sign in<\/h2>/);
  assert.match(signedOut, /type="password"/);
  assert.match(signedOut, /role="alert">Invalid username or password/);
  assert.match(signedOut, /Local development identity, not for production/);

  const dashboard = render({ kind: "signed-in", session: viewer });
  assert.match(dashboard, /Signed in as ops-viewer/);
  assert.match(dashboard, /class="dashboard"/);
  assert.doesNotMatch(dashboard, /does not have access/);

  const clerk = render({ kind: "signed-in", session: { username: "payments-clerk", permissions: [] } });
  assert.match(clerk, /does not have access to analytics dashboards/);
  assert.doesNotMatch(clerk, /class="dashboard"/);
  assert.match(render({ kind: "loading" }), /Checking your session/);
});
