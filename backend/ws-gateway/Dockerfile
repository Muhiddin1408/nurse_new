# syntax=docker/dockerfile:1.7
# Build konteksti — REPO ILDIZI (libs/go-common kerak):
#   docker build -f backend/ws-gateway/Dockerfile -t medbron/ws-gateway:$(git rev-parse HEAD) .
# Bitta image — ikki binar: ws-gateway (WebSocket/HTTP), ws-gateway-consumer (+ healthcheck).

FROM golang:1.26-bookworm AS build
WORKDIR /src/backend/ws-gateway
COPY libs/go-common /src/libs/go-common
COPY backend/ws-gateway/go.mod backend/ws-gateway/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY backend/ws-gateway/ ./
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ ./cmd/...

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ /usr/local/bin/
USER nonroot:nonroot
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]
CMD ["/usr/local/bin/ws-gateway"]
