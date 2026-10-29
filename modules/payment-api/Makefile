SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
VERSION ?= dev
COMMIT ?= unknown
INTEGRATION ?= 0

.PHONY: help build test lint fmt clean security integration migrate

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Build bin/payment-api with version metadata
	CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=$(VERSION) -X main.commit=$(COMMIT)" -o bin/payment-api .

test: ## Run offline standard-library unit tests
	GOPROXY=off go test ./...

ifeq ($(INTEGRATION),1)
test: integration
endif

integration: ## Run Docker-backed PostgreSQL Testcontainers integration tests
	go test -tags=integration -count=1 -timeout=5m ./internal/postgres

migrate: build ## Apply embedded migrations using DATABASE_URL (bounded to 30 seconds)
	./bin/payment-api migrate

lint: ## Check Go source with go vet and gofmt
	go vet ./...
	@test -z "$$(gofmt -l .)" || { gofmt -l .; exit 1; }

fmt: ## Format Go source
	gofmt -w .

security: ## Run the separate advisory IaC scan for this module
	$(MAKE) -C ../.. security-module MODULE_NAME=$(notdir $(CURDIR))

clean: ## Remove build output
	rm -rf bin
