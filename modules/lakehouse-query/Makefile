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
	PYTHONPATH=$(ROOT) $(PYTHON) -m unittest discover -s $(ROOT)/tests/unit -p 'test_lakehouse_query.py'

lint: ## Validate the Trino chart
	helm lint --strict chart

fmt: ## Check shared whitespace rules
	cd $(ROOT) && .venv/bin/pre-commit run --files modules/lakehouse-query/Makefile modules/lakehouse-query/.dockerignore modules/lakehouse-query/.trivyignore.yaml modules/lakehouse-query/Dockerfile modules/lakehouse-query/README.md modules/lakehouse-query/chart/README.md modules/lakehouse-query/chart/Chart.yaml modules/lakehouse-query/chart/values.yaml modules/lakehouse-query/chart/templates/config.yaml modules/lakehouse-query/chart/templates/deployment.yaml modules/lakehouse-query/chart/templates/service.yaml

clean: ## No generated build artifacts
	@:
