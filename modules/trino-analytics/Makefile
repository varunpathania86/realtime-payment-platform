SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean
ROOT := ../..
PYTHON := $(abspath $(ROOT)/.venv/bin/python)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate the Trino chart
	helm lint --strict chart

test: ## Run offline chart and catalog configuration tests
	PYTHONPATH=$(ROOT) $(PYTHON) -m unittest discover -s $(ROOT)/tests/unit -p 'test_trino_analytics.py'

lint: ## Validate the Trino chart
	helm lint --strict chart

fmt: ## Check shared whitespace rules
	cd $(ROOT) && .venv/bin/pre-commit run --files modules/trino-analytics/Makefile modules/trino-analytics/.dockerignore modules/trino-analytics/.trivyignore.yaml modules/trino-analytics/Dockerfile modules/trino-analytics/README.md modules/trino-analytics/chart/README.md modules/trino-analytics/chart/Chart.yaml modules/trino-analytics/chart/values.yaml modules/trino-analytics/chart/templates/config.yaml modules/trino-analytics/chart/templates/deployment.yaml modules/trino-analytics/chart/templates/service.yaml

clean: ## No generated build artifacts
	@:
