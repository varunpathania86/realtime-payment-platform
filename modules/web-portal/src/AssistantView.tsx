import type { FormEvent } from "react";
import { maxQuestionLength } from "./assistant.js";
import type { AssistantAnswer } from "./assistant.js";

export type AssistantState =
  | { kind: "idle" }
  | { kind: "asking"; question: string }
  | { kind: "answered"; question: string; answer: AssistantAnswer }
  | { kind: "error"; question: string; message: string };

const statusLabels: Record<string, string> = {
  answered: "Answered",
  fallback: "Fallback — no verified model answer",
  denied: "Denied by your permissions",
  rejected: "Rejected as unsafe",
  unauthenticated: "Not authenticated",
};

interface Props {
  state: AssistantState;
  onAsk: (question: string) => void;
}

export function AssistantView({ state, onAsk }: Props) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = String(new FormData(event.currentTarget).get("question") ?? "").trim();
    if (question) onAsk(question);
  };
  const asking = state.kind === "asking";
  return (
    <section aria-labelledby="assistant" className="assistant">
      <h2 id="assistant">Operations assistant</h2>
      <p>
        Answers come from operations documents and read-only tools, cited by source. It never approves,
        declines or changes payments.
      </p>
      <form onSubmit={submit} className="question">
        <label>
          Question
          <textarea name="question" rows={3} maxLength={maxQuestionLength} required
            defaultValue={state.kind === "idle" ? "" : state.question} />
        </label>
        <button type="submit" disabled={asking}>{asking ? "Asking…" : "Ask"}</button>
      </form>
      {asking && <p role="status">Waiting for the assistant. Local CPU answers can take up to a minute.</p>}
      {state.kind === "error" && <p role="alert">{state.message}</p>}
      {state.kind === "answered" && (
        <article aria-label="Assistant answer">
          <p className={`badge ${state.answer.status}`}>{statusLabels[state.answer.status] ?? state.answer.status}</p>
          <p className="answer">{state.answer.answer}</p>
          {state.answer.citations.length > 0 && (
            <>
              <h3>Sources</h3>
              <ol>
                {state.answer.citations.map((citation) => (
                  <li key={citation.id}>
                    <code>{citation.documentId}</code> — {citation.heading}
                  </li>
                ))}
              </ol>
            </>
          )}
          {state.answer.tools.length > 0 && (
            <>
              <h3>Tools</h3>
              <ul>
                {state.answer.tools.map((tool, index) => (
                  <li key={`${tool.name}-${index}`}>
                    <code>{tool.name}</code>: {tool.status}
                  </li>
                ))}
              </ul>
            </>
          )}
          {state.answer.requestId && (
            <p className="meta">
              Request <code>{state.answer.requestId}</code>
              {state.answer.latencyMs !== undefined && ` · ${(state.answer.latencyMs / 1000).toFixed(1)} s`}
            </p>
          )}
        </article>
      )}
    </section>
  );
}
