# Module development and catalog types

This repository uses `solution.yaml` to list independently versioned modules. Each module lives at `modules/<module-name>/module.yaml`; the manifest's `name` must match the directory name.

For now, `type` is a human-readable primary classification. It is not a complete description of every aspect of a module and must not be used as a workflow switch. Workflows discover modules from the catalog and manifests, not from hard-coded module names or assumptions about `type`.

`module.yaml.version` records the current stable production version only. New modules start at `0.0.0`; workflow-generated iteration, branch, revision, and RC versions are not written there. The integrated solution has its own stable version in `solution.yaml`.

## Current types

| Type | Meaning | Example |
|---|---|---|
| `api` | Backend application whose primary interface is an HTTP API. | `payment-api`, `ai-gateway` |
| `web` | User-facing browser application. | `web-portal` |
| `analytics` | A module whose primary purpose is analytical storage, query, or BI capability. | `clickhouse-analytics`, `iceberg-lakehouse`, `trino-analytics`, `superset-bi` |
| `worker` | A background executable that performs work without serving the primary user-facing request path. It may consume messages or perform asynchronous tasks. | `event-generator`, `ai-orchestrator`, `llm-enrichment-worker` |
| `stream-processing` | A specialized worker for continuous event streams, with processing concerns such as event time, state, windows, checkpoints, and recovery. | `flink-jobs` |
| `library` | Reusable code imported or otherwise consumed by other modules; not deployed as a standalone service. | `ai-core` |
| `contracts` | Shared interface definitions used to communicate across module boundaries, such as Avro event schemas or OpenAPI specifications. | `event-contracts` |
| `model` | The payment-risk machine-learning model, including its training/evaluation and inference artifacts. It is not the conversational LLM. | `payment-risk-model` |
| `knowledge` | Versioned documents and prepared content used by the LLM assistant's retrieval and context features. | `operations-knowledge` |

## Clarifying the overlapping terms

`worker` and `stream-processing` describe runtime roles. Stream processing is a specialized form of background work, so every stream-processing module is conceptually a worker; `worker` is used for general background executables, while `stream-processing` calls out Flink's stateful, continuous-processing lifecycle.

The other values describe different primary responsibilities or deliverables:

- A **library** is reusable executable code; **contracts** are shared interface definitions. Neither is a deployed process.
- A **model** is a predictive ML artifact and its lifecycle. The payment-risk model scores synthetic payment features and is exported for Flink inference.
- **Knowledge** is the content supplied to the conversational LLM through retrieval or prepared context. It means documents, metadata, chunks, embeddings, and bundles—not information encoded in the LLM's weights.
- An **analytics** module describes a domain capability and may itself be a service, application, or data artifact. For example, Superset is a web application, while Iceberg is a table format. The current type records each module's primary catalog role, not every technical characteristic.

These types are intentionally descriptive at this stage. They are not guaranteed to be mutually exclusive in a deeper architecture taxonomy. Future schema or workflow requirements should add separate fields for distinct dimensions (for example, delivery kind versus runtime) rather than infer behavior from increasingly overloaded `type` values.

## Release version forms

For a target version such as `0.2.2`, module release automation uses these forms:

| Form | Example | Purpose |
|---|---|---|
| Iteration branch artifact | `0.2.2-it1-br.0ac2` | Latest artifact for a branch; the short branch hash is derived from the normalized branch name and may be retagged as commits change |
| Integrated main revision | `0.2.2-it1-rv.1` | Immutable revision built from an integrated source commit |
| Approved iteration baseline | `0.2.2-it1` | Immutable baseline promoted from a verified integrated revision |
| Release candidate | `0.2.2-rc1` | Immutable candidate promoted from an approved iteration or RC revision |
| RC-fix branch artifact | `0.2.2-rc1-br.c84d` | Branch artifact on the matching module-specific RC maintenance line |
| Optional RC revision | `0.2.2-rc1-rv.1` | Integrated revision during RC maintenance |
| Stable production | `0.2.2` | Production version recorded in `module.yaml.version` after promotion |

The `rv` counter is scoped per module, target version, iteration, and release line. A rerun for the same source commit reuses its reservation. The integrated solution has independent iteration, RC, and stable versions, but does not use module `br` or `rv` versions. Its tags are `solution/v<version>`; module tags are namespaced as `modules/<module-name>/v<version>`.

Impact is approved per module. `patch`, `minor`, and `major` are explicit values; `none` defaults to `patch` for now. Do not infer impact or lifecycle from branch names or commit messages. Promotions retag the already-built OCI artifact and verify its digest; they do not rebuild it. After production promotion, an automated PR updates the stable version field in the module or solution catalog.

The exact schemas, counter reservation, promotion, and cleanup behavior are introduced in later implementation phases. See [the master plan](master-plan.md) for the complete policy.

## Module boundaries

- Keep module names in `solution.yaml` and each module's `module.yaml`; do not hard-code names in workflow logic.
- Declare a dependency when a module consumes another module's public contract or artifact. Avoid dependencies that merely reflect deployment order.
- Infrastructure that deploys or connects modules is solution-level configuration unless it has a separately owned, independently versioned product boundary.
- A library, contract, model, or knowledge module does not need a Docker image or Helm chart just because deployable application modules do. Publish only the artifact formats that the module actually produces.
- A knowledge-ingestion executable is a runtime module; the documents and prepared knowledge it processes are the knowledge module. Keep those responsibilities distinct even if they initially share source files.
- The risk model is independent from the LLM assistant: the deterministic predictive score is produced by the ML model; the LLM may provide asynchronous explanations but does not approve, reject, or numerically score payments.

## Adding a module

1. Add its kebab-case name to `solution.yaml`.
2. Create `modules/<name>/module.yaml` with matching `name`, stable production `version`, owners, dependencies, and the best-fit primary `type`.
3. Add only dependencies that represent actual consumption.
4. Add runtime packaging (for example, Dockerfile or Helm chart) only when appropriate to the module.
5. Update the architecture and module documentation if the boundary or responsibility changes.
