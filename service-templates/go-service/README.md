# __SERVICE_NAME__

> NN=__NN__ · Go · ichki port 8080 · host __HOST_PORT__ · Postgres __PG_PORT__ · Redis __REDIS_PORT__
> Shablondan yaratilgan (`scripts/new-service.sh`). Bo'limlarni servis vazifasiga moslab to'ldiring —
> BLUEPRINT 14-bo'lim "tayyor" mezoni README siz bajarilmaydi.

## Vazifasi
<!-- docs/SERVICE_REGISTRY.md dagi "Egalik" bandi. -->

## Tuzilma
| Papka | Vazifasi |
|---|---|
| `cmd/__SERVICE_NAME__` | HTTP server |
| `cmd/__SHORT__-relay` | Outbox relay (alohida konteyner) |
| `cmd/__SHORT__-consumer` | Event consumer (alohida konteyner) |
| `internal/domain` | Entity va sof biznes qoidalari |
| `internal/service` | Use-case'lar; event publish faqat shu yerdan (outbox, bitta tx) |
| `internal/store` | pgx repositorylar |
| `internal/cache` | Redis |
| `internal/clients` | Boshqa servis clientlari |
| `internal/eventbus` | Event handlerlar |
| `internal/httpapi` | Marshrut va handlerlar |
| `migrations` | golang-migrate SQL |

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | Standart |
| GET | `/v1/__SHORT__/whoami` | user JWT | Shablon namunasi — o'chiring |

## Eventlar
| Publish | Consume |
|---|---|
| __PUBLISHES__ | __CONSUMES__ |

## Sinxron bog'liqliklar
Chaqiradi: __CALLS__. Chaqiruvchilar (`/internal/v1`): __CALLERS__.

## Ishga tushirish
```bash
make test                # unit
make test-integration    # Docker kerak
make lint
cp .env.example .env && make up && make migrate
curl localhost:__HOST_PORT__/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | consumer: 30 kundan keyin (soatiga bir) |
