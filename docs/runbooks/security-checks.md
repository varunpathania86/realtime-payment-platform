# Runbook: Advisory IaC security checks

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-10-25

## Symptoms

The separate Advisory IaC security workflow reports findings or a scan execution
failure while normal build/component checks may pass.

## Impact

Stage 1 scans shared platform Terraform and packaged module Dockerfiles/charts/IaC.
It does not yet scan image vulnerabilities, dependency CVEs, or implement a
SonarCloud-equivalent quality platform. Scans do not build, publish, or deploy.
TFLint and existing secret detection remain normal quality checks.

Security findings initially are advisory for merging. This is repository policy,
not an automatic effect of using another workflow. Keep the build quality and
applicable component checks required in branch protection/rulesets; do not add
security job contexts to the required list yet. Avoid a blanket "all workflows
must pass" rule if the intended policy is advisory. No settings are changed
automatically by this implementation.

## Diagnosis

```bash
make doctor
make security
make security-solution
make security-module MODULE_NAME=payment-api
```

Inspect `.local/security/solution.json` and `module-<name>.json`, with readable
Markdown siblings `solution.md` and `module-<name>.md`, or download
the security workflow's report artifacts (retained for 30 days). Each scoped
scan replaces its old report before execution, so a failed run cannot advertise
stale evidence. Solution and module jobs run separately; modules are dynamically
discovered by catalog identity and actual IaC packaging, not module type.
All packaged modules are scanned on each PR/push, not only changed modules.
The Markdown reports include findings and suggested remediation, or an explicit
incomplete-scan error. GitHub job summaries show status/counts; full JSON and
Markdown reports are in `security-solution` / `security-module-<name>` artifacts.
Quality reports are separate: run `make quality-report` for
`.local/quality/solution.md` and `.log`; the build workflow uploads them as
`quality-solution` (and `quality-module-<name>` for its module matrix).

The scanner returns 10 for findings and other nonzero codes for execution errors.
Make itself normally returns 2 when its recipe fails; read the diagnostic to
distinguish findings from an incomplete scan. A red advisory check is truthful
and can remain non-required. No `continue-on-error` or exit-zero override hides
findings or scanner failure.

First use may download public check definitions. A network error, invalid
configuration, or missing scanner is an execution failure, not a clean result.
Fork PRs use read-only tokens on GitHub-hosted runners, with no publishing
credentials or SARIF write permissions.

## Mitigation

Triage each finding against the rendered/deployed configuration. Examples of
context needing review include Docker HEALTHCHECK versus Kubernetes probes,
Helm's default rendering namespace versus the actual `apps` namespace, and the
intentional local `rpp-local` image namespace. Reports are evidence, not automatic
proof of exploitability. Do not add blanket suppressions or weaken existing
secret detection just to obtain green checks.

On 2026-10-25, the initial solution scan had no findings and module scans reported
eight findings: one high, two medium, and five low. The high finding flags the
scratch contract bundle's default user; that image contains data only and has
no executable entrypoint. Review its intended consumption rather than treating
it as a deployed root-running service. Findings remain visible pending triage.

Fix genuine defects, or document a narrow, reviewed exception with rationale and
follow-up. Fix scan execution errors so the scan actually completes.
Use the same root `trivy.yaml` and `.tflint.hcl` locally and in CI.

## Verification

- `make ci` must still pass independently of Trivy findings.
- `make security` returns zero only for completed scans without findings.
- Findings produce nonzero status and JSON evidence; execution failures produce
  explicit errors and never success-shaped fallback reports.
- Confirm security checks are not required by the configured merge rules.

## Prevention and follow-up

Run local checks before pushing. Maintain pinned installations through
`make setup`, with verification by `make doctor`.
Stage 16 adds broader secrets/TLS/policy and image/dependency scanning.
Stage 17 adds delivery/promotion integration. Later enforcement can require
selected reliable security gates through an explicit policy change.
