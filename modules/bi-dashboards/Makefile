SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean image integration
ROOT := ../..
PYTHON := $(abspath $(ROOT)/.venv/bin/python)
PIN = $$(awk '$$1 == "$(1)" {print $$2}' $(ROOT)/.tool-versions)
IMAGE := rpp-tools/bi-dashboards:check
INTEGRATION ?= 0

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate the Superset chart and Python sources
	helm lint --strict chart
	$(PYTHON) -m py_compile superset/*.py tests/superset_check.py

test: ## Run offline chart, asset and frontend-payload tests
	PYTHONPATH=$(ROOT) $(PYTHON) -m unittest discover -s $(ROOT)/tests/unit -p 'test_bi_dashboards.py'

ifeq ($(INTEGRATION),1)
test: integration
endif

lint: build ## Validate the chart and source syntax

image: ## Build the pinned Superset image used by integration checks
	docker build --build-arg "SUPERSET_VERSION=$(call PIN,superset)" \
		--build-arg "SUPERSET_DIGEST=$(call PIN,superset-image-digest)" -t "$(IMAGE)" .

integration: image ## Check configuration and guest-query security against the real Superset
	docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /app/superset_home \
		--entrypoint python -e PYTHONPATH=/opt/bi/superset -v "$(CURDIR):/opt/bi:ro" \
		"$(IMAGE)" /opt/bi/tests/superset_check.py

fmt: ## Check shared whitespace rules
	cd $(ROOT) && .venv/bin/pre-commit run --files $(addprefix modules/bi-dashboards/,Makefile .dockerignore \
		.trivyignore.yaml Dockerfile README.md requirements.txt $(wildcard superset/*.py) tests/superset_check.py \
		tests/fixtures/frontend-chart-queries.json chart/README.md chart/Chart.yaml chart/values.yaml \
		$(wildcard chart/templates/*))

clean: ## Remove Python bytecode in this module
	$(PYTHON) -c 'from pathlib import Path; [p.unlink() for p in Path(".").glob("**/__pycache__/*.pyc")]'
