# Sub-project Makefile. Copy to <project>/Makefile.
# Shared contract: build, test, lint, fmt, clean. Shared linters run from the root.

.DEFAULT_GOAL := help
.PHONY: help build test lint fmt clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

build: ## Build the project
	@echo "TODO: build"

test: ## Run unit tests
	@echo "TODO: test"

lint: ## Run project-specific linters
	@echo "TODO: lint"

fmt: ## Format code
	@echo "TODO: fmt"

clean: ## Remove build output
	@echo "TODO: clean"
