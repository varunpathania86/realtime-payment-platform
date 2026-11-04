SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean integration risk-integration risk-ci
PYTHON := $(abspath ../../.venv/bin/python)
INTEGRATION ?= 0
MODEL_DIRECTORY ?= $(abspath ../../.local/models/payment-risk/1.0.0)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Compile job sources without requiring a running cluster
	$(PYTHON) -m py_compile analytics.py benchmark.py configuration.py processing.py fixtures.py job.py runtime.py control.py risk.py model_registry.py

test: ## Run deterministic processing tests offline
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

ifeq ($(INTEGRATION),1)
test: risk-ci
endif

lint: ## Validate Python syntax and module packaging
	$(PYTHON) -m py_compile analytics.py benchmark.py configuration.py processing.py fixtures.py job.py runtime.py control.py risk.py model_registry.py

integration: ## Check the actual PyFlink operator using the setup-built runtime
	docker run --rm --entrypoint python -e PYTHONPATH=/opt/job -v "$(CURDIR):/opt/job:ro" \
		"rpp-tools/flink:$$(awk '$$1 == "flink-python" {print $$2}' ../../.tool-versions)" /opt/job/tests/operator_check.py
	docker run --rm --entrypoint python -e PYTHONPATH=/opt/job -v "$(CURDIR):/opt/job:ro" \
		"rpp-tools/flink:$$(awk '$$1 == "flink-python" {print $$2}' ../../.tool-versions)" /opt/job/tests/local_execution.py
	docker run --rm --entrypoint python -e PYTHONPATH=/opt/job -e KAFKA_BROKERS=localhost:9092 \
		-e ANALYTICS_ENABLED=true -e CLICKHOUSE_URL=http://clickhouse-analytics:8123 \
		-e CLICKHOUSE_USER=rpp_analytics -e CLICKHOUSE_PASSWORD=test-password \
		-e CHECKPOINT_URI=file:///tmp/flink-checkpoints -v "$(CURDIR):/opt/job:ro" \
		"rpp-tools/flink:$$(awk '$$1 == "flink-python" {print $$2}' ../../.tool-versions)" /opt/job/tests/graph_check.py

risk-integration: ## Verify exported model golden parity and revisions in the real Flink worker
	docker run --rm --entrypoint python -e PYTHONPATH=/opt/job:/models/payment_risk.zip \
		-e RISK_TEST_PACKAGE=/models/payment_risk.zip -v "$(CURDIR):/opt/job:ro" \
		-v "$(abspath $(MODEL_DIRECTORY)):/models:ro" \
		"rpp-tools/flink:$$(awk '$$1 == "flink-python" {print $$2}' ../../.tool-versions)" /opt/job/tests/risk_execution.py

risk-ci: ## Prepare pinned runtimes and run baseline plus three-way model parity for affected-module CI
	$(MAKE) -C ../payment-risk-model integration
	$(MAKE) integration

fmt: ## Check shared whitespace rules without rewriting unrelated code
	../../.venv/bin/pre-commit run --files modules/flink-jobs/analytics.py modules/flink-jobs/processing.py modules/flink-jobs/fixtures.py modules/flink-jobs/job.py modules/flink-jobs/tests/test_analytics.py

clean: ## Remove compiled job caches only
	$(PYTHON) -c 'import pathlib; [p.unlink() for p in pathlib.Path("__pycache__").glob("*.pyc")]'
