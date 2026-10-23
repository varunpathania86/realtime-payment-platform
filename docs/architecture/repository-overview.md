# Repository and delivery overview

How the monorepo's independently versioned modules are cataloged and how changes travel from branch to release.

## Layout

```mermaid
flowchart TB
  root[realtime-payment-platform]
  root --> catalog[solution.yaml - solution and module catalog]
  root --> modules[modules/*/module.yaml - independently versioned capabilities]
  modules --> apps[APIs, workers, web applications]
  modules --> data[contracts, analytics, ML model, LLM knowledge]
  root --> platform[platform/ - shared Terraform, Helm, GitOps]
  root --> tests[tests/ - cross-module verification]
  root --> docs[docs/ - SDD, ADR, LLD, runbooks]
  root --> templates[templates/ - document and project templates]
  root --> github[.github/ - workflows and repository governance]
  root --> ai[AGENTS.md and ai/ - tool-neutral instructions and skills]
```

## Delivery flow

```mermaid
flowchart LR
  B[feature/ bugfix/ patch/ hotfix/ branch] --> PR[Pull request]
  PR --> Build[Validate and build affected modules dynamically]
  Build --> Merge[Squash merge to main]
  Merge --> Revision[Allocate idempotent module revision]
  Revision --> Promote[Protected promotion of exact OCI digests]
  Promote --> Stable[Stable-version catalog PR]
```

CI validates catalogs and builds/tests modules. Successful same-repository push CI can trigger protected branch/main OCI publication using trusted default-branch tooling. Main revision reservations use atomic updates on `automation/release-state`; no temporary versions enter catalogs. See the [publication runbook](../release-publication.md) for activation and recovery. Promotion and stable-version PR automation in the diagram remain target-state steps.

## Local development flow

```mermaid
flowchart LR
  Source[Local source changes] --> Build[Host Docker build]
  Build --> Load[Minikube image load]
  Load --> Terraform[Profile-specific Terraform state]
  Terraform --> Helm[Traefik and application Helm releases]
  Helm --> Check[Ingress smoke checks and browser]
  Check --> Source
```

`make dev-up` and `make dev-update` implement this path without GitHub Actions,
GHCR, publication approvals, release reservations, or the PI clock.
Module-owned charts live with their application; shared Terraform composition
lives under `platform/terraform/`. Terraform owns the releases until the
planned Argo CD transition. See [ADR-001](../adr/ADR-001-terraform-local-development.md).
