SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate the local Terraform environment
	../../scripts/local-dev.sh validate

test: ## Lint and render all catalog-discovered application charts
	../../scripts/local-dev.sh charts

lint: ## Check Terraform formatting
	terraform fmt -check -recursive

fmt: ## Format Terraform
	terraform fmt -recursive

clean: ## Preserve local state; remove nothing
	@echo "Local state is preserved; use make dev-down from the repository root to destroy resources."
