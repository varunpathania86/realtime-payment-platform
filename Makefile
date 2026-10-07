# Root Makefile: runs the shared target contract across all sub-projects.
# Contract for every Makefile (root and sub-projects): build, test, lint, fmt, clean.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

# Use a system pre-commit if present, otherwise the one installed by `make setup`.
VENV       := .venv
PRE_COMMIT := $(or $(shell command -v pre-commit),$(VENV)/bin/pre-commit)

# A sub-project is any directory below these roots that contains a Makefile.
PROJECT_ROOTS := apps packages streaming platform ml knowledge analytics contracts tests
SUBPROJECTS   := $(sort $(patsubst %/Makefile,%,$(wildcard $(addsuffix /*/Makefile,$(PROJECT_ROOTS)))))

.PHONY: help setup tools doctor cluster-up cluster-status cluster-down projects build test lint fmt clean ci hooks version

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Set up the developer environment (see docs/developer-setup.md)
	@command -v python3 >/dev/null || { echo "python3 is required: see docs/developer-setup.md"; exit 1; }
	@command -v pre-commit >/dev/null || [ -x $(VENV)/bin/pre-commit ] || { \
	echo "Installing pre-commit into $(VENV)"; python3 -m venv $(VENV) && $(VENV)/bin/pip install --quiet pre-commit; }
	$(PRE_COMMIT) install
	scripts/install-tools.sh
	@$(MAKE) --no-print-directory doctor

tools: ## Install or update the pinned CLI tools from .tool-versions
	scripts/install-tools.sh

doctor: ## Check the environment against .tool-versions
	@scripts/doctor.sh

cluster-up: ## Start the local Minikube cluster (idempotent)
	@scripts/cluster.sh up

cluster-status: ## Show the local cluster status
	@scripts/cluster.sh status

cluster-down: ## DESTRUCTIVE: delete the local Minikube cluster and its data
	@scripts/cluster.sh down

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

ci: lint test build ## Everything the Build workflow runs

hooks: ## Install git pre-commit hooks
	$(PRE_COMMIT) install

version: ## Print the latest git tag
	@git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0 (no tags yet)"
