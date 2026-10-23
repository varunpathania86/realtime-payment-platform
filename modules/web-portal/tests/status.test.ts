import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadStatus } from "../src/status.js";
import { StatusPage } from "../src/StatusPage.js";
import type { StatusState } from "../src/status.js";

const payloads: Record<string, unknown> = {
  "/api/healthz": { status: "ok" },
  "/api/readyz": { status: "ok" },
  "/api/version": { version: "test-build", commit: "abc123" },
};

test("loads all same-origin endpoints with cache disabled and cancellation", async () => {
  const requests: string[] = [];
  const controller = new AbortController();
  const data = await loadStatus(async (path, init) => {
    requests.push(path);
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.signal, controller.signal);
    return Response.json(payloads[path]);
  }, controller.signal);
  assert.deepEqual(requests, Object.keys(payloads));
  assert.deepEqual(data, { health: "ok", ready: "ok", version: "test-build", commit: "abc123" });
});

test("rejects HTTP, network, JSON, readiness, and metadata failures", async (t) => {
  for (const [name, fetcher, expected] of [
    ["http", async () => new Response("Unavailable", { status: 503 }), /HTTP 503/],
    ["network", async () => { throw new Error("offline"); }, /offline/],
    ["json", async () => new Response("not json"), /JSON|Unexpected/],
    ["shape", async () => Response.json(null), /invalid response/],
    ["not ready", async () => Response.json({ status: "down" }), /not healthy and ready/],
    ["metadata", async (path: string) => Response.json(
      path === "/api/version" ? { version: 1, commit: "" } : payloads[path],
    ), /invalid build metadata/],
  ] as const) {
    await t.test(name, async () => {
      await assert.rejects(loadStatus(fetcher), expected);
    });
  }
});

test("renders explicit accessible loading, success and error states with build markers", () => {
  const states: StatusState[] = [
    { kind: "loading" },
    { kind: "success", data: { health: "ok", ready: "ok", version: "api-v1", commit: "api-sha" } },
    { kind: "error", message: "Network unavailable" },
  ];
  for (const state of states) {
    const html = renderToStaticMarkup(createElement(StatusPage, {
      state, onRetry: () => {}, uiVersion: "ui-v2", uiCommit: "ui-sha",
    }));
    assert.match(html, /<h1>Platform status<\/h1>/);
    assert.match(html, /ui-v2/);
    assert.match(html, /ui-sha/);
    assert.match(html, /Retry status check/);
    assert.match(html, /role="status"/);
    if (state.kind === "loading") {
      assert.match(html, /Loading API status/);
      assert.match(html, /disabled=""/);
      assert.match(html, /aria-busy="true"/);
    } else {
      assert.doesNotMatch(html, /disabled=""/);
    }
    if (state.kind === "success") {
      assert.match(html, /API is healthy and ready/);
      assert.match(html, /api-v1/);
      assert.match(html, /api-sha/);
    }
    if (state.kind === "error") {
      assert.match(html, /role="alert"/);
      assert.match(html, /Network unavailable/);
    }
  }
});

test("a failed request can be retried successfully", async () => {
  await assert.rejects(loadStatus(async () => { throw new Error("offline"); }));
  const data = await loadStatus(async (path) => Response.json(payloads[path]));
  assert.equal(data.health, "ok");
});
