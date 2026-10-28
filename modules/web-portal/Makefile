SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
VERSION ?= dev
COMMIT ?= unknown

.PHONY: help build test lint fmt clean security

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

node_modules/.package-lock.json: package.json package-lock.json
	npm ci --no-audit --no-fund
	@touch $@

build: node_modules/.package-lock.json ## Build static assets and bin/web-portal
	VITE_APP_VERSION="$(VERSION)" VITE_APP_COMMIT="$(COMMIT)" npm run build
	CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o bin/web-portal .

test: node_modules/.package-lock.json ## Run offline Node and Go unit tests
	npm test
	GOPROXY=off go test ./...

lint: node_modules/.package-lock.json ## Check TypeScript types, Go vet, and Go formatting
	npm run lint
	go vet ./...
	@test -z "$$(gofmt -l *.go)" || { gofmt -l *.go; exit 1; }

fmt: ## Format Go code (frontend uses shared editor settings)
	gofmt -w *.go

security: ## Run the separate advisory IaC scan for this module
	$(MAKE) -C ../.. security-module MODULE_NAME=$(notdir $(CURDIR))

clean: ## Remove static assets, test output, and server binary
	rm -rf dist .test-dist bin
