# SDD: Implemented components and execution flows

- **Status:** Implemented-source reference, not a live-cluster inventory
- **Version:** v0.1.0
- **Owner:** Platform maintainers
- **Reviewed:** 2026-11-15

## 1. Purpose and scope

Explain what the repository actually implements, including the components
inside each major system and the direction of data and control flow.
The [master plan](../master-plan.md) contains the larger **target** architecture;
this document deliberately excludes unimplemented target-state connections.

Read the diagrams in order:

Start with the [high-level streaming data flow](#31-high-level-streaming-data-flow)
for data origins, processing, predictive inference and storage only.

1. [Overall application flow](#3-context-diagram).
2. [Payments and Kafka](#41-payments-postgresql-and-kafka).
3. [Flink and predictive inference](#42-flink-execution-and-predictive-inference).
4. [Lakehouse and BI](#43-lakehouse-and-bi).
5. [Identity and browser requests](#44-identity-and-browser-requests).
6. [AI ingestion, retrieval and GPU](#45-ai-knowledge-ingestion-and-interactive-execution).
7. [Observability](#8-observability).
8. [Deployment and topology](#10-deployment-and-operations).

**Arrow convention:** solid arrows show application data, requests or returned
results, as labelled. Dotted arrows show scheduling, configuration, identity,
artifact delivery or other control-plane interactions. A request arrow to a
database means the caller sends a query; results return to that caller.
`OPTIONAL` means implemented but enabled only by configuration, not missing.
Diagrams show available capabilities together, not a promise that every
topology enables them simultaneously.

## 2. Requirements

### 2.1 Functional

These are the implemented boundaries, not one serial processing chain:

| Path | Actual order | Important distinction |
|---|---|---|
| Payment creation | Browser/client -> Go API -> PostgreSQL transaction -> outbox relay -> Kafka -> receipt consumer -> PostgreSQL | Payment API does not consume processing events to authorize payments |
| Streaming analytics | Authenticated synthetic requests -> API/outbox -> relay simulation -> Kafka processing topic -> Flink | The processing event references the API-created payment |
| Advisory prediction | Same processing topic -> Flink account/currency history -> ONNX Runtime -> Kafka audit topics and optional ClickHouse | Inference is inside TaskManager Python workers, not an HTTP prediction service |
| Historical lakehouse | Same processing topic -> separate Flink SQL job -> Iceberg files in MinIO and snapshot commits through Polaris | Does not consume risk predictions or window results |
| BI | Browser dashboard -> Superset -> ClickHouse and optional Trino -> Polaris/MinIO | Read-only analytical queries, not payment decisions |
| Interactive AI | Browser -> portal BFF -> orchestrator -> MCP tools/chat server -> verified answer | Never in the payment creation or predictive scoring path |
| Knowledge ingestion | Repository corpus -> batch ingestion Job -> embedding server -> pgvector; originals -> MinIO | A triggered batch Job, not an always-running ingestion service |

### 2.2 Non-functional (performance, security, availability, cost)

- Synthetic data only; integer minor-unit money amounts in payment contracts.
- Idempotent payment creation, durable outbox publication, replay-safe receipts.
- Checkpointed streaming state with explicitly described external sink semantics.
- Authorization and numeric payment decisions never delegated to an LLM.
- Optional AI, BI, observability and GPU features retain their own resource costs.
- Local single-cluster operation is not highly available or capacity-certified.

## 3. Context diagram

### 3.1 High-level streaming data flow

This map excludes UI, BI and interactive AI. Synthetic fixture/load clients
authenticate using Keycloak client credentials and call the payment API.
The relay publishes an initiation and a simulated processing event for the
same payment; no live fixture/load client publishes directly to Kafka.
Optional destinations and branches require their corresponding feature flags.

```mermaid
flowchart TB
    subgraph Initiation["Payment initiation pipeline"]
        Requests["Authenticated payment requests / synthetic API fixtures"]
        API["Payment API"]
        Payments[("PostgreSQL: payments and transactional outbox")]
        Relay["Outbox relay"]
        Simulation["Deterministic simulation inside relay"]
        Initiated[("Kafka: payments.initiated.v1")]
        Consumer["Receipt consumer"]
        Receipts[("Same payment PostgreSQL: receipts and quarantine")]
        Requests --> API
        API -->|"atomic commit"| Payments
        Payments -->|"pending outbox events"| Relay
        Relay -->|"publish Avro events"| Initiated
        Relay -->|"same persisted payment, after initiation acknowledgement"| Simulation
        Initiated --> Consumer
        Consumer -->|"durable handling"| Receipts
    end

    subgraph Streaming["Synthetic real-time processing pipeline"]
        Processing[("Kafka: payment-processing-events-v1")]
        subgraph PaymentJob["Flink payment-event-time-v1"]
            Windows["Validate / deduplicate / event-time windows"]
            Features["OPTIONAL account-currency history / risk features"]
            Predict["Predictive model: ONNX inference inside Flink"]
            Features --> Predict
        end
        LakeJob["OPTIONAL independent Flink SQL job: raw capture / validate / deduplicate"]
        CH[("OPTIONAL ClickHouse: facts / windows / predictions / late audit")]
        Outputs[("Kafka: window results / prediction revisions / too-late events")]
        Simulation -->|"JSON simulated processing event"| Processing
        Processing --> Windows
        Processing --> Features
        Processing -->|"independent consumer"| LakeJob
        Windows -->|"facts / window revisions / late rows"| CH
        Windows -->|"window results / too-late events"| Outputs
        Predict -->|"advisory scores / late audit"| CH
        Predict -->|"prediction revisions / too-late events"| Outputs
    end

    subgraph Lakehouse["OPTIONAL Iceberg lakehouse"]
        Iceberg[("MinIO: Iceberg raw and curated tables / Parquet files")]
        Catalog["Polaris: snapshot / table catalog"]
        CatalogDB[("PostgreSQL: Polaris catalog state")]
        Catalog --> CatalogDB
    end
    LakeJob -->|"write data and metadata files"| Iceberg
    LakeJob -->|"commit snapshots on checkpoints"| Catalog

    Model[("MinIO model-registry: published ONNX package")]
    State[("MinIO flink-state: checkpoints / savepoints")]
    Model -. "verified artifact loaded at job startup" .-> Predict
    PaymentJob -. "persist checkpointed state" .-> State
    LakeJob -. "persist checkpointed state" .-> State
```

**How to read it:**

- PostgreSQL receives initiated payments and receipts. Flink does not currently
  write processed events or predictive scores back into the payment database.
- Flink consumes processing events derived from the API-created payment. Its window and risk branches
  run in parallel; risk features are not derived from completed window results.
- The predictive model is a trained, published ONNX artifact loaded into a
  Flink Python operator. It produces advisory scores, not payment approvals.
- ClickHouse receives streaming facts, aggregate revisions, prediction
  revisions and late-event audit rows when analytics is enabled.
- The separate Flink SQL job writes raw/curated **processing events** into
  Iceberg on MinIO. Predictions are not currently written into Iceberg.
- Polaris stores catalog state in PostgreSQL; table files live in MinIO.
  Dotted arrows are artifact/state operations, not additional event streams.
- The relay's simulation is a Go function, not a new service or a Kafka
  initiation-topic consumer. It acknowledges both publications before marking
  the outbox row published. Partial publication can replay either event.

Sources: [payment pipeline](../../modules/payment-api/README.md),
[Flink processing and risk branches](../../modules/stream-processor/job.py),
[lakehouse job](../../modules/stream-processor/lakehouse.py),
[model lifecycle](../../modules/payment-risk-model/README.md).

### 3.2 Overall application flow

This is the application-level map. Sections below expand the boxes.

```mermaid
flowchart TB
    User["Browser / direct authenticated API client"]
    Portal["React portal + Go BFF"]
    KC["OPTIONAL Keycloak"]
    API["Go payment API"]
    PG[("Payment PostgreSQL: payments + outbox + receipts")]
    Relay["Outbox relay"]
    KI[("Kafka: payments.initiated.v1")]
    Receipt["Receipt consumer"]
    Fixture["Triggered authenticated synthetic API requests"]
    KP[("Kafka: payment-processing-events-v1")]
    Flink["Flink payment-event-time-v1"]
    Risk["OPTIONAL ONNX inference inside Flink"]
    Results[("Kafka: window results / late events / risk audit")]
    CH[("OPTIONAL ClickHouse")]
    Lake["OPTIONAL separate Flink payment-lakehouse-v1"]
    Polaris["Polaris Iceberg REST catalog"]
    Object[("MinIO: Iceberg files + Flink state + model artifacts")]
    Trino["Trino coordinator and worker"]
    BI["OPTIONAL Superset"]
    AI["OPTIONAL AI orchestrator"]
    MCP["Read-only MCP assistant-tools"]
    LLM["llama.cpp chat and embedding servers"]
    KPG[("Knowledge PostgreSQL + pgvector")]
    Ingest["Triggered knowledge ingestion Job"]

    User --> Portal
    User --> API
    Portal -. "code flow / audience token exchange" .-> KC
    Portal -->|"payment requests"| API
    API -->|"atomic transaction"| PG
    PG -->|"pending outbox rows"| Relay
    Relay -->|"Avro initiation events"| KI
    KI --> Receipt
    Receipt -->|"deduplicated receipts"| PG
    Fixture -->|"Keycloak service bearer token"| API
    Relay -->|"simulated JSON processing events for same payment"| KP
    KP --> Flink
    Flink --> Results
    Flink -->|"facts / aggregate revisions / late rows"| CH
    Flink --> Risk
    Risk -->|"prediction revisions"| Results
    Risk -->|"prediction revisions"| CH
    KP --> Lake
    Lake -->|"Parquet / Iceberg metadata files"| Object
    Lake -->|"snapshot commits"| Polaris
    Flink -. "checkpoints / savepoints" .-> Object
    Object -. "pinned model ZIP at startup" .-> Flink
    Portal -->|"guest-token request"| BI
    User -->|"embedded dashboard queries"| BI
    BI --> CH
    BI --> Trino
    Trino -->|"metadata requests"| Polaris
    Trino -->|"read files directly"| Object
    Portal -->|"assistant question"| AI
    AI -->|"allowlisted tool calls"| MCP
    AI -->|"classification / generation"| LLM
    MCP --> KPG
    MCP -->|"query embeddings"| LLM
    MCP --> CH
    MCP --> Trino
    MCP -->|"payment lookup"| API
    Ingest -->|"document embeddings"| LLM
    Ingest -->|"versioned chunks and vectors"| KPG
    Ingest -->|"original documents"| Object
```

Apicurio, authentication subcomponents, storage details and observability are
expanded below rather than making this overview unreadable.

## 4. Architecture

### 4.1 Payments, PostgreSQL and Kafka

The API, relay and receipt consumer are **three process modes of the same
payment-api image**, deployed separately. They are not Kafka, Flink or model
subcomponents.

```mermaid
flowchart LR
    Client["Portal BFF / API client"]
    subgraph Payment["payment-api module"]
        Migrate["Migration init container"]
        HTTP["HTTP process: validation + idempotent create/read"]
        Relay["Relay process: ordered outbox polling / retry"]
        Consumer["Consumer process: Avro decode / receipt handling"]
    end
    subgraph DB["PostgreSQL payments database"]
        Payments[("payments + idempotency")]
        Outbox[("payment_outbox: pending / published / dead")]
        Receipts[("payment_event_receipts")]
        Dead[("payment_event_dead_letters")]
    end
    subgraph Kafka["Apache Kafka"]
        Controller["KRaft controller quorum: metadata / leader coordination"]
        Broker["Broker: partition leaders and followers"]
        Topic[("payments.initiated.v1 partitions")]
        Offsets[("Consumer-group offsets: payment-receipts-v1")]
    end
    Registry["Apicurio Registry: Avro schema IDs / compatibility"]
    Schema["event-contracts: canonical schema bundle"]
    RegistryDB[("Apicurio PostgreSQL storage")]

    Migrate -. "schema / trigger migrations" .-> DB
    Client --> HTTP
    HTTP -->|"one transaction"| Payments
    Payments -->|"database trigger in same transaction"| Outbox
    Outbox -->|"poll rows; preserve per-account order"| Relay
    Relay -->|"register / resolve schema"| Registry
    Schema -. "schema file" .-> Relay
    Relay -->|"account-keyed Avro frame; wait for acknowledgement"| Broker
    Broker --> Topic
    Relay -->|"mark published after acknowledgement"| Outbox
    Relay -->|"retry count / exhausted row marked dead"| Outbox
    Controller -. "metadata and partition leadership" .-> Broker
    Topic --> Consumer
    Consumer -->|"resolve schema ID"| Registry
    Consumer -->|"event-ID-deduplicated effect"| Receipts
    Consumer -->|"invalid record quarantine"| Dead
    Consumer -. "commit after durable handling" .-> Offsets
    Registry --> RegistryDB
```

- Local `rpp` has one Kafka process with `broker,controller` roles. It is not
  three brokers just because a topic has three partitions.
- The multi-cluster topology provides brokers and a KRaft quorum across
  `rpp-1/2/3`; partition replicas communicate across their shared Docker network.
- KRaft replaces ZooKeeper; no ZooKeeper workload is deployed.
- API response success means the PostgreSQL transaction succeeded. Relay
  publication happens asynchronously afterward. A crash between broker
  acknowledgement and marking the outbox published can replay an event.
- The receipt consumer writes receipt records; it does not call Flink, generate
  processing outcomes, settle a payment, or invoke a risk model.
- The relay now simulates processing using required request dimensions and
  publishes `payment-processing-events-v1` after the initiation acknowledgement.
  Both sends precede the outbox SQL commit; deterministic output identity
  makes replay logically deduplicable. This is not an atomic Kafka/SQL transaction.
- Apicurio provides schema identity/compatibility, not message transport.

Evidence: [payment API](../../modules/payment-api/README.md),
[database triggers](../../modules/payment-api/internal/postgres/migrations/004_outbox_write_guard.sql),
[event contracts](../../modules/event-contracts/README.md),
[single-cluster Kafka configuration](../../platform/terraform/environments/local/events.tf).

### 4.2 Flink execution and predictive inference

One session cluster runs a JobManager Deployment and a TaskManager Deployment.
The normal enabled chart runs one pod per role. Two jobs can share its task
slots: `payment-event-time-v1` and optional `payment-lakehouse-v1`.

```mermaid
flowchart TB
    Producer["Authenticated API fixtures / streaming load"]
    OriginAPI["Payment API / PostgreSQL outbox / relay simulation"]
    Input[("Kafka payment-processing-events-v1")]
    ModelStore[("MinIO model-registry: version + SHA-256")]
    StateStore[("MinIO flink-state: checkpoints / savepoints")]
    subgraph Flink["stream-processor session cluster"]
        Submit["Post-install/upgrade submission Job: runtime.py"]
        subgraph JM["JobManager process"]
            REST["REST submission/status :8081"]
            Schedule["Job coordination / task scheduling :6123"]
            Blob["Blob artifact delivery :6124"]
            Checkpoint["Checkpoint coordination"]
        end
        subgraph TM["TaskManager process: task slots / RPC :6122"]
            Source["Kafka source / partition watermarks / idleness"]
            Windows["Python worker: event validation / dedup / windows / timers"]
            History["OPTIONAL Python worker: account-currency history"]
            ONNX["Feature extraction + one ONNX session per risk operator"]
            Sinks["Kafka and ClickHouse sinks"]
            SQL["OPTIONAL separate Java/SQL Iceberg job"]
        end
    end
    KOut[("Kafka window results + too-late + risk topics")]
    CH[("ClickHouse facts / windows / late events / predictions")]
    Iceberg["Polaris + MinIO lakehouse"]

    Producer --> OriginAPI --> Input
    ModelStore -. "download and verify ZIP before submission" .-> Submit
    Submit -. "submit job graph; poll first completed checkpoint" .-> REST
    REST -.-> Schedule
    Submit -. "Python dependencies / verified model package" .-> Blob
    Schedule -. "deploy tasks" .-> TM
    TM -. "register / heartbeat / report status" .-> Schedule
    Blob -. "worker artifact delivery" .-> TM
    Checkpoint -. "trigger barriers / coordinate completion" .-> TM
    TM -. "state snapshots and acknowledgements" .-> Checkpoint
    TM -. "persist state" .-> StateStore
    Checkpoint -. "checkpoint metadata / recovery" .-> StateStore
    Input --> Source
    Source --> Windows
    Source --> History
    Windows -->|"facts and aggregate revisions"| Sinks
    History -->|"prior-history features"| ONNX
    ONNX -->|"advisory scores and revisions"| Sinks
    Sinks --> KOut
    Sinks --> CH
    Input -->|"independent source and consumer group"| SQL
    SQL -->|"files and checkpoint-driven commits"| Iceberg
```

**JobManager manages execution; TaskManager executes records.** The JobManager
is not the per-payment prediction engine. Python operators run through Flink's
Python worker machinery associated with TaskManager tasks. ONNX inference
runs there on CPU; the NVIDIA option accelerates llama.cpp, not ONNX or Flink.
There is no deployed standalone Flink ResourceManager pod, model server,
dedicated SQL cluster, or model-training service.

The payment job branches are:

| Branch | Transformation | Actual outputs |
|---|---|---|
| Windows | Validate event time; deduplicate event IDs; 1-minute tumbling and 10-minute sliding windows; 2-minute allowed lateness | Compacted Kafka `payment-window-results-v1`; optional ClickHouse `payment_window_aggregates` |
| Facts | Normalize deduplicated processing events inside the window operator, including events too late for a window | Optional ClickHouse `payment_facts` |
| Too late | Events beyond all retained window boundaries | Kafka `payment-events-too-late`; optional ClickHouse `payment_late_events` |
| Risk | Key by account/currency; maintain prior history; compute contracted features; score ONNX; revise for accepted late history | Kafka `payment-risk-predictions-v1`, `payment-risk-too-late-v1`; optional ClickHouse predictions/late rows |
| Lakehouse | Independent SQL validation and keep-first event-ID deduplication | Iceberg bronze and curated processing-event tables; **not** risk predictions |

The payment job checkpoints every 10 seconds, with 10-second bounded
out-of-order watermarks, 30-second idle-partition detection and 24-hour
deduplication state TTL. The independent SQL job checkpoints every 60 seconds
and retains keep-first deduplication state for 24 hours.

The training path is separate from this always-running stream:

```mermaid
flowchart LR
    Train["Triggered offline payment-risk-model training container"]
    Data["Synthetic account-held-out training/evaluation data"] --> Train
    Train --> Baseline["scikit-learn logistic-regression pipeline"]
    Train --> Candidate["LightGBM comparison"]
    Baseline --> Export["ONNX export / selection"]
    Candidate --> Export
    Export --> Verify["Python vs ONNX golden/held-out parity"]
    Verify --> Package["model.onnx + feature contract + metadata + golden cases + ZIP"]
    Package --> Publish["make risk-publish: immutable, checksum-verified upload"]
    Publish --> Store[("MinIO model-registry")]
    Store -. "startup artifact, not per-record request" .-> Worker["Flink Python risk operator"]
```

Evidence: [actual payment graph](../../modules/stream-processor/job.py),
[submission and bootstrap](../../modules/stream-processor/runtime.py),
[risk operator](../../modules/stream-processor/risk.py),
[model module](../../modules/payment-risk-model/README.md),
[Flink chart roles and ports](../../modules/stream-processor/chart/README.md).

### 4.3 Lakehouse and BI

Iceberg is a **table format and library**, not another HTTP server. Polaris
is the REST catalog, MinIO stores the files, and Trino executes SQL.

```mermaid
flowchart TB
    Kafka[("Kafka processing topic")]
    subgraph LakeJob["Flink payment-lakehouse-v1: separate SQL job"]
        Raw["Bronze projection: original payload + Kafka position"]
        Curated["Validation + event-ID keep-first dedup"]
        Writer["Iceberg writers in TaskManager tasks"]
        Commit["Checkpoint-driven snapshot commit"]
    end
    subgraph Catalog["Polaris"]
        REST["Iceberg REST API + OAuth principals/grants"]
        CatalogDB[("polaris database in local payment PostgreSQL instance")]
    end
    subgraph MinIO["MinIO lakehouse bucket"]
        Bronze[("payment_events_raw: Parquet data")]
        Clean[("payment_events: Parquet data")]
        Meta[("Iceberg metadata / manifests / snapshots")]
    end
    subgraph Trino["Single-node Trino process"]
        Coordinator["SQL coordinator / planning"]
        Worker["Worker execution in same node"]
        ReadCat["lakehouse catalog: reader"]
        AdminCat["lakehouse_admin catalog: maintainer"]
    end
    subgraph Superset["BI dashboards"]
        Web["Web/API process: saved charts and guest-query guard"]
        Setup["Setup hook: migrate / roles / datasets / dashboard"]
        BIStore[("superset metadata database in local payment PostgreSQL instance")]
    end
    CH[("ClickHouse: real-time tables")]
    BFF["Portal BFF"] -->|"request dashboard-scoped guest token"| Web
    Browser["Browser iframe"] -->|"guest token / saved chart requests"| Web
    Kafka --> Raw
    Kafka --> Curated
    Raw --> Writer
    Curated --> Writer
    Writer --> Bronze
    Writer --> Clean
    Writer --> Meta
    Writer -. "completed checkpoint" .-> Commit
    Commit -->|"commit snapshot pointer"| REST
    REST --> CatalogDB
    Web -->|"SELECT real-time facts/aggregates/predictions"| CH
    Web -->|"SELECT historical events"| Coordinator
    Coordinator -. "schedule local query tasks" .-> Worker
    Worker --> ReadCat
    Maint["Explicit lakehouse maintenance command"] --> AdminCat
    ReadCat -->|"resolve table and snapshot metadata"| REST
    AdminCat -->|"authorized maintenance/evolution"| REST
    Worker -->|"read files directly, not through Polaris"| MinIO
    AdminCat -->|"maintenance file operations"| MinIO
    Setup -. "provision assets / metadata" .-> BIStore
    Web --> BIStore
```

Polaris and Superset use separate logical databases/roles on the local
PostgreSQL instance; they are not independent PostgreSQL pods in this path.
Knowledge PostgreSQL and identity PostgreSQL **are** separate workloads.
Trino does not query these metadata databases directly.
Superset uses synchronous queries: no Redis, Celery workers, scheduled reports
or alert execution are deployed.

Evidence: [lakehouse SQL](../../modules/stream-processor/lakehouse.py),
[Polaris](../../modules/lakehouse-catalog/README.md),
[Trino](../../modules/lakehouse-query/README.md),
[ClickHouse sinks](../../modules/stream-processor/analytics.py),
[Superset](../../modules/bi-dashboards/README.md).

### 4.4 Identity and browser requests

```mermaid
flowchart LR
    Browser["Browser: React / opaque HttpOnly session cookie"]
    Ingress["Traefik ingress controller"]
    subgraph Portal["web-portal Go BFF"]
        Static["Static React assets / status routes"]
        Auth["OIDC code + S256 PKCE / nonce / state"]
        Session["Bounded in-memory sessions / refresh / logout"]
        Exchange["Resource-specific token exchange"]
        Payments["Payment proxy / origin and role checks"]
        Assistant["Assistant proxy"]
        Guest["Superset guest-token client"]
    end
    subgraph Keycloak["OPTIONAL identity provider"]
        Realm["rpp realm: users / groups / client roles"]
        OAuth["Authorization / token / JWKS / logout endpoints"]
        IDDB[("Dedicated identity-postgres")]
    end
    API["Payment API: independently validate payment-api audience"]
    AI["Orchestrator: independently validate ai-orchestrator audience"]
    MCP["MCP: independently validate assistant-tools audience"]
    Superset["Superset: OIDC human login / scoped guest API"]
    Grafana["Grafana: OIDC human login"]

    Browser --> Ingress
    Ingress --> Static
    Ingress --> Auth
    Auth -. "browser redirect; user signs in at Keycloak" .-> OAuth
    OAuth -. "one-use authorization code via callback" .-> Auth
    Auth -. "server-side code exchange" .-> OAuth
    Auth -. "verified tokens stored server-side" .-> Session
    Session -. "refresh / revoke" .-> OAuth
    OAuth --> Realm
    Realm --> IDDB
    Session -.-> Exchange
    Exchange -. "one target audience per exchange" .-> OAuth
    Browser -->|"payment request through ingress"| Payments
    Payments -->|"payment-api bearer token; no browser cookie"| API
    Browser -->|"question through ingress"| Assistant
    Assistant -->|"orchestrator token + separate tools/payment tokens"| AI
    AI -->|"tools token; payment token only for lookup"| MCP
    Browser -->|"guest-token request through ingress"| Guest
    Guest --> Superset
    Superset -. "interactive SSO" .-> OAuth
    Grafana -. "interactive SSO" .-> OAuth
```

Keycloak access/refresh tokens never reach React or model input. The narrowly
scoped Superset guest token deliberately does reach the embedded dashboard.
Identity-disabled development login
and assertions exist as a compatibility mode; configured OIDC fails closed.
BFF restart signs users out because durable multi-replica sessions are not
implemented. DBX has its own local password, not Keycloak SSO.

Evidence: [BFF](../../modules/web-portal/README.md),
[identity chart](../../platform/terraform/modules/identity/chart/README.md),
[identity ADR](../adr/ADR-032-keycloak-identity-and-bff.md).

### 4.5 AI knowledge ingestion and interactive execution

```mermaid
flowchart TB
    Corpus["Repository documents / ADRs / runbooks / contracts"]
    Bundle["Manifest validation + deterministic corpus bundle"]
    Template["Suspended CronJob: ingestion pod template, no scheduled runs"]
    Trigger["make knowledge-ingest: create Job from CronJob"]
    subgraph Ingestion["operations-knowledge batch Job"]
        Parse["LangChain heading-aware chunks / document versions"]
        Quarantine["Injection detection / quarantined chunks"]
        Index["Index writer"]
    end
    subgraph Serving["llm-serving: one pod, two containers"]
        Fetch["Init: download and checksum-verify GGUF files"]
        PVC[("Kept ReadWriteOnce model PVC")]
        Chat["chat: Qwen2.5 1.5B Q4_K_M / :8080"]
        Embed["embed: nomic-embed-text v1.5 / :8081 / 768 dimensions"]
    end
    Originals[("MinIO knowledge-originals")]
    KDB[("Knowledge PostgreSQL: versions / chunks / pgvector / full-text")]
    subgraph Orchestrator["ai-orchestrator FastAPI + LangGraph"]
        Auth["Authenticate / authorize"]
        Intent["Classify intent / reject injection"]
        Retrieve["Retrieve via MCP / check quality"]
        Plan["Model-assisted tool plan"]
        Authorize["Deterministic allowlist / permissions / argument bounds"]
        Tools["Execute bounded tools"]
        Generate["Generate schema-constrained answer"]
        Structured["Validate structured output"]
        Verify["Verify citation labels"]
        Redact["Redact sensitive data"]
        Audit["Audit hash / length / result; return answer"]
    end
    subgraph MCP["assistant-tools MCP server"]
        Gate["Token / permissions / argument bounds"]
        Search["search_knowledge: SQL filters + hybrid rank fusion"]
        DataTools["get_payment / payment_metrics / risk_model_summary / payment_history"]
    end
    Data["Payment API / ClickHouse / optional Trino"]
    BFF["Portal BFF"]
    GPU["OPTIONAL NVIDIA GPU: two time-sliced shared allocations"]
    Plugin["Terraform-owned NVIDIA device plugin"]
    Allocate["Kubelet resource allocation"]

    Corpus --> Bundle
    Bundle --> Parse
    Template -. "pod specification" .-> Trigger
    Trigger -. "launch finite batch" .-> Ingestion
    Parse --> Originals
    Parse --> Quarantine
    Quarantine -->|"safe text"| Index
    Quarantine -->|"stored but excluded from retrieval"| KDB
    Index -->|"document embedding request"| Embed
    Embed -->|"vectors"| Index
    Index --> KDB
    Fetch --> PVC
    PVC --> Chat
    PVC --> Embed
    BFF --> Auth --> Intent
    Intent -->|"injection signals: rejected"| Redact
    Intent --> Retrieve
    Intent --> Plan
    Intent --> Generate
    Intent -->|"model-assisted classification"| Chat
    Retrieve -->|"rag_tools after quality check"| Plan
    Retrieve -->|"empty results: one keyword retry"| Retrieve
    Plan --> Authorize --> Tools
    Plan -->|"schema-constrained proposal"| Chat
    Retrieve --> Gate
    Tools --> Gate
    Gate --> Search
    Gate --> DataTools
    Search -->|"query embedding"| Embed
    Search -->|"tenant / permission / active-version / quarantine filters"| KDB
    Search -->|"authorized ranked chunks"| Retrieve
    DataTools --> Data
    DataTools -->|"bounded read-only results"| Tools
    Retrieve --> Generate
    Tools --> Generate
    Generate --> Chat
    Chat -->|"structured answer proposal"| Structured
    Structured --> Verify
    Verify -->|"invalid citations: one retry"| Generate
    Verify --> Redact --> Audit
    Audit -->|"answer / citations / statuses"| BFF
    GPU -. "device discovery" .-> Plugin
    Plugin -. "advertise two nvidia.com/gpu.shared slots" .-> Allocate
    Allocate -. "same CUDA device; one slot; full layer offload" .-> Chat
    Allocate -. "same CUDA device; one slot; full layer offload" .-> Embed
```

`ai-core` is a shared library copied into ingestion, MCP and orchestrator
images: identity validation, embeddings adapter, hybrid SQL retrieval and
deterministic safety utilities. It is **not** another pod.
The graph never connects directly to PostgreSQL, ClickHouse or Trino; MCP
owns those accesses. Authorization occurs in code before retrieval and again
at the tool boundary, never as a model decision.

GPU mode is optional; CPU remains available. Both llama.cpp containers request
one `nvidia.com/gpu.shared` slot. The fetch init container gets none.
Time slicing shares compute and VRAM, without VRAM isolation or extra host RAM.
The vendor chart's label-gated MPS helper is not part of the chosen time-slicing
execution path. There is no vLLM, separate embedding service module, persisted
LangGraph checkpointer, CAG cache, or asynchronous LLM-enrichment worker here.

Evidence: [ingestion](../../modules/operations-knowledge/README.md),
[actual graph](../../modules/ai-orchestrator/README.md),
[MCP tool/backend table](../../modules/assistant-tools/README.md),
[shared library](../../modules/ai-core/README.md),
[model servers](../../modules/llm-serving/README.md),
[GPU decision](../adr/ADR-034-shared-local-ai-gpu.md).

## 5. Data model and contracts

| Store/topic | Contents | Writer / reader |
|---|---|---|
| PostgreSQL `payments` database | Payments, idempotency, ordered outbox, receipts, dead letters | API, relay, receipt consumer |
| Kafka `payments.initiated.v1` | Schema-ID-framed Avro initiation event; partition key account ID | Relay / receipt consumer |
| Kafka `payment-processing-events-v1` | JSON simulated outcomes derived from API payments; CreateTime equals event time | Outbox relay / two independent Flink jobs |
| Kafka `payment-window-results-v1` | Keyed aggregate result payloads; compacted topic | Flink / inspection tools |
| Kafka late/risk topics | Late-event records and advisory prediction revisions | Flink / verification and inspection tools |
| ClickHouse | Facts, window revisions, late events, prediction revisions and lineage fields | Flink; explicit synthetic AI fixtures / BI and MCP |
| Iceberg bronze/curated tables | Raw and validated/deduplicated processing events | Flink SQL / Trino |
| Polaris metadata database | Catalog objects, access grants and table metadata references | Polaris |
| MinIO | `flink-state`, `lakehouse`, `model-registry`, `knowledge-originals` buckets | Checkpointing, Iceberg clients, model publication, ingestion |
| Knowledge PostgreSQL | Versioned documents, classified chunks, full-text and vector indexes | Ingestion / MCP retrieval |
| Identity PostgreSQL | Keycloak realm/client/user/session data | Keycloak |
| Superset metadata database | Roles, dashboards, chart definitions and connection metadata | Superset setup and web process |
| DBX PVC | Workbench configuration and runtime state | DBX server and provisioning |

Checkpointed Flink internal state does not make all external stores one
transaction. Kafka side sinks and ClickHouse writes can replay; replacement
keys/revisions and `FINAL` queries handle ClickHouse duplicates logically.
Iceberg commits happen on the separate lakehouse job's checkpoints.
Durable lineage fields exist; no dedicated lineage catalog/UI is deployed.

## 6. Key flows

### Payment initiation is asynchronous after commit

```mermaid
sequenceDiagram
    participant C as Client / BFF
    participant A as Payment API
    participant P as PostgreSQL
    participant R as Relay
    participant K as Kafka
    participant W as Receipt consumer
    C->>A: POST payment + idempotency key
    A->>P: Transaction: payment + trigger-created outbox
    P-->>A: Commit / original record for identical retry
    A-->>C: HTTP 201 + payment, identical retry returns same response
    R->>P: Poll ordered pending outbox
    R->>K: Publish schema-framed initiation event
    K-->>R: Broker acknowledgement
    R->>P: Mark published
    K->>W: Deliver event
    W->>P: Deduplicated receipt or durable quarantine
    W->>K: Commit handled offset
```

### A processing event has independent consumers

```mermaid
sequenceDiagram
    participant F as Authenticated synthetic API client
    participant A as Payment API / PostgreSQL outbox
    participant R as Relay with deterministic simulation
    participant K as Kafka processing topic
    participant T as TaskManager payment job
    participant O as ONNX operator inside Python worker
    participant H as ClickHouse
    participant L as Separate TaskManager lakehouse job
    participant M as MinIO / Polaris
    F->>A: Payment request + service token + idempotency key
    A-->>F: Persisted payment and processing event identity
    A->>R: Pending initiation outbox row
    R->>K: Initiation plus derived JSON processing event
    K->>T: payment-windows-v1 consumer group
    T->>H: Optional normalized fact and window revisions
    T->>O: Optional independent account-history branch
    O->>H: Optional advisory prediction revision
    O->>K: Prediction audit / too-late output
    T->>K: Window result / too-late output
    K->>L: Independent payment-lakehouse-v1 consumer group
    L->>M: Bronze/curated files and checkpoint snapshot commit
```

The two jobs do not wait for each other's results. Window emission is driven by
watermarks/timers; the diagram does not imply an immediate completed window
for every event or a prediction-to-Iceberg connection.

## 7. Security and privacy

- Keycloak, when enabled, authenticates humans. API, orchestrator and MCP
  validate their own audience-scoped tokens and roles.
- Internal Kafka uses TLS/SCRAM; application credentials come from Kubernetes
  Secrets. Diagrams intentionally contain no passwords or token values.
- DBX and maintainer commands are developer access paths, not user-facing
  payment or AI authorization boundaries.
- Superset readers, MCP queries and Polaris reader/writer/maintainer principals
  are scoped separately. Internal connectivity is not proof of network-policy
  isolation.
- No real payment/cardholder data, OpenBao or External Secrets Operator is
  introduced by these diagrams.

## 8. Observability

Full observability is implemented in the optional three-cluster topology:
collectors per cluster, shared backends in cluster 1. The single-cluster
identity path installs **Grafana only**, not the entire collector/backend set.

```mermaid
flowchart LR
    Browser["React status interaction spans"]
    BFF["Portal telemetry proxy / Go server spans"]
    Apps["Payment API / relay / receipt-consumer spans and metrics"]
    Pods["Annotated workload metrics / pod log files"]
    Kafka["Initiation-topic and receipt-group metrics"]
    PG["CNPG PostgreSQL metrics in multi-cluster mode"]
    subgraph Collector["OpenTelemetry Collector per cluster"]
        OTLP["OTLP HTTP receiver"]
        Scrape["Prometheus scrape + selected Kafka metrics"]
        File["Filelog receiver"]
        Process["Memory limiter / resource labels / batch"]
    end
    Prom[("Prometheus metrics + alert rules")]
    Loki[("Loki logs")]
    Tempo[("Tempo traces")]
    AM["Alertmanager"]
    Graf["Grafana dashboards / data-source queries"]
    Browser -. "same-origin browser OTLP" .-> BFF
    BFF -. "OTLP traces" .-> OTLP
    Apps -. "OTLP traces" .-> OTLP
    Apps -. "metrics scrape" .-> Scrape
    Pods -. "annotated metrics" .-> Scrape
    Pods -. "apps namespace log files" .-> File
    Kafka -. "selected topic/group collection on backend cluster" .-> Scrape
    PG -. "metrics scrape" .-> Scrape
    OTLP -.-> Process
    Scrape -.-> Process
    File -.-> Process
    Process -. "remote-write metrics" .-> Prom
    Process -. "OTLP logs" .-> Loki
    Process -. "OTLP traces" .-> Tempo
    Prom -. "firing alerts" .-> AM
    Graf --> Prom
    Graf --> Loki
    Graf --> Tempo
```

This is not universal instrumentation of every box. Flink has operator/model
metrics and inspection APIs; the current collector's selected Kafka metrics
cover initiation/receipt traffic, not every processing or risk topic.
The implemented MCP allowlist does **not** query Prometheus, Loki or Tempo.
Alertmanager's local receiver has no configured email, Slack or webhook
destination; firing alerts are not automatically external notifications.

Evidence: [collector configuration](../../platform/terraform/modules/observability/chart/templates/collector.yaml),
[observability chart](../../platform/terraform/modules/observability/chart/README.md),
[observability runbook](../runbooks/observability.md).

## 9. Testing strategy

This is a source-grounded reference; it does not start, reset or certify a
cluster. Relevant executable evidence lives in:

- [Payment integration tests](../../modules/payment-api/README.md#make-targets):
  idempotency, outbox replay, schema and receipt/quarantine behavior.
- [Flink/model checks](../../modules/payment-risk-model/README.md#make-targets):
  actual worker execution, golden-score parity and late-history revisions.
- [Lakehouse verification](../runbooks/lakehouse.md): snapshot, deduplication,
  evolution and reader/maintainer boundaries.
- [AI evaluation](../runbooks/ai-assistant.md): retrieval, citation,
  authorization and injection cases.
- [Identity verification](../runbooks/unified-identity.md): code flow, SSO,
  resource access, rotation, expiry and logout.
- [GPU live evidence](../developer-setup.md#optional-nvidia-acceleration-for-ai):
  concurrent chat/embedding requests and observed shared-device allocations.

Documentation validation uses the existing Markdown/format hooks. Historical
test success is not a claim that workloads are running right now.

## 10. Deployment and operations

### 10.1 Execution control plane

```mermaid
flowchart TB
    Dev["Developer Make commands"]
    Scripts["Local lifecycle / feature / safety scripts"]
    Build["Host Docker builds: catalog-discovered modules"]
    Load["Minikube image load into explicit profile"]
    TF["Terraform: profile-scoped state and dependencies"]
    Helm["Helm releases / templates / lifecycle hooks"]
    Direct["Direct Kubernetes namespaces / Secrets / storage resources"]
    subgraph K8s["Each Minikube Kubernetes cluster"]
        APIServer["Kubernetes API server"]
        Etcd[("etcd: cluster object state")]
        Controllers["Deployment / StatefulSet / Job controllers"]
        Scheduler["Pod scheduler"]
        Kubelet["Node kubelet + container runtime"]
        Service["Services / kube-proxy + CoreDNS discovery"]
        Pods["Application pods / init containers / hook Jobs"]
        Volumes[("PVC/PV local storage")]
    end
    Dev -.-> Scripts
    Scripts -.-> Build
    Build -.-> Load
    Load -. "images available to node runtime" .-> Kubelet
    Scripts -.-> TF
    TF -.-> Helm
    TF -.-> Direct
    Helm -. "render/install" .-> APIServer
    Direct -.-> APIServer
    APIServer -.-> Etcd
    APIServer -.-> Controllers
    Controllers -. "desired pods" .-> APIServer
    Scheduler -. "assign pods to node" .-> APIServer
    APIServer -. "assigned pod specifications" .-> Kubelet
    Kubelet -. "start/restart/probe containers" .-> Pods
    Service -->|"name resolution and routed requests"| Pods
    Pods --> Volumes
```

Terraform/Helm create desired resources; Kubernetes controllers and kubelet
run them. Flink's JobManager schedules **Flink tasks**, while Kubernetes
schedules **pods**. These are different schedulers at different layers.
Services are stable routing/discovery addresses, not extra application
processes. PVCs are persistent storage, not database engines.

### 10.2 Single versus three-cluster placement

```mermaid
flowchart TB
    Host["One developer host / Docker / optional NVIDIA GPU"]
    Host -. "MULTI_CLUSTER=0" .-> Single["rpp: one Kubernetes node; selected application features"]
    Host -. "MULTI_CLUSTER=3" .-> Network["Shared Docker network"]
    Network -.-> C1["rpp-1: base stack + selected heavy features"]
    Network -.-> C2["rpp-2: base stack"]
    Network -.-> C3["rpp-3: base stack"]
    C1 <-->|"Kafka broker replication / KRaft quorum"| C2
    C2 <-->|"Kafka broker replication / KRaft quorum"| C3
    C3 <-->|"Kafka broker replication / KRaft quorum"| C1
    Primary["Selected CNPG global writer: pg-1 / pg-2 / pg-3"]
    Replicas["Other CNPG members: replica clusters / local instances"]
    C1 -. "CNPG operator; selected writer or replica" .-> Primary
    Primary -->|"PostgreSQL WAL replication"| Replicas
    C2 -. "members managed by local CNPG operator" .-> Replicas
    C3 -. "members managed by local CNPG operator" .-> Replicas
```

The CNPG writer is selected configuration, not necessarily always cluster 1.
The illustrated association with cluster 1 is the default placement; explicit
switchover/failover changes the writer. CNPG operators manage PostgreSQL
members; they do not execute application SQL. The base multi-cluster
infrastructure can run without the full-stack CNPG option.
Three profiles on one laptop are a learning/failure-recovery topology, not
independent hardware failure domains. Heavy optional AI/analytics/lakehouse
features and GPU passthrough belong to cluster 1 in the multi-cluster workflow.
There is no single topology template promising every feature at once.

### 10.3 Developer workbench and supporting Jobs

```mermaid
flowchart LR
    Browser["Developer browser"] --> Forward["make dbx-access: loopback port-forward"]
    subgraph DBX["Optional DBX workbench"]
        Server["DBX web/server process"]
        Install["Runtime-install init: verified JDBC/MQ drivers"]
        Sync["Connection-provisioning process"]
        Config[("PVC: workbench configuration / runtime state")]
    end
    Forward --> Server
    Install -. "installed drivers" .-> Server
    Sync -. "idempotent managed connection profiles" .-> Server
    Server --> Config
    Server --> Payments[("Payments PostgreSQL")]
    Server --> Superset[("OPTIONAL Superset metadata PostgreSQL")]
    Server --> Polaris[("OPTIONAL Polaris metadata PostgreSQL")]
    Server --> Knowledge[("OPTIONAL knowledge PostgreSQL")]
    Server --> CH[("OPTIONAL ClickHouse: analytics reader")]
    Server --> Trino["OPTIONAL Trino"]
    Server --> Kafka["Kafka: TLS/SCRAM"]
```

- **DBX:** a server/persistent workbench plus runtime-install and
  connection-provisioning logic. Its seven managed connection profiles are
  shown above; optional profiles exist only when their backing configuration
  is present. It reaches PostgreSQL, Kafka, ClickHouse and Trino internally;
  browser access uses an explicit loopback forward. It does not relay normal
  payment events, run Flink tasks, or sit between BI and the databases.
- **Setup hooks/init Jobs:** migrations, Kafka topic/schema setup, Polaris
  catalog/table/principal setup, ClickHouse table setup, Superset assets,
  identity realm seed/reconciliation, knowledge database grants, DBX connection
  sync and Flink submission.
  They are provisioning dependencies, not continuously executing data stages.
- **Triggered tools:** fixtures, load/benchmark drivers, model training and
  publication, knowledge ingestion, maintenance and acceptance checks.
  `event-generator` is not a deployed daemon; current generators are tooling.
- **Delivery:** local build/load/deploy works without publication or an active
  PI. GitHub build/publication/release-state workflows are a separate artifact
  lifecycle, not a Kafka/data processing step. Terraform still owns local
  deployments rather than Argo CD.

Evidence: [local workflow](local-platform-guide.md),
[single-cluster Terraform](../../platform/terraform/environments/local/main.tf),
[multi-cluster orchestration](../../scripts/multi-cluster.sh),
[CNPG composition](../../platform/terraform/environments/kafka-multicluster/database.tf),
[DBX infrastructure](../../platform/terraform/environments/local/dbx.tf).

## 11. Risks and open questions

### Catalog entries are not proof of implemented services

| Catalog module | Implemented form |
|---|---|
| `payment-api` | HTTP API + migration init + relay + receipt consumer |
| `web-portal` | React assets + Go BFF process |
| `stream-processor` | Flink session cluster, submission hook, payment and lakehouse jobs |
| `realtime-analytics` | ClickHouse StatefulSet + schema hook |
| `lakehouse-catalog` | Polaris REST catalog + setup/init logic |
| `lakehouse-query` | Single Trino coordinator/worker |
| `bi-dashboards` | Superset web process + setup hook |
| `payment-risk-model` | Offline training/export/package code + in-worker scoring library; no standalone serving pod |
| `operations-knowledge` | Versioned corpus, suspended CronJob template, explicitly triggered ingestion Jobs and evaluation tooling |
| `ai-core` | Shared Python library; no standalone deployment |
| `ai-orchestrator` | FastAPI + request-local LangGraph execution |
| `assistant-tools` | Read-only MCP server |
| `llm-serving` | One pod with fetch init and two llama.cpp containers |
| `event-contracts` | Schema/data bundle; no runtime service |
| `event-generator` | Catalog-only module; synthetic generators currently live in tooling |
| `ai-gateway` | Catalog-only module; current browser AI boundary is the portal BFF |
| `llm-enrichment-worker` | Catalog-only module; asynchronous LLM enrichment is not implemented |

**Do not infer these target-state connections from the overall master plan:**

- Real authorization/settlement decisions: outcomes remain explicitly simulated.
- Risk prediction output automatically being written to Iceberg.
- Risk triggering Kafka LLM requests and an enrichment worker.
- MCP querying observability stores.
- Argo CD, OpenBao, External Secrets Operator or a lineage platform running.
- A GPU providing system RAM or accelerating the ONNX operator.
- Model training happening automatically for each event.
- Every feature running or enabled merely because it exists in the catalog.

## 12. Related ADRs and LLDs

- [Transactional outbox](../adr/ADR-003-transactional-outbox.md).
- [Local cross-cluster Kafka](../adr/ADR-016-local-cross-cluster-kafka.md).
- [Cross-cluster PostgreSQL](../adr/ADR-017-full-stack-cross-cluster-replication.md).
- [Shared observability](../adr/ADR-019-shared-observability-backends.md).
- [PyFlink event-time semantics](../adr/ADR-022-pyflink-event-time-semantics.md).
- [ClickHouse analytics](../adr/ADR-023-clickhouse-analytics.md).
- [Advisory risk features](../adr/ADR-024-advisory-event-time-risk-features.md).
- [Polaris/Iceberg lakehouse](../adr/ADR-026-polaris-iceberg-lakehouse.md).
- [Embedded Superset](../adr/ADR-028-embedded-superset-bi.md).
- [AI operations assistant](../adr/ADR-029-ai-operations-assistant.md).
- [Keycloak and BFF](../adr/ADR-032-keycloak-identity-and-bff.md).
- [Optional DBX](../adr/ADR-033-optional-dbx-development-workbench.md).
- [Shared local AI GPU](../adr/ADR-034-shared-local-ai-gpu.md).
