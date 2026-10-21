#!/usr/bin/env bash
# Lokal Go o'rnatilmagan bo'lsa ham Go buyruqlarini Docker ichida ishga tushiradi.
#   scripts/go.sh libs/go-common go test ./...
# Docker socket ulanadi — integratsiya testlari (testcontainers-go) ham ishlaydi.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${1:?ishchi papka (repo ildiziga nisbatan)}"
shift
GO_IMAGE="${GO_IMAGE:-golang:1.26-bookworm}"
exec docker run --rm --network host \
  -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod -e GOFLAGS="${GOFLAGS:-}" \
  -e TESTCONTAINERS_RYUK_DISABLED="${TESTCONTAINERS_RYUK_DISABLED:-true}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --group-add "$(stat -c %g /var/run/docker.sock)" \
  -v medbron-gocache:/cache \
  -v "$ROOT":/src -w "/src/$DIR" \
  "$GO_IMAGE" "$@"
