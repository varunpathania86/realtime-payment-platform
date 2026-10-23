# Local development rules

Applies to: `scripts/local-dev.sh`, `scripts/smoke.py`, `scripts/check_local_chart.py`,
`platform/terraform/**`, `modules/*/Dockerfile`, and `modules/*/chart/**`.

- Keep local development independent of GitHub, GHCR credentials, publication
  approvals, release state, and the PI clock. Surface genuine local errors.
- Build with the same module Dockerfiles used for publication, with toolchain
  versions from `.tool-versions`. Do not embed a second tool-version policy.
- Discover runnable modules from the solution catalog plus Dockerfile/chart
  presence. Do not infer deployability from module type.
- Host Docker images must be loaded into the explicitly selected Minikube
  profile. Local development references use source-content tags and pull policy
  `Never`; do not mutate stable catalog versions.
- Every command must use the selected kubeconfig context and profile-specific
  git-ignored Terraform state. Do not silently deploy to the current context.
- Terraform owns local namespaces and Helm releases until the planned GitOps
  transition. Do not add competing imperative Helm/Kubernetes ownership.
- Preserve non-root, read-only containers, probes, resource requests/limits,
  and loopback-only developer ingress forwarding.
- Update the root/module READMEs, developer guide, master-plan status, and runbook
  when local behavior changes. Document only implemented commands as available.
- Follow `ai/skills/local-development/SKILL.md` for verification and cleanup.
