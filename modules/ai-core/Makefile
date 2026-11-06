SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean integration
ROOT := $(abspath ../..)
AI_PYTHON := bash $(ROOT)/scripts/ai-python.sh
INTEGRATION ?= 0

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Import every library module in the pinned AI runtime
	$(AI_PYTHON) run ai-core python -W error -c 'import ai_core.identity, ai_core.embeddings, ai_core.retrieval, ai_core.safety'

test: ## Run deterministic offline unit tests in the pinned AI runtime
	$(AI_PYTHON) run ai-core python -m unittest discover -s tests -p 'test_*.py'

ifeq ($(INTEGRATION),1)
test: integration
endif

lint: build ## Validate imports and syntax

integration: ## Check schema, roles and hybrid retrieval SQL against the pinned pgvector image
	$(AI_PYTHON) pgvector ai-core python -m unittest tests/pgvector_check.py

fmt: ## Check repository formatting for this module
	cd "$(ROOT)" && .venv/bin/pre-commit run --files $$(git ls-files --cached --others --exclude-standard modules/ai-core)

clean: ## Nothing to clean: checks run in a container with bytecode disabled
	@true
