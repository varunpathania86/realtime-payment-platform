SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean
ROOT := $(abspath ../..)
AI_PYTHON := bash $(ROOT)/scripts/ai-python.sh
PYTHON := $(ROOT)/.venv/bin/python

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Import the MCP server in the pinned AI runtime and validate the chart contract
	$(AI_PYTHON) run assistant-tools python -W error -c 'import assistant_tools.server'
	helm lint chart --strict
	helm template assistant-tools chart --namespace apps | $(PYTHON) $(ROOT)/scripts/check_local_chart.py

test: ## Run offline tool, backend and MCP round-trip tests in the pinned AI runtime
	$(AI_PYTHON) run assistant-tools python -m unittest discover -s tests -p 'test_*.py'

lint: build ## Validate imports and the chart

fmt: ## Check repository formatting for this module
	cd "$(ROOT)" && .venv/bin/pre-commit run --files $$(git ls-files --cached --others --exclude-standard modules/assistant-tools)

clean: ## Nothing to clean: checks run in a container with bytecode disabled
	@true
