SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean benchmark integration
ROOT := ../..
PYTHON := $(abspath $(ROOT)/.venv/bin/python)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate the packaged ClickHouse chart
	helm lint --strict chart
	$(PYTHON) -m py_compile benchmark.py tests/runtime_check.py

test: ## Run offline chart and analytics package tests
	PYTHONPATH=$(ROOT) $(PYTHON) -m unittest discover -s $(ROOT)/tests/unit -p 'test_realtime_analytics.py'

lint: ## Validate the packaged ClickHouse chart
	helm lint --strict chart
	$(PYTHON) -m py_compile benchmark.py tests/runtime_check.py

fmt: ## Check shared whitespace rules
	cd $(ROOT) && .venv/bin/pre-commit run --files scripts/clickhouse-integration.sh modules/realtime-analytics/Makefile modules/realtime-analytics/.dockerignore modules/realtime-analytics/.trivyignore.yaml modules/realtime-analytics/Dockerfile modules/realtime-analytics/README.md modules/realtime-analytics/benchmark.py modules/realtime-analytics/tests/runtime_check.py modules/realtime-analytics/chart/Chart.yaml modules/realtime-analytics/chart/values.yaml modules/realtime-analytics/chart/files/schema.sql modules/realtime-analytics/chart/templates/config.yaml modules/realtime-analytics/chart/templates/schema-job.yaml modules/realtime-analytics/chart/templates/service.yaml modules/realtime-analytics/chart/templates/statefulset.yaml modules/realtime-analytics/chart/templates/storage.yaml

benchmark: ## Run a bounded local query-latency benchmark
	$(PYTHON) benchmark.py

integration: ## Build and exercise the pinned ClickHouse image in an isolated container
	bash $(ROOT)/scripts/clickhouse-integration.sh

clean: ## No generated build artifacts
	@:
