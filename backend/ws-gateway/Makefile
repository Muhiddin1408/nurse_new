# Lokal Go bo'lmasa ham ishlaydi: GO := ../../scripts/go.sh backend/ws-gateway go
GO ?= $(shell command -v go >/dev/null 2>&1 && echo go || echo ../../scripts/go.sh backend/ws-gateway go)

.PHONY: build test test-integration lint fmt tidy up

build:
	$(GO) build ./...

test:
	$(GO) test ./...

test-integration:
	$(GO) test -tags integration -count=1 ./...

lint:
	$(GO) vet ./...
	test -z "$$(gofmt -l .)"

fmt:
	gofmt -w .

tidy:
	$(GO) mod tidy

up:
	docker compose up -d --build
