# Root Makefile: runs the shared target contract across all sub-projects.
# Contract for every Makefile (root and sub-projects): build, test, lint, fmt, clean.

SHELL := /usr/bin/env bash
export PATH := $(HOME)/.local/bin:$(PATH)
.DEFAULT_GOAL := help
.PHONY: security security-solution security-module security-plan publication-login quality-report quality-module-report release-baseline
.PHONY: dev-metrics dev-observability db-switchover db-failover db-rejoin
.PHONY: identity-integration identity-sync identity-access identity-verify
.PHONY: dev-clean access-local dbx-access dbx-status minio-access
.PHONY: streaming-fixtures streaming-inspect streaming-load streaming-savepoint streaming-stop streaming-benchmark streaming-benchmark-preflight risk-publish risk-verify lakehouse-query lakehouse-verify lakehouse-maintain bi-verify knowledge-ingest ai-fixtures ai-eval

# Use a system pre-commit if present, otherwise the one installed by `make setup`.
VENV       := .venv
PRE_COMMIT := $(or $(shell command -v pre-commit),$(VENV)/bin/pre-commit)
MULTI_CLUSTER ?= 0
ifneq ($(origin AI_GPU),undefined)
ifneq (x$(AI_GPU)x,x0x)
ifneq (x$(AI_GPU)x,x1x)
$(error AI_GPU must be 0 or 1)
endif
endif
endif
ifeq ($(MULTI_CLUSTER),3)
ifneq ($(strip $(AI_GPU)),)
$(error Use ai_gpu in DEV_CONFIG for MULTI_CLUSTER=3; AI_GPU selects the single-cluster AI workload)
endif
endif
# Sentinels preserve trailing whitespace so malformed selectors cannot take the wrong branch.
ifneq (x$(MULTI_CLUSTER)x,x0x)
ifneq (x$(MULTI_CLUSTER)x,x3x)
$(error MULTI_CLUSTER must be 0 or 3)
endif
endif
ifneq ($(strip $(DEV_PROFILE)),)
$(error DEV_PROFILE is no longer supported; use MULTI_CLUSTER=3)
endif
ifneq ($(strip $(MINIKUBE_PROFILE)),)
ifeq ($(MULTI_CLUSTER),0)
ifneq ($(MINIKUBE_PROFILE),rpp)
$(error MULTI_CLUSTER=0 supports only MINIKUBE_PROFILE=rpp)
endif
else
ifneq ($(words $(MINIKUBE_PROFILE)),1)
$(error MULTI_CLUSTER=3 accepts one MINIKUBE_PROFILE at a time)
endif
ifneq ($(filter $(MINIKUBE_PROFILE),rpp-1 rpp-2 rpp-3),$(MINIKUBE_PROFILE))
$(error MULTI_CLUSTER=3 supports only MINIKUBE_PROFILE=rpp-1, rpp-2, or rpp-3)
endif
endif
endif
LOCAL_PROFILE = $(or $(MINIKUBE_PROFILE),$(if $(filter 3,$(MULTI_CLUSTER)),rpp-1,rpp))

# A sub-project is any directory below these roots that contains a Makefile.
PROJECT_ROOTS := apps packages streaming platform ml knowledge analytics contracts tests modules
SUBPROJECTS   := $(sort $(patsubst %/Makefile,%,$(wildcard $(addsuffix /*/Makefile,$(PROJECT_ROOTS)))))

.PHONY: help setup tools docker setup-agent agent-status remove-agent doctor validate-catalog test-catalog check-module publication-plan publish-module projects build test lint fmt clean ci hooks version dev-up dev-update dev-access dev-status dev-down smoke

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
	bash scripts/setup-risk.sh
	bash scripts/ai-python.sh setup
	bash scripts/setup-gpu.sh
	bash scripts/identity-runtime.sh setup
	bash scripts/dbx-runtime.sh setup
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

identity-integration: ## Test real Keycloak code+PKCE and permissions in isolated disposable containers
	$(VENV)/bin/python tests/integration/identity_provider_check.py

identity-access: ## Forward Keycloak administration to loopback only (leave this terminal running)
	kubectl --context "$(LOCAL_PROFILE)" -n security port-forward --address 127.0.0.1 service/identity-provider "$(or $(IDENTITY_PORT),18081):8080"

identity-sync: ## Explicitly reconcile the managed realm on an existing identity database
	$(VENV)/bin/python -m scripts.identity_reconcile --context "$(LOCAL_PROFILE)" --url "$(or $(IDENTITY_URL),http://127.0.0.1:18081)"

identity-verify: ## Verify live Keycloak SSO, resource authorization, logout, expiry and signing-key rotation
	$(VENV)/bin/python -m scripts.identity_acceptance --profile "$(LOCAL_PROFILE)"

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

access-local: ## Forward deployed data services to loopback ports for desktop clients (foreground; Ctrl-C stops)
	$(VENV)/bin/python -m scripts.resource_access --profile "$(LOCAL_PROFILE)"

dbx-access: ## Forward optional in-cluster DBX to http://127.0.0.1:44224 (foreground; Ctrl-C stops)
	@MINIKUBE_PROFILE="$(LOCAL_PROFILE)" DBX_LOCAL_PORT="$(or $(DBX_LOCAL_PORT),44224)" bash scripts/local-dev.sh dbx-access

dbx-status: ## Check the selected profile's optional DBX rollout
	@MINIKUBE_PROFILE="$(LOCAL_PROFILE)" bash scripts/local-dev.sh dbx-status

minio-access: ## Forward only the MinIO console to http://127.0.0.1:19091 (foreground; Ctrl-C stops)
	kubectl --context "$(LOCAL_PROFILE)" -n data port-forward --address 127.0.0.1 service/minio 19091:9001

dev-up: ## Start rpp, or rpp-1/2/3 with MULTI_CLUSTER=3
	@if [ -n "$(DEV_CONFIG)" ] && [ "$(MULTI_CLUSTER)" != 3 ]; then \
		echo "DEV_CONFIG requires MULTI_CLUSTER=3" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ] && [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "Do not select a single profile for a multi-cluster start" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ]; then bash scripts/multi-cluster.sh up; \
	else MINIKUBE_PROFILE="$(LOCAL_PROFILE)" bash scripts/run-local.sh up; fi

dev-update: ## Rebuild/load the default single cluster, or a selected topology
	@if [ -n "$(DEV_CONFIG)" ] && [ "$(MULTI_CLUSTER)" != 3 ]; then \
		echo "DEV_CONFIG requires MULTI_CLUSTER=3" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ] && [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "Do not select a single profile for a multi-cluster update" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ]; then \
		bash scripts/multi-cluster.sh update; \
	else MINIKUBE_PROFILE="$(LOCAL_PROFILE)" bash scripts/local-dev.sh update; fi

dev-status: ## Show selected cluster readiness
	@if [ "$(MULTI_CLUSTER)" = 3 ] && [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "Do not select a single profile for multi-cluster status" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ]; then \
		bash scripts/multi-cluster.sh status; \
	else MINIKUBE_PROFILE="$(LOCAL_PROFILE)" bash scripts/cluster.sh status && \
		kubectl --context "$(LOCAL_PROFILE)" get pods -A; fi

dev-metrics: ## Enable bundled metrics-server on the three owned clusters
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "dev-metrics requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	@bash scripts/multi-cluster.sh metrics

dev-observability: ## Forward opt-in Grafana to http://127.0.0.1:3000 (foreground)
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "dev-observability requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	@bash scripts/multi-cluster.sh observability

streaming-fixtures: ## Send deterministic synthetic processing events to the selected cluster
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/streaming-control.py fixtures

risk-publish: ## Publish immutable trained model artifacts to the explicitly selected profile's MinIO
	$(VENV)/bin/python -m scripts.risk_model --profile "$(LOCAL_PROFILE)" --directory "$(or $(MODEL_DIRECTORY),.local/models/payment-risk/1.0.0)"

risk-verify: ## Verify advisory predictions, late-history revision and model telemetry on the selected profile
	$(VENV)/bin/python -m scripts.risk_acceptance --profile "$(LOCAL_PROFILE)"

bi-verify: ## Verify embedded BI access boundaries (viewer, denied clerk, guest-token scope, CSP) on the selected profile
	$(VENV)/bin/python -m scripts.bi_acceptance --profile "$(LOCAL_PROFILE)"

knowledge-ingest: ## Bundle the operations knowledge corpus and run one ingestion job on the selected profile
	$(VENV)/bin/python -m scripts.ai_platform ingest --profile "$(LOCAL_PROFILE)"

ai-fixtures: ## Replace synthetic ClickHouse payment facts and advisory predictions for assistant tools
	$(VENV)/bin/python -m scripts.ai_platform fixtures --profile "$(LOCAL_PROFILE)"

ai-eval: ## Run the starter assistant evaluation through the portal; fails on authorization leaks
	$(VENV)/bin/python -m scripts.ai_platform eval --profile "$(LOCAL_PROFILE)"

# Raw value, single-quoted for the shell, so Iceberg names like "t$snapshots" and quotes survive.
LAKEHOUSE_SQL = $(subst ','\'',$(value SQL))

lakehouse-query: ## Run read-only Trino SQL against the lakehouse (SQL="SELECT ...")
	@test -n '$(LAKEHOUSE_SQL)' || { echo "Set SQL, for example SQL='SELECT count(*) FROM payment_events'" >&2; exit 2; }
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/lakehouse.py query '$(LAKEHOUSE_SQL)'

lakehouse-verify: ## Verify lakehouse rows, deduplication, time travel, read-only access and evolution
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/lakehouse.py verify

lakehouse-maintain: ## Compact files and expire snapshots/orphans (LAKEHOUSE_RETENTION, default 7d)
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/lakehouse.py maintain --retention "$(or $(LAKEHOUSE_RETENTION),7d)"

streaming-inspect: ## Inspect current compacted window results and too-late output
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/streaming-control.py inspect

streaming-load: ## Produce bounded synthetic load for TaskManager recovery verification
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/streaming-control.py load

streaming-benchmark-preflight: ## Check host memory before starting the three-cluster analytics benchmark
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "streaming-benchmark-preflight requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	$(VENV)/bin/python -m scripts.streaming_benchmark --base rpp --preflight --config "$(or $(DEV_CONFIG),templates/analytics-benchmark-topology.json)"

streaming-benchmark: ## Measure 50 events/s baseline and 100 events/s stress on three running clusters
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "streaming-benchmark requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	$(VENV)/bin/python -m scripts.streaming_benchmark --base rpp

streaming-savepoint: ## Create a persistent savepoint for the selected Flink job
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/streaming-control.py savepoint

streaming-stop: ## Stop the selected Flink job with a persistent savepoint (no drain)
	MINIKUBE_PROFILE="$(LOCAL_PROFILE)" $(VENV)/bin/python scripts/streaming-control.py stop

db-switchover: ## Move the CNPG writer using demotion/promotion tokens (DATABASE_TARGET=pg-2)
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "db-switchover requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	@bash scripts/multi-cluster.sh switchover

db-failover: ## Fence the old primary and verify durable WAL before promotion (DATABASE_TARGET=pg-2)
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "db-failover requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	@bash scripts/multi-cluster.sh failover

db-rejoin: ## DESTRUCTIVE: recreate the fenced owned profile and clone the current database
	@if [ "$(MULTI_CLUSTER)" != 3 ] || [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "db-rejoin requires MULTI_CLUSTER=3 without MINIKUBE_PROFILE" >&2; exit 2; \
	fi
	@bash scripts/multi-cluster.sh rejoin

dev-down: ## Stop the selected cluster(s), preserving data
	@if [ "$(MULTI_CLUSTER)" = 3 ] && [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "Do not select a single profile for a multi-cluster stop" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ]; then bash scripts/multi-cluster.sh stop; \
	else MINIKUBE_PROFILE="$(LOCAL_PROFILE)" bash scripts/run-local.sh stop; fi

dev-clean: ## DESTRUCTIVE: delete the selected cluster(s) and their data
	@if [ "$(MULTI_CLUSTER)" = 3 ] && [ -n "$(MINIKUBE_PROFILE)" ]; then \
		echo "Do not select a single profile for multi-cluster cleanup" >&2; exit 2; \
	elif [ "$(MULTI_CLUSTER)" = 3 ]; then \
		bash scripts/multi-cluster.sh down && bash scripts/multi-cluster.sh cluster-down; \
	else MINIKUBE_PROFILE="$(LOCAL_PROFILE)" bash scripts/run-local.sh clean; fi

dev-access: ## Forward ingress to http://127.0.0.1:8080 (foreground; Ctrl-C stops)
	@MINIKUBE_PROFILE="$(LOCAL_PROFILE)" scripts/local-dev.sh access

smoke: ## Check API build identity and frontend through local ingress
	@MINIKUBE_PROFILE="$(LOCAL_PROFILE)" scripts/local-dev.sh smoke

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
