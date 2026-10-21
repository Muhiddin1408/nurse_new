# syntax=docker/dockerfile:1.7
# Build konteksti — REPO ILDIZI (libs/go-common kerak):
#   docker build -f backend/__SERVICE_NAME__/Dockerfile -t medbron/__SERVICE_NAME__:$(git rev-parse HEAD) .
# Bitta image — uch binar: __SERVICE_NAME__ (HTTP), __SHORT__-relay, __SHORT__-consumer (+ healthcheck).

FROM golang:1.26-bookworm AS build
WORKDIR /src/backend/__SERVICE_NAME__
COPY libs/go-common /src/libs/go-common
COPY backend/__SERVICE_NAME__/go.mod backend/__SERVICE_NAME__/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY backend/__SERVICE_NAME__/ ./
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ ./cmd/...

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ /usr/local/bin/
USER nonroot:nonroot
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]
CMD ["/usr/local/bin/__SERVICE_NAME__"]
