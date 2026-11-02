SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Validate the local Terraform environment
	../../scripts/local-dev.sh validate
	bash ../../scripts/multi-cluster.sh validate

test: ## Lint and render all catalog-discovered application charts
	../../scripts/local-dev.sh charts
	terraform -chdir=modules/kafka-broker init -backend=false -input=false
	terraform -chdir=modules/kafka-broker test

lint: ## Check Terraform formatting and shared TFLint rules
	terraform fmt -check -recursive
	tflint --recursive --config=$(abspath ../../.tflint.hcl)

fmt: ## Format Terraform
	terraform fmt -recursive

clean: ## Preserve local state; remove nothing
	@echo "Local state is preserved; use make dev-down from the repository root to destroy resources."
