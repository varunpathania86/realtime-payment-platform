# Root Makefile: runs the shared target contract across all sub-projects.
# Contract for every Makefile (root and sub-projects): build, test, lint, fmt, clean.

SHELL := /usr/bin/env bash
export PATH := $(HOME)/.local/bin:$(PATH)
.DEFAULT_GOAL := help

# Use a system pre-commit if present, otherwise the one installed by `make setup`.
VENV       := .venv
PRE_COMMIT := $(or $(shell command -v pre-commit),$(VENV)/bin/pre-commit)

# A sub-project is any directory below these roots that contains a Makefile.
PROJECT_ROOTS := apps packages streaming platform ml knowledge analytics contracts tests modules
SUBPROJECTS   := $(sort $(patsubst %/Makefile,%,$(wildcard $(addsuffix /*/Makefile,$(PROJECT_ROOTS)))))

.PHONY: help setup tools docker setup-agent agent-status remove-agent doctor validate-catalog test-catalog check-module publication-plan publish-module cluster-up cluster-status cluster-down projects build test lint fmt clean ci hooks version dev-up dev-update dev-images dev-access dev-status dev-down smoke terraform-init terraform-validate terraform-plan terraform-apply terraform-destroy

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
	@$(MAKE) --no-print-directory doctor

tools: ## Install or update the pinned CLI tools from .tool-versions
	scripts/install-tools.sh

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

publication-plan: ## Plan trusted OCI publication (requires workflow environment variables)
	$(VENV)/bin/python -m scripts.publish_modules plan

publish-module: ## Publish one approved module artifact (requires workflow environment variables)
	$(VENV)/bin/python -m scripts.publish_modules publish

cluster-up: ## Start the local Minikube cluster (idempotent)
	@scripts/cluster.sh up

cluster-status: ## Show the local cluster status
	@scripts/cluster.sh status

cluster-down: ## DESTRUCTIVE: delete the local Minikube cluster and its data
	@scripts/cluster.sh down

dev-up: ## Build/load local images and deploy the stack without GitHub or GHCR
	@scripts/local-dev.sh up

dev-update: ## Rebuild/load local images and update the existing local stack
	@scripts/local-dev.sh update

dev-images: ## Build catalog-discovered application images and load them into Minikube
	@scripts/local-dev.sh images

dev-access: ## Forward ingress to http://127.0.0.1:8080 (foreground; Ctrl-C stops)
	@scripts/local-dev.sh access

dev-status: ## Show local application resources
	@scripts/local-dev.sh status

dev-down: ## DESTRUCTIVE: destroy the local Terraform-managed stack, preserving Minikube
	@scripts/local-dev.sh down

smoke: ## Check API build identity and frontend through local ingress
	@scripts/local-dev.sh smoke

terraform-init: ## Initialize the profile-specific local Terraform backend
	@scripts/local-dev.sh init

terraform-validate: ## Validate Terraform without a running cluster
	@scripts/local-dev.sh validate

terraform-plan: ## Plan local infrastructure using the built image inventory
	@scripts/local-dev.sh plan

terraform-apply: ## Plan and apply local infrastructure using loaded images
	@scripts/local-dev.sh apply

terraform-destroy: dev-down ## DESTRUCTIVE: destroy the local Terraform-managed stack

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
