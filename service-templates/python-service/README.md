# __SERVICE_NAME__

> NN=__NN__ · Python · ichki port 8080 · host __HOST_PORT__ · Postgres __PG_PORT__ · Redis __REDIS_PORT__
> Shablondan yaratilgan (`scripts/new-service.sh`). Quyidagi bo'limlarni servis vazifasiga moslab to'ldiring —
> BLUEPRINT 14-bo'lim "tayyor" mezoni README siz bajarilmaydi.

## Vazifasi
<!-- docs/SERVICE_REGISTRY.md dagi "Egalik" bandi: qaysi ma'lumot va qaror shu servisga tegishli. -->

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | Standart |
| GET | `/v1/__SHORT__/whoami` | user JWT | Shablon namunasi — o'chiring |

## Eventlar
Manba: `contracts/events/catalog.yaml`.

| Publish | Consume |
|---|---|
| __PUBLISHES__ | __CONSUMES__ |

## Sinxron bog'liqliklar
Chaqiradi: __CALLS__. Chaqiruvchilar (`/internal/v1`): __CALLERS__.

## Ishga tushirish
```bash
uv sync                                   # .venv (libs/py-common editable)
uv run pytest                             # unit
uv run pytest -m integration              # Docker kerak (testcontainers)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm __SERVICE_NAME__-migrate
curl localhost:__HOST_PORT__/health/ready
```

## Migratsiyalar
```bash
uv run alembic revision --autogenerate -m "..."   # keyin faylni ko'zdan kechiring
```
Migratsiya deploy'ning alohida qadami — container CMD'da emas (BLUEPRINT 9).

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin (soatiga bir) |
