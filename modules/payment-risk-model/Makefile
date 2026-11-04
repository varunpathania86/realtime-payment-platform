SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean train integration
PYTHON := $(abspath ../../.venv/bin/python)
ROOT := $(abspath ../..)
IMAGE := rpp-tools/payment-risk:$(shell awk '$$1 == "risk-python" {print $$2}' ../../.tool-versions)
OUTPUT ?= $(ROOT)/.local/models/payment-risk/1.0.0
INTEGRATION ?= 0

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate model sources and feature contract without ML dependencies
	$(PYTHON) -m py_compile features.py train.py scorer.py
	$(PYTHON) -m json.tool feature_contract.json >/dev/null

test: ## Run deterministic offline feature contract tests
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

ifeq ($(INTEGRATION),1)
test: integration
endif

lint: build ## Validate source syntax and metadata

fmt: ## Check repository formatting without rewriting unrelated files
	cd "$(ROOT)" && .venv/bin/pre-commit run --files modules/payment-risk-model/features.py modules/payment-risk-model/train.py modules/payment-risk-model/feature_contract.json

train: ## Train, compare and export synthetic models in the setup-built container
	@mkdir -p "$(OUTPUT)"
	docker run --rm --user "$$(id -u):$$(id -g)" --network none \
		-v "$(CURDIR):/opt/risk:ro" -v "$(OUTPUT):/output" "$(IMAGE)" --output /output

integration: ## Reproduce training and three-way golden parity including the real Flink worker
	cd "$(ROOT)" && STREAMING=1 RISK_MODEL=1 $(MAKE) setup
	$(MAKE) train
	$(MAKE) -C ../flink-jobs risk-integration MODEL_DIRECTORY="$(OUTPUT)"

clean: ## Remove only Python bytecode in this module
	$(PYTHON) -c 'from pathlib import Path; [p.unlink() for p in Path(".").glob("**/__pycache__/*.pyc")]'
