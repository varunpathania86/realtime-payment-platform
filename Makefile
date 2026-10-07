# Root Makefile: runs the shared target contract across all sub-projects.
# Contract for every Makefile (root and sub-projects): build, test, lint, fmt, clean.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

# A sub-project is any directory below these roots that contains a Makefile.
PROJECT_ROOTS := apps packages streaming platform ml knowledge analytics contracts tests
SUBPROJECTS   := $(sort $(patsubst %/Makefile,%,$(wildcard $(addsuffix /*/Makefile,$(PROJECT_ROOTS)))))

.PHONY: help doctor projects build test lint fmt clean ci hooks version

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

doctor: ## Check that required tools are installed
	@ok=1; for t in git make python3 pre-commit; do \
	if command -v $$t >/dev/null; then echo "ok       $$t"; else echo "MISSING  $$t"; ok=0; fi; \
	done; [ $$ok = 1 ]

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
	pre-commit run --all-files
	@for p in $(SUBPROJECTS); do echo "==> $$p: lint"; $(MAKE) -C $$p lint || exit 1; done

ci: lint test build ## Everything the Build workflow runs

hooks: ## Install git pre-commit hooks
	pre-commit install

version: ## Print the latest git tag
	@git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0 (no tags yet)"
