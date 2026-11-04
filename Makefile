# Root Makefile: runs the shared target contract across all sub-projects.
# Contract for every Makefile (root and sub-projects): build, test, lint, fmt, clean.

SHELL := /usr/bin/env bash
export PATH := $(HOME)/.local/bin:$(PATH)
.DEFAULT_GOAL := help
.PHONY: security security-solution security-module security-plan publication-login quality-report quality-module-report release-baseline
.PHONY: dev-metrics dev-observability db-switchover db-failover db-rejoin
.PHONY: streaming-fixtures streaming-inspect streaming-load streaming-savepoint streaming-stop streaming-benchmark streaming-benchmark-preflight

# Use a system pre-commit if present, otherwise the one installed by `make setup`.
VENV       := .venv
PRE_COMMIT := $(or $(shell command -v pre-commit),$(VENV)/bin/pre-commit)

# A sub-project is any directory below these roots that contains a Makefile.
PROJECT_ROOTS := apps packages streaming platform ml knowledge analytics contracts tests modules
SUBPROJECTS   := $(sort $(patsubst %/Makefile,%,$(wildcard $(addsuffix /*/Makefile,$(PROJECT_ROOTS)))))

.PHONY: help setup tools docker setup-agent agent-status remove-agent doctor validate-catalog test-catalog check-module publication-plan publish-module cluster-down projects build test lint fmt clean ci hooks version dev-up dev-update dev-access dev-status dev-down smoke

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Set up the developer environment (see docs/developer-setup.md)
	@command -v python3 >/dev/null || { echo "python3 is required: see docs/developer-setup.md"; exit 1; }
	@test -x $(VENV)/bin/python || python3 -m venv $(VENV)
	@$(VENV)/bin/pip install --quiet -r scripts/requirements-catalog.txt
	@command -v pre-commit >/dev/null || [ -x $(VENV)/bin/pre-commit ] || { \
	echo "Installing pre-commit into $(VENV)"; $(VENV)/bin/pip install --quiet pre-commit; }
	$(PRE_COMMIT) install
	scripts/install-tools.sh
	scripts/install-docker.sh
	bash scripts/setup-multicluster.sh
	bash scripts/setup-cnpg.sh
	bash scripts/setup-streaming.sh
	@$(MAKE) --no-print-directory doctor

tools: ## Install or update the pinned CLI tools from .tool-versions
	scripts/install-tools.sh $(TOOLS)

docker: ## Install Docker Engine if missing (needs sudo)
	scripts/install-docker.sh

setup-agent: ## Register this machine as a self-hosted GitHub Actions runner (for Deploy)
	scripts/agent.sh setup

agent-status: ## Show the self-hosted runner status
	@scripts/agent.sh status

remove-agent: ## Unregister and delete the self-hosted runner
	scripts/agent.sh remove

doctor: ## Check the environment against .tool-versions
	@scripts/doctor.sh

validate-catalog: ## Validate solution, module, and release manifests against JSON Schemas
	$(VENV)/bin/python scripts/module_catalog.py validate

test-catalog: ## Run catalog validation and change-planning unit tests
	$(VENV)/bin/python -m unittest discover -s tests/unit -p 'test_*.py'

check-module: ## Run the target contract for MODULE_NAME (used by the CI matrix)
	$(VENV)/bin/python scripts/module_catalog.py run-module --name "$(MODULE_NAME)"

security: ## Scan solution and packaged modules with Trivy (separate from make ci)
	$(VENV)/bin/python -m scripts.security_checks all

quality-report: ## Run make ci and retain a Markdown report plus full log
	$(VENV)/bin/python -m scripts.check_reports

quality-module-report: ## Run check-module for MODULE_NAME and retain Markdown/log output
	$(VENV)/bin/python -m scripts.check_reports --module "$(MODULE_NAME)"

security-solution: ## Scan shared platform IaC and write JSON and Markdown reports
	$(VENV)/bin/python -m scripts.security_checks solution

security-module: ## Scan MODULE_NAME packaging and write a JSON report
	$(VENV)/bin/python -m scripts.security_checks module --module "$(MODULE_NAME)"

security-plan: ## Discover a catalog-driven IaC security matrix
	$(VENV)/bin/python -m scripts.security_checks plan

publication-plan: ## Plan trusted OCI publication (requires workflow environment variables)
	$(VENV)/bin/python -m scripts.publish_modules plan

publish-module: ## Publish one approved module artifact (requires workflow environment variables)
	$(VENV)/bin/python -m scripts.publish_modules publish

release-baseline: ## Promote module/solution iteration, RC or stable artifacts (protected workflow only)
	$(VENV)/bin/python -m scripts.release_baseline

publication-login: ## Authenticate Helm to GHCR using the job token through stdin
	@printf '%s' "$$GH_TOKEN" | helm registry login ghcr.io --username "$$GITHUB_ACTOR" --password-stdin

dev-up: ## Start rpp-a/b/c (optional DEV_PROFILE and DEV_CONFIG overrides)
	@bash scripts/multi-cluster.sh up

dev-update: ## Rebuild/load applications and update all active clusters, retaining data
	@bash scripts/multi-cluster.sh update

dev-status: ## Show cluster/container CPU, RAM, readiness, requests and limits
	@bash scripts/multi-cluster.sh status

dev-metrics: ## Enable bundled metrics-server on the three owned clusters
	@bash scripts/multi-cluster.sh metrics

dev-observability: ## Forward opt-in Grafana to http://127.0.0.1:3000 (foreground)
	@bash scripts/multi-cluster.sh observability

streaming-fixtures: ## Send deterministic synthetic processing events to the selected cluster
	$(VENV)/bin/python scripts/streaming-control.py fixtures

streaming-inspect: ## Inspect current compacted window results and too-late output
	$(VENV)/bin/python scripts/streaming-control.py inspect

streaming-load: ## Produce bounded synthetic load for TaskManager recovery verification
	$(VENV)/bin/python scripts/streaming-control.py load

streaming-benchmark-preflight: ## Check host memory before starting the three-cluster analytics benchmark
	$(VENV)/bin/python -m scripts.streaming_benchmark --base "$(or $(DEV_PROFILE),rpp)" --preflight --config "$(or $(DEV_CONFIG),templates/analytics-benchmark-topology.json)"

streaming-benchmark: ## Measure 50 events/s baseline and 100 events/s stress on three running clusters
	$(VENV)/bin/python -m scripts.streaming_benchmark --base "$(or $(DEV_PROFILE),rpp)"

streaming-savepoint: ## Create a persistent savepoint for the selected Flink job
	$(VENV)/bin/python scripts/streaming-control.py savepoint

streaming-stop: ## Stop the selected Flink job with a persistent savepoint (no drain)
	$(VENV)/bin/python scripts/streaming-control.py stop

db-switchover: ## Move the CNPG writer using demotion/promotion tokens (DATABASE_TARGET=pg-b)
	@bash scripts/multi-cluster.sh switchover

db-failover: ## Fence the old primary and verify durable WAL before promotion (DATABASE_TARGET=pg-b)
	@bash scripts/multi-cluster.sh failover

db-rejoin: ## DESTRUCTIVE: recreate the fenced owned profile and clone the current database
	@bash scripts/multi-cluster.sh rejoin

dev-down: ## DESTRUCTIVE: destroy the owned stack and all data, retaining cluster nodes
	@bash scripts/multi-cluster.sh down

cluster-down: ## DESTRUCTIVE: delete three owned clusters after successful dev-down
	@bash scripts/multi-cluster.sh cluster-down

dev-access: ## Forward ingress to http://127.0.0.1:8080 (foreground; Ctrl-C stops)
	@MINIKUBE_PROFILE="$${MINIKUBE_PROFILE:-$${DEV_PROFILE:-rpp}-a}" scripts/local-dev.sh access

smoke: ## Check API build identity and frontend through local ingress
	@MINIKUBE_PROFILE="$${MINIKUBE_PROFILE:-$${DEV_PROFILE:-rpp}-a}" scripts/local-dev.sh smoke

projects: ## List discovered sub-projects
	@if [ -z "$(SUBPROJECTS)" ]; then echo "(none yet)"; else printf '%s\n' $(SUBPROJECTS); fi

define run_in_projects
@for p in $(SUBPROJECTS); do echo "==> $$p: $(1)"; $(MAKE) -C $$p $(1) || exit 1; done
endef

build: ## Build every sub-project
	$(call run_in_projects,build)

test: ## Run unit tests in every sub-project
	$(call run_in_projects,test)

fmt: ## Format every sub-project
	$(call run_in_projects,fmt)

clean: ## Clean every sub-project
	$(call run_in_projects,clean)

lint: ## Run shared static checks, then every sub-project's lint
	$(PRE_COMMIT) run --all-files
	@for p in $(SUBPROJECTS); do echo "==> $$p: lint"; $(MAKE) -C $$p lint || exit 1; done

ci: lint validate-catalog test-catalog test build ## Everything the Build workflow runs

hooks: ## Install git pre-commit hooks
	$(PRE_COMMIT) install

version: ## Print the latest git tag
	@git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0 (no tags yet)"
