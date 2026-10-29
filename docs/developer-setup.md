# Developer setup

How to prepare a machine to work on this repository. Target environment: Windows with WSL2 (Ubuntu), VS Code Remote - WSL. Native Linux and macOS work the same way.

Work inside the WSL filesystem (for example `~/code`), not under `/mnt/c`, for speed and correct line endings.

## 1. Prerequisites

| Tool | Needed for | Install (Ubuntu/WSL) |
|---|---|---|
| git | Version control | `sudo apt install git` |
| make | All workflows | `sudo apt install make` |
| python3 with venv | pre-commit and catalog validation | `sudo apt install python3 python3-venv` |
| SSH key on GitHub | Push access | [GitHub docs](https://docs.github.com/en/authentication/connecting-to-github-with-ssh); verify with `ssh -T git@github.com` |
| GitHub CLI (optional) | Creating pull requests | [cli.github.com](https://cli.github.com); then `gh auth login` |

`make setup` installs the pinned tools, Node.js with npm, and Docker (see below).

### Pinned tools

Versions are pinned in [.tool-versions](../.tool-versions). `make setup` (or `make tools`) downloads kubectl, Minikube, Helm, Terraform, Go, jq, yq, Node.js, TFLint, and Trivy into `~/.local/bin` without sudo, verifying each download with SHA-256. Go is unpacked to `~/.local/go`; Node.js includes npm and lives in a versioned directory under `~/.local/`. Tools already at the pinned version are skipped, so the command is safe to repeat.

Make sure `~/.local/bin` is on your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

To upgrade a tool, change its version in `.tool-versions` and run `make tools`.
For a focused installation use `make tools TOOLS="tflint trivy"`.
GitHub release checksum lookups use `GH_TOKEN` (or `GITHUB_TOKEN`) when set;
CI supplies its scoped job token to avoid anonymous API rate limits. Local setup
still works without a token, subject to GitHub's anonymous rate limits.
Authentication is sent only to the GitHub API metadata request, not release
asset redirects or other download hosts. Metadata failures stop installation
with an explicit error; checksum verification is never skipped.
TFLint uses its bundled Terraform ruleset and root `.tflint.hcl`; no downloaded
provider plugin is required. Trivy uses root `trivy.yaml` and may download an
updated public checks bundle on first use. It scans locally; no source upload,
SaaS subscription, or registry authentication is needed for IaC scans.

### Docker

Minikube runs on the Docker driver. `make setup` runs `scripts/install-docker.sh` (also available alone as `make docker`):

- If `docker info` already works (Docker Desktop with WSL integration, or an existing Engine), nothing is installed.
- Otherwise it installs Docker Engine from Docker's official apt repository, enables the service, and adds you to the `docker` group. This needs `sudo`, so it asks for your password once. Supported: Ubuntu and Debian, including WSL2 with systemd.
- Docker then works without `sudo`, even in the terminal you ran `make setup` from: the group membership applies to new shells, and a one-time access rule on the Docker socket (`setfacl`) covers open ones. If you ever see a permission error after a Docker restart, run `make docker` again or open a new terminal (in WSL: `wsl --shutdown` from Windows PowerShell, then reopen).

On other systems, install Docker manually ([Docker Desktop](https://docs.docker.com/desktop/) or [Engine](https://docs.docker.com/engine/install/)) and verify with `docker info`.

### Node.js

The exact Node.js version is pinned in `.tool-versions` and installed, with npm,
by `make setup`. No manual Node manager is required. The portal uses `npm ci`
with its committed lockfile; its Makefile installs dependencies when needed.
`make doctor` checks Node.js and npm along with the other required tools.

## 2. One-time setup

```bash
git clone git@github.com:varunpathania86/realtime-payment-platform.git
cd realtime-payment-platform
make setup
```

`make setup`:

1. Creates the git-ignored `.venv/` and installs the pinned catalog-validation libraries and Apache Avro 1.12.0 from `scripts/requirements-catalog.txt`; installs `pre-commit` there only if it is not already on your PATH. Avro validates and binary-round-trips the first synthetic event contract.
2. Installs the git pre-commit hook, so checks run on every commit.
3. Installs the pinned tools from `.tool-versions` (`scripts/install-tools.sh`).
4. Installs Docker Engine if it is missing (`scripts/install-docker.sh`, needs sudo).
5. Runs `make doctor`, which compares everything with `.tool-versions` and lists problems (for example a missing Docker).

### Editor (optional)

[.vscode/extensions.json](../.vscode/extensions.json) lists recommended VS Code extensions: Remote - WSL, EditorConfig, markdownlint, ShellCheck, YAML, Mermaid preview, Docker, Kubernetes, Terraform, and Go. VS Code offers to install them when you open the folder; nothing is installed automatically and `make setup` does not depend on them. Open the repo from WSL (`code .` inside the WSL terminal) so the extensions run in WSL. Other editors can ignore this file.

## 3. Daily commands

| Command | Purpose |
|---|---|
| `make help` | List all targets |
| `make doctor` | Check the environment against `.tool-versions` |
| `make validate-catalog` | Validate `solution.yaml`, module manifests, release manifests, and dependency references |
| `make test-catalog` | Run offline tests for catalog validation, versions, reservations, classification, and publication recovery |
| `make tools` | Install or update the pinned tools |
| `make docker` | Install Docker Engine if missing (needs sudo) |
| `make setup-agent` | Optional: register this machine as a self-hosted runner for the Deploy workflow ([runbook](runbooks/self-hosted-runner.md), including the dedicated WSL instance steps) |
| `make cluster-up` | Start the local Minikube cluster (profile `rpp`) |
| `make cluster-status` | Show cluster status |
| `make cluster-down` | Delete the cluster and its data (destructive) |
| `make dev-up` | Build/load local application images, provision Terraform/Helm, and smoke-test |
| `make dev-update` | Rebuild/load local images and apply application updates |
| `make dev-images` | Build/load images and write the profile-specific image inventory |
| `make dev-access` | Forward local ingress to loopback port 8080 until Ctrl-C |
| `make dev-status` | Inspect local namespaces and application resources |
| `make smoke` | Verify ingress, API build identity, readiness, and the React entry page |
| `make dev-down` | Destroy Terraform-managed local resources, preserving Minikube (destructive) |
| `make ci` | Lint, test, and build; same as the Build workflow |
| `make quality-report` | Run `make ci`, retaining Markdown results and full text logs |
| `make quality-module-report MODULE_NAME=payment-api` | Run the module quality contract with Markdown/log output |
| `make lint` | Shared static checks and per-project lint |
| `make security` | Separate Trivy IaC checks for shared solution and packaged modules; findings return nonzero |
| `make security-solution` | Scan shared platform Terraform/IaC |
| `make security-module MODULE_NAME=payment-api` | Scan one catalog module's Dockerfile/chart/IaC |
| `make fmt` | Format all sub-projects |
| `make projects` | List discovered sub-projects |

### Quality and security reports

`make ci` and `make lint` print directly to the terminal. To retain a quality
report locally, run `make quality-report`; it runs the same `make ci` command,
streams its output, and writes `.local/quality/solution.md` plus `solution.log`.
For a single module, use `make quality-module-report MODULE_NAME=payment-api`;
output is `.local/quality/module-payment-api.md` and `.log`.
These are command-result reports with full diagnostic output, not coverage
reports or a Sonar-style quality score. Checks remain fail-fast and failures
return nonzero; later checks may not have run.

Every `make security`, `make security-solution`, or `make security-module` scan
automatically writes both JSON and Markdown under `.local/security/`:
`solution.json` / `solution.md`, and `module-<name>.json` / `module-<name>.md`.
Markdown includes severity, check ID, target, scanner-reported line where
available, finding, message, and remediation. Failed/incomplete scans write
an error Markdown report, never a clean-scan result.
Module-owned `.trivyignore.yaml` files contain exact-file/check exceptions with
rationales and expiry dates, listed in each report. They do not disable scanner
rules globally. See [the security triage record](runbooks/security-checks.md#mitigation).

In GitHub Actions, open the workflow run's **Summary** for job status summaries.
Download full reports from **Artifacts**: `quality-solution`,
`quality-module-<name>`, `security-solution`, or `security-module-<name>`.
They are retained for 30 days and uploaded even when checks fail, provided the
reporting step ran. Dependency/tool installation failures before that step are
available in job logs, not a generated report. These workflow changes must be
pushed before GitHub produces the new reports.

Local reports are Git-ignored and each scope's next run replaces its previous
files. A successful report describes that run, not future source edits.

Build and security checks run on pushes to same-repository branches (except
`automation/**`). Same-repository PR events skip their jobs rather than repeating
the push checks; GitHub may still show a skipped workflow entry. Push results on
the PR's head commit supply its checks. Fork PRs retain read-only PR checks.
Push CI remains the publication trigger, including for feature branches.
Same-repository PR checks no longer test GitHub's synthetic merge commit; update
the branch from its target when you need to verify the combined source.

## 4. Local application development

Stage 0 prepares the workstation and cluster lifecycle. Stage 1 supplies the
first runnable applications and the local edit/build/deploy/test loop.
The Go API exposes `/healthz`, `/readyz`, and `/version`; ingress exposes them
under `/api`. The React page displays API health, readiness, and build identity.
Stage 2 adds PostgreSQL-backed `/api/payments` POST and `/api/payments/{id}` GET,
repeatable migrations, and durable idempotency. See
[the payment API guide](../modules/payment-api/README.md) for a synthetic curl
example and supported currencies; [OpenAPI](../modules/payment-api/openapi.yaml)
is the checked-in HTTP contract. Apicurio is introduced in Stage 3, not required
to define or test the HTTP contract now.

### Start and update

```bash
make doctor
make dev-up
make dev-access
```

Open `http://127.0.0.1:8080` in your browser. `dev-access` runs in the foreground
and binds only to loopback. No hosts-file changes, root-owned port binding,
Minikube tunnel, GitHub runner, or registry login is required.

After editing an application:

```bash
# In a second terminal; leave dev-access running.
make ci
make dev-update
```

Refresh the browser after the update. Smoke checks use an automatically assigned
temporary loopback port and stop only their own forwarding process afterward.
`LOCAL_PORT` selects the browser port for `dev-access` (default 8080); it does not
select the smoke-test port.
Smoke checks allow up to 30 seconds for transient ingress propagation failures
(HTTP 404/502/503/504, connection errors, or timeouts), logging retries and
returning nonzero if readiness never recovers. Invalid response bodies and stale
API build identities fail immediately. This is deployment verification, not
application-level database retry behavior.

`dev-up` starts Minikube, builds images using the host Docker daemon, loads them
into Minikube, initializes Terraform, plans/applies the stack, and runs smoke
checks. `dev-update` repeats build/load/deploy/smoke against an existing cluster.
Both commands fail explicitly if a build, image load, deployment, or smoke check
fails. They do not consult the PI schedule or release-reservation state.

The local Docker image store is not a registry. Minikube uses a separate
container runtime, so building on the host alone is insufficient: `dev-images`
loads every runnable module image into the selected profile.
Images use `rpp-local/<module>:dev-<content-hash>` and `imagePullPolicy: Never`.
The hash includes non-ignored module files (including dirty/untracked source),
the source commit, and tool pins. Editing source changes the image reference and triggers a rollout.
These tags are development identities, not stable catalog or PI release versions.

The catalog discovers runnable modules by the presence of both a Dockerfile and
a Helm chart. Catalog-only and non-runtime modules are not deployed.
The applications run non-root with read-only filesystems, restricted capabilities,
probes, and resource requests/limits.

### PostgreSQL, migrations, and integration tests

The local workflow now provisions a private PostgreSQL StatefulSet/service in
`data`, with a 1 GiB volume and an exact image tag from `.tool-versions`.
It generates a random 64-character password in private
`.local/<profile>/database.tfvars.json` (mode 0600), preserved across updates.
Terraform injects a database URL into an apps-namespace Secret; the API migration
init container applies embedded SQL before the runtime starts. Published charts
require an externally provisioned database and Secret; no credentials are
packaged. Local cluster connections use `sslmode=disable` and a database-owner
role for this educational environment, not production credential/TLS policy.

`make dev-update` preserves payments and reuses the migration ledger/password.
Losing the private credential file with existing database state is an explicit
recovery error, not an automatic password reset. Terraform state also contains
local secrets and must never be committed or shared.
`make dev-down` deletes namespaces and their PVCs/data. `make cluster-down`
deletes the selected cluster and all data. Do not use teardown as a restart.

```bash
make -C modules/payment-api build
make -C modules/payment-api test
make -C modules/payment-api integration
```

`test` remains offline after Go dependencies have been cached by a build.
`integration` starts its own disposable PostgreSQL container through the pinned
Testcontainers Go library, and fails rather than skips when Docker is unavailable.
The first integration run may download PostgreSQL and the Testcontainers cleanup
helper. No manual PostgreSQL CLI/server installation is needed.
The dynamic module CI passes `INTEGRATION=1` to enable this module's integration
target as part of its normal test contract; other modules keep their own Make
behavior. Root `make ci` remains cluster/Docker-independent for unit checks.

Migration SQL lives with the API. It is checksum-tracked and transactionally
serialized; never edit applied SQL or clear the ledger to force a rollout.
For database recovery, see [the runbook](runbooks/payment-database.md).

During a database outage, the API's direct `/healthz` remains 200 and direct
`/readyz` returns 503. Kubernetes then marks the API pod unready, so ingress can
return 503 for **all** `/api` routes, including health. Verify independent
liveness by forwarding directly to the API pod as described in the runbook,
not through `make dev-access`. Restoring PostgreSQL should recover readiness
without restarting the API. Only run deliberate outage tests on an isolated
profile; manual scaling is Terraform drift and is restored by a subsequent apply.

### Explicit Terraform workflow

For inspecting the infrastructure plan separately:

```bash
make cluster-up
make dev-images
make terraform-init
make terraform-validate
make terraform-plan
make terraform-apply
make smoke
```

`terraform-apply` calculates a fresh saved plan, then applies that exact plan.
Terraform owns the six namespaces, Traefik Helm release, application Helm
releases, and local PostgreSQL resources/Secrets. Do not also use manual
`helm upgrade` or `kubectl apply` to manage those
objects. Argo CD is not introduced until its roadmap stage.
Helm installs are atomic and upgrades clean up newly created resources on failure,
so an unsuccessful installation does not leave an untracked failed release.

Provider versions and checksums are pinned in the local Terraform environment;
the Traefik chart version is pinned there too. `make ci` validates Terraform and
lints/renders application charts without needing a running cluster.
TFLint runs as part of Terraform linting and therefore `make lint` / `make ci`.
Trivy is intentionally separate: run `make security` locally or inspect the
advisory security workflow. Reports are in `.local/security/`. Findings and scan
execution errors both remain visible as nonzero results; these jobs are not
intended to be required merge checks initially. Existing secret detection stays
in required quality checks. See [the security runbook](runbooks/security-checks.md).

### Profiles, ports, and state

The default Minikube profile is `rpp`, with 4 CPUs and 8 GiB RAM. To isolate a
development or verification environment, consistently pass a different profile:

```bash
make dev-up MINIKUBE_PROFILE=rpp-sandbox MINIKUBE_CPUS=2 MINIKUBE_MEMORY=4096
make dev-access MINIKUBE_PROFILE=rpp-sandbox LOCAL_PORT=8081
```

Resource overrides are intended for the minimal Stage 1 stack; later profiles
need more memory. Every local Kubernetes/Terraform command explicitly targets
the selected profile, not whichever kubeconfig context happened to be current.
Use a single kubeconfig file; `KUBECONFIG` may override its path.

Terraform state, provider working data, saved plans, and image inventory live in
git-ignored `.local/<profile>/`. Keep this directory until you have destroyed the
resources it owns. Do not commit it, copy state between profiles, or delete state
as a workaround for a failed apply. You can run `make smoke` while `dev-access`
is running; kubectl binds an available temporary port atomically for each check.

To close browser access, press Ctrl-C in the `dev-access` terminal; the deployed
applications keep running. `make dev-down` is not a pause command: it destroys
the Terraform-managed stack. Use it only when you intend to remove those
resources. You do not need it before updating applications.

### Recovery and clean-cluster acceptance

Remote CI/publication/deploy failures do not block these commands. Fix genuine
local build/test errors normally. Initial setup, npm/base-image downloads, provider
initialization, and chart downloads still require internet access; this is not
an air-gapped workflow.

To demonstrate pod recovery:

```bash
make dev-status
kubectl --context rpp -n apps delete pod -l app.kubernetes.io/name=payment-api
kubectl --context rpp -n apps rollout status deployment/payment-api --timeout=120s
make smoke
```

The deletion is intentional and affects only the selected API pod; its Deployment
recreates it. To verify complete reproducibility, use a dedicated profile:

```bash
make dev-up MINIKUBE_PROFILE=rpp-recreate
make dev-down MINIKUBE_PROFILE=rpp-recreate
make cluster-down MINIKUBE_PROFILE=rpp-recreate
make dev-up MINIKUBE_PROFILE=rpp-recreate
make smoke MINIKUBE_PROFILE=rpp-recreate
```

`dev-down` (also `terraform-destroy`) destroys all Terraform-managed resources in
that profile, including its application namespaces. `cluster-down` deletes the
whole cluster and its data. Never use this sequence on another person's profile.
Clean up the dedicated profile afterward using the same two teardown commands.

See [local development troubleshooting](runbooks/local-development.md) and
[ADR-001](adr/ADR-001-terraform-local-development.md) for ownership and trade-offs.

## 5. Workflow summary

```mermaid
flowchart LR
  A[git switch -c feature/name] --> B[edit]
  B --> C[git commit: pre-commit hooks run]
  C --> D[make ci]
  D --> E[git push, open PR]
  E --> F[Build workflow, squash merge]
```

Branch, commit, and versioning rules are in [CONTRIBUTING.md](../CONTRIBUTING.md).

Trusted OCI publication uses GitHub-hosted Buildx and the job's GitHub token.
Local contract tests and archive builds need only `make setup`; no new CLI is
required. GitHub approval/environment and GHCR configuration are covered in the
[publication runbook](release-publication.md).

## 6. Troubleshooting

For WSL networking and disk problems see the [WSL runbook](runbooks/wsl-troubleshooting.md).

| Problem | Fix |
|---|---|
| `make doctor` reports `PROBLEM ...` | Follow the hint printed on that line; most are fixed by `make setup` |
| `~/.local/bin is not on PATH` | Add it to `~/.bashrc` as shown above |
| `docker daemon is not reachable` | `sudo systemctl start docker`, or start Docker Desktop with WSL integration |
| `permission denied` on the Docker socket | Run `make docker`, or open a new terminal so the `docker` group applies |
| `python3 -m venv` fails | `sudo apt install python3-venv` |
| Catalog validation dependencies are missing | Run `make setup` to install the pinned libraries into `.venv/` |
| `systemd-binfmt.service failed` during `apt install` on WSL | Harmless: WSL already registers its own binfmt handler. Check with `sudo dpkg --audit`; no output means apt finished cleanly |
| `Permission denied (publickey)` on push | Check the key with `ssh -T git@github.com` and that the remote uses the SSH URL (`git remote -v`) |
| Hooks fail on line endings | Work in the WSL filesystem; the repo enforces LF via `.gitattributes` |
| Hook environments are broken or stale | `.venv/bin/pre-commit clean` (or `pre-commit clean`), then `make lint` |
