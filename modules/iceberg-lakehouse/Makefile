SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean
ROOT := ../..
PYTHON := $(abspath $(ROOT)/.venv/bin/python)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate the Polaris chart and setup script
	helm lint --strict chart
	$(PYTHON) -m py_compile setup_catalog.py

test: ## Run offline chart, SigV4 and catalog convergence tests
	PYTHONPATH=$(ROOT) $(PYTHON) -m unittest discover -s $(ROOT)/tests/unit -p 'test_iceberg_lakehouse.py'

lint: ## Validate the Polaris chart and setup script
	helm lint --strict chart
	$(PYTHON) -m py_compile setup_catalog.py

fmt: ## Check shared whitespace rules
	cd $(ROOT) && .venv/bin/pre-commit run --files modules/iceberg-lakehouse/Makefile modules/iceberg-lakehouse/.dockerignore modules/iceberg-lakehouse/.trivyignore.yaml modules/iceberg-lakehouse/Dockerfile modules/iceberg-lakehouse/README.md modules/iceberg-lakehouse/setup_catalog.py modules/iceberg-lakehouse/chart/README.md modules/iceberg-lakehouse/chart/Chart.yaml modules/iceberg-lakehouse/chart/values.yaml modules/iceberg-lakehouse/chart/templates/_helpers.tpl modules/iceberg-lakehouse/chart/templates/config.yaml modules/iceberg-lakehouse/chart/templates/deployment.yaml modules/iceberg-lakehouse/chart/templates/service.yaml modules/iceberg-lakehouse/chart/templates/setup-job.yaml

clean: ## Remove compiled caches only
	$(PYTHON) -c 'import pathlib; [p.unlink() for p in pathlib.Path("__pycache__").glob("*.pyc")]'
