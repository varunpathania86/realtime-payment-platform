export interface AssistantCitation {
  id: string;
  documentId: string;
  heading: string;
}

export interface AssistantTool {
  name: string;
  status: string;
}

export interface AssistantAnswer {
  status: string;
  answer: string;
  citations: AssistantCitation[];
  tools: AssistantTool[];
  requestId?: string;
  latencyMs?: number;
}

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export const maxQuestionLength = 1000;

const failures: Record<number, string> = {
  401: "Your session has expired. Sign in again.",
  403: "Your account does not have access to the assistant.",
  404: "The assistant is not enabled in this environment.",
  429: "The assistant is busy. Retry in a few seconds.",
  504: "The assistant did not answer in time.",
};

const text = (value: unknown) => (typeof value === "string" ? value : "");

function parseAnswer(body: unknown): AssistantAnswer {
  const value = body as Record<string, unknown> | null;
  if (typeof value !== "object" || value === null || typeof value.status !== "string" || typeof value.answer !== "string") {
    throw new Error("The assistant returned an invalid response");
  }
  const list = (items: unknown) => (Array.isArray(items) ? items : []).filter(
    (item): item is Record<string, unknown> => typeof item === "object" && item !== null,
  );
  return {
    status: value.status,
    answer: value.answer,
    citations: list(value.citations).map((item) => ({
      id: text(item.id), documentId: text(item.document_id), heading: text(item.heading),
    })),
    tools: list(value.tools).map((item) => ({ name: text(item.name), status: text(item.status) })),
    requestId: text(value.request_id) || undefined,
    latencyMs: typeof value.latency_ms === "number" ? value.latency_ms : undefined,
  };
}

export async function askAssistant(question: string, fetcher: Fetcher = fetch, signal?: AbortSignal): Promise<AssistantAnswer> {
  const response = await fetcher("/assistant/ask", {
    cache: "no-store",
    credentials: "same-origin",
    method: "POST",
    signal,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new Error(failures[response.status] ?? `The assistant returned HTTP ${response.status}`);
  return parseAnswer(await response.json());
}
