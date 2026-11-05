SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean integration
ROOT := $(abspath ../..)
AI_PYTHON := bash $(ROOT)/scripts/ai-python.sh
INTEGRATION ?= 0

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Import the ingestion package and validate the evaluation set in the pinned AI runtime
	$(AI_PYTHON) run operations-knowledge python -W error -c 'import operations_knowledge.ingest, operations_knowledge.evaluation as e; e.load_cases("evaluation/cases.yaml")'

test: ## Run deterministic offline unit tests in the pinned AI runtime
	$(AI_PYTHON) run operations-knowledge python -m unittest discover -s tests -p 'test_*.py'

ifeq ($(INTEGRATION),1)
test: integration
endif

lint: build ## Validate imports, manifest-independent code and evaluation metadata

integration: ## Check versioned ingestion and quarantine against the pinned pgvector image
	$(AI_PYTHON) pgvector operations-knowledge python -m unittest tests/ingestion_check.py

fmt: ## Check repository formatting for this module
	cd "$(ROOT)" && .venv/bin/pre-commit run --files $$(git ls-files --cached --others --exclude-standard modules/operations-knowledge)

clean: ## Nothing to clean: checks run in a container with bytecode disabled
	@true
