SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
PYTHON := $(abspath ../../.venv/bin/python)
.PHONY: help build test lint fmt clean security

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate examples and build a deterministic contract archive
	$(PYTHON) ../../scripts/event_contracts.py --module . --build

test: ## Test Avro round trips and invalid synthetic payment events
	cd ../.. && .venv/bin/python -m unittest discover -s modules/event-contracts/tests -p 'test_*.py'

lint: ## Parse schemas and validate the committed examples
	$(PYTHON) ../../scripts/event_contracts.py --module .

fmt: ## Format Avro schemas and examples as JSON
	@for path in schemas/*.avsc examples/*.json; do $(PYTHON) -m json.tool --indent 2 "$$path" "$$path"; done

clean: ## Remove the generated contract archive
	$(PYTHON) -c 'from pathlib import Path; Path("dist/contracts.tar").unlink(missing_ok=True)'

security: ## Run the separate advisory IaC scan for this module
	$(MAKE) -C ../.. security-module MODULE_NAME=$(notdir $(CURDIR))
