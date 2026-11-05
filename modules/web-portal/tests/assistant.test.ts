import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { askAssistant } from "../src/assistant.js";
import { AnalyticsView } from "../src/AnalyticsView.js";
import { AssistantView } from "../src/AssistantView.js";
import type { AssistantState } from "../src/AssistantView.js";

const reply = {
  status: "answered",
  answer: "Restart from the latest checkpoint [S1].",
  intent: "rag",
  citations: [{ id: "S1", document_id: "docs/runbooks/flink-recovery.md", heading: "Recovery", source_uri: "x" }],
  retrieved: ["docs/runbooks/flink-recovery.md"],
  tools: [{ name: "search_knowledge", status: "ok" }],
  request_id: "4f1c2b",
  latency_ms: 4200,
};

test("asks the same-origin BFF without caching and maps the response", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const answer = await askAssistant("How do I recover?", async (path, init) => {
    calls.push([path, init]);
    return Response.json(reply);
  });
  assert.equal(calls[0][0], "/assistant/ask");
  assert.equal(calls[0][1]?.method, "POST");
  assert.equal(calls[0][1]?.cache, "no-store");
  assert.equal(calls[0][1]?.credentials, "same-origin");
  assert.deepEqual(JSON.parse(String(calls[0][1]?.body)), { question: "How do I recover?" });
  assert.deepEqual(answer, {
    status: "answered",
    answer: reply.answer,
    citations: [{ id: "S1", documentId: "docs/runbooks/flink-recovery.md", heading: "Recovery" }],
    tools: [{ name: "search_knowledge", status: "ok" }],
    requestId: "4f1c2b",
    latencyMs: 4200,
  });
});

test("failures become readable messages", async () => {
  const failing = (status: number) => askAssistant("q", async () => new Response("detail", { status }));
  await assert.rejects(failing(403), /does not have access/);
  await assert.rejects(failing(429), /busy/);
  await assert.rejects(failing(504), /did not answer in time/);
  await assert.rejects(failing(502), /HTTP 502/);
  await assert.rejects(askAssistant("q", async () => Response.json({ status: 1 })), /invalid response/);
  const sparse = await askAssistant("q", async () => Response.json({ status: "denied", answer: "No.", citations: "x" }));
  assert.deepEqual(sparse.citations, []);
  assert.deepEqual(sparse.tools, []);
});

test("renders answers, citations, tool statuses and errors as text", () => {
  const render = (state: AssistantState) => renderToStaticMarkup(createElement(AssistantView, { state, onAsk: () => {} }));
  assert.match(render({ kind: "idle" }), /<button type="submit">Ask<\/button>/);
  assert.match(render({ kind: "asking", question: "q" }), /disabled="">Asking…/);

  const answered = render({
    kind: "answered",
    question: "How do I recover?",
    answer: {
      status: "answered", answer: "<img src=x onerror=alert(1)>", citations: [
        { id: "S1", documentId: "docs/runbooks/flink-recovery.md", heading: "Recovery" },
      ], tools: [{ name: "get_payment", status: "denied" }], requestId: "4f1c2b", latencyMs: 4200,
    },
  });
  assert.match(answered, /class="badge answered">Answered/);
  assert.match(answered, /<code>docs\/runbooks\/flink-recovery.md<\/code> — Recovery/);
  assert.match(answered, /<code>get_payment<\/code>: denied/);
  assert.match(answered, /4\.2 s/);
  // Model output is rendered as text, never as markup.
  assert.doesNotMatch(answered, /<img/);
  assert.match(answered, /&lt;img/);

  const fallback = render({
    kind: "answered", question: "q",
    answer: { status: "fallback", answer: "A verified model answer is not available.", citations: [], tools: [] },
  });
  assert.match(fallback, /Fallback — no verified model answer/);
  assert.doesNotMatch(fallback, /<h3>Sources/);
  assert.match(render({ kind: "error", question: "q", message: "The assistant is busy." }), /role="alert">The assistant is busy/);
});

test("the operations page shows the assistant only when the session allows it", () => {
  const page = (assistant: boolean) => renderToStaticMarkup(createElement(AnalyticsView, {
    state: { kind: "signed-in", session: { username: "ops-viewer", permissions: [], assistant } },
    busy: false, onSignIn: () => {}, onSignOut: () => {},
    assistant: createElement("div", { id: "assistant-panel" }),
  }));
  assert.match(page(true), /id="assistant-panel"/);
  assert.doesNotMatch(page(false), /id="assistant-panel"/);
});
