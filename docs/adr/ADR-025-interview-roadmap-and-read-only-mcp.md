# ADR-025: Interview-focused roadmap order and read-only MCP tools

- **Status:** Accepted
- **Date:** 2026-11-04
- **Deciders:** varunpathania86

## Context

Stages 0-7 are merged. The maintainer's goal is interview readiness, with the
Iceberg lakehouse, Superset embedded in React and a LangChain/LangGraph LLM
assistant demonstrable as early as possible. The original roadmap placed
Keycloak (Stage 10) before AI and spread the assistant across four stages
(RAG, CAG/LLM serving, LangChain/LangGraph and evaluation), so the first
end-to-end assistant demo would only exist after Stage 13.

The maintainer also wants the Model Context Protocol (MCP), which the frozen
technology list did not include. The developer laptop has about 15.5 GiB RAM,
so Trino, Superset and a local LLM cannot run beside the full streaming stack.

## Decision

Keep one roadmap stage per pull request and keep Stages 8, 9 and 14-18
numbered as before. Reorder Stages 10-13:

| Stage | New scope | Previously |
|---|---|---|
| 10 | AI operations assistant: pgvector RAG with LangChain, LangGraph routing, read-only MCP tools, llama.cpp serving and a starter evaluation set | Parts of 11, 12, 13 and 15 |
| 11 | Keycloak identity and authorization, replacing local development identity in Superset embedding and AI | 10 |
| 12 | CAG bundles and Go AI Gateway hardening | Remainder of 12 |
| 13 | Durable LangGraph workflows and human approval | Remainder of 13 |

Until Stage 11, Superset guest-token issuance and AI tool authorization use an
explicit **local development identity**: configured synthetic users mapped to
the planned application permissions and enforced in deterministic Go or Python
code. It is labelled non-production and is replaced, not extended, by Keycloak.

Add MCP as the boundary between the LangGraph agent and platform data tools.
The platform hosts its own MCP server; tools are read-only, allowlisted,
bounded by timeouts and row limits, and authorize the caller's permissions
themselves rather than trusting the model. The MCP server is cluster-internal,
not exposed through ingress, and holds no write credentials. Third-party MCP
servers, write tools and model-chosen arbitrary SQL are out of scope.

Each stage ships a lean Minikube profile that disables components it does not
need, and records measured memory.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Keep the original order | No roadmap churn; real identity before AI | First assistant demo only after Stage 13 |
| One combined AI pull request (old 11-15) | Single milestone | Very large review; mixes RAG, CAG, async Kafka and evaluation risks |
| Renumber every later stage contiguously | Clean numbering | Breaks stage references in accepted ADRs, runbooks and release docs |
| Tools called directly from LangGraph without MCP | Fewer moving parts | Tool contracts tied to one framework; misses the requested MCP capability |
| Expose MCP tools to external clients now | Usable from desktop AI clients | Needs real identity and network hardening that arrive later |

## Consequences

- Iceberg/Trino (Stage 8), Superset in React (Stage 9) and the AI assistant
  (Stage 10) become three consecutive pull requests.
- Superset and AI demos before Stage 11 must state that identity is local and
  synthetic; Stage 11 adds Keycloak token validation and fail-closed tests to
  both paths.
- CAG, asynchronous pipeline LLM enrichment and the full evaluation stage
  remain separate later stages; Stage 10 includes only a starter evaluation set
  and authorization-negative and prompt-injection cases.
- MCP joins the frozen technology list; the assistant still never authorizes,
  approves or blocks payments and never performs numeric decisions.
- Full-stack demos stay resource-limited; acceptance uses dedicated
  single-cluster profiles.
