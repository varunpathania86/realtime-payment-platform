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
	PYTHONPATH=$(ROOT) $(PYTHON) -m unittest discover -s $(ROOT)/tests/unit -p 'test_lakehouse_catalog.py'

lint: ## Validate the Polaris chart and setup script
	helm lint --strict chart
	$(PYTHON) -m py_compile setup_catalog.py

fmt: ## Check shared whitespace rules
	cd $(ROOT) && .venv/bin/pre-commit run --files modules/lakehouse-catalog/Makefile modules/lakehouse-catalog/.dockerignore modules/lakehouse-catalog/.trivyignore.yaml modules/lakehouse-catalog/Dockerfile modules/lakehouse-catalog/README.md modules/lakehouse-catalog/setup_catalog.py modules/lakehouse-catalog/chart/README.md modules/lakehouse-catalog/chart/Chart.yaml modules/lakehouse-catalog/chart/values.yaml modules/lakehouse-catalog/chart/templates/_helpers.tpl modules/lakehouse-catalog/chart/templates/config.yaml modules/lakehouse-catalog/chart/templates/deployment.yaml modules/lakehouse-catalog/chart/templates/service.yaml modules/lakehouse-catalog/chart/templates/setup-job.yaml

clean: ## Remove compiled caches only
	$(PYTHON) -c 'import pathlib; [p.unlink() for p in pathlib.Path("__pycache__").glob("*.pyc")]'
