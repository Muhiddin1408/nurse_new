# go-common

Go servislari (schedule-service, ws-gateway) uchun umumiy kod. Python `libs/py-common` bilan bir xil
kontrakt: outbox jadvali sxemasi, envelope, RabbitMQ topologiyasi, JWT `kid`, xato formati, metrika nomlari.

| Paket | Vazifasi |
|---|---|
| `config` | `Base` (envconfig), `InboundInternalTokens` |
| `auth` | RS256 verifier (kid, audience), `RequireUser/RequireRoles/RequireAdmin/RequireInternal` |
| `httpapi` | Router (`/health/*`, `/metrics`, 404/405), `RequestContext`, `Recoverer`, `Error`, `DecodeJSON`, `Serve` |
| `httpclient` | Retry/backoff/jitter, circuit breaker, header'lar; `UpstreamError` → 502 |
| `eventbus` | `Outbox.AddEvent`, `Relay`, `Consumer` (idempotent, retry → DLQ), `Publisher`, `Topology`, `SchemaSQL` |
| `events` | Konstantalar va `Catalog` (generatsiya qilingan), embed qilingan JSON Schema validatsiyasi |
| `logging`, `telemetry`, `health`, `circuit`, `reqctx`, `testutil` | Yordamchilar |

```bash
../../scripts/go.sh libs/go-common go test ./...                          # unit
../../scripts/go.sh libs/go-common go test -tags integration ./...        # + Postgres/RabbitMQ
```
`events/catalog_gen.go` va `events/schemas/` — generatsiya qilingan (`python scripts/gen-events.py`).
