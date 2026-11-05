SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean
ROOT := $(abspath ../..)
PYTHON := $(ROOT)/.venv/bin/python

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Render the chart and validate the local deployment contract
	helm lint chart --strict
	helm template llm-serving chart --namespace apps | $(PYTHON) $(ROOT)/scripts/check_local_chart.py

test: ## Check the model fetch script offline with file:// fixtures
	bash tests/fetch_model_test.sh

lint: build ## Lint the chart and shell scripts
	cd "$(ROOT)" && .venv/bin/pre-commit run shellcheck --files modules/llm-serving/scripts/fetch-model.sh modules/llm-serving/tests/fetch_model_test.sh

fmt: ## Check repository formatting for this module
	cd "$(ROOT)" && .venv/bin/pre-commit run --files $$(git ls-files --cached --others --exclude-standard modules/llm-serving)

clean: ## Nothing to clean
	@true
