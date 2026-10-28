SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
VERSION ?= dev
COMMIT ?= unknown

.PHONY: help build test lint fmt clean security

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Build bin/payment-api with version metadata
	CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=$(VERSION) -X main.commit=$(COMMIT)" -o bin/payment-api .

test: ## Run offline standard-library unit tests
	GOPROXY=off go test ./...

lint: ## Check Go source with go vet and gofmt
	go vet ./...
	@test -z "$$(gofmt -l .)" || { gofmt -l .; exit 1; }

fmt: ## Format Go source
	gofmt -w .

security: ## Run the separate advisory IaC scan for this module
	$(MAKE) -C ../.. security-module MODULE_NAME=$(notdir $(CURDIR))

clean: ## Remove build output
	rm -rf bin
