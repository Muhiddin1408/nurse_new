# dispute-service

> NN=13 · Python · ichki port 8080 · host 8113 · Postgres 5413 · Redis 6313

## Vazifasi
Nizolar (B11, ARCHITECTURE 6.7): mijoz to'langan/bo'lib o'tgan bron bo'yicha shikoyat ochadi, dalil (fayl)
biriktiradi, admin SLA (`sla.dispute_hours`, standart 48 soat) ichida hal qiladi. Pulni qaytarish — payment
(`dispute.resolved` → refund), shifokor ulushini ushlash — billing (`refund.succeeded.doctor_share_tiyin`).

Holatlar: `open → resolved` (refund > 0) `→ closed` (`refund.succeeded{source=dispute}`); `open → closed`
(rad etildi). SLA o'tsa — `dispute.sla_breached` (bir marta, eskalatsiya). Bitta bronga bitta nizo.

## Yechim qoidalari
| `resolution` | `refund_tiyin` | Izoh |
|---|---|---|
| `refund_full` | to'langan summa (booking `paid_tiyin`, nizo ochilgandagi) | summa yuborilmasa ham shu |
| `refund_partial` | `0 < x < paid_tiyin` | |
| `rejected` | 0 | `doctor_liable=false`; darhol `closed` |

`doctor_liable=true` — refund shifokor ulushidan ham ushlanadi; `false` — hammasini platforma ko'taradi (ADR-0012).
Klinikada to'langan (`paid_tiyin=0`) bron — faqat `rejected` (pul platformadan o'tmagan). Payment qoldiq bilan
cheklaydi (avval bekor qilishda qaytgan bo'lsa) — haqiqiy summa `refunded_tiyin` da.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| POST | `/v1/disputes` | user JWT | `{booking_id, reason, description}` → 201; 404 begona/yo'q bron, 409 `NOT_DISPUTABLE` / `DISPUTE_EXISTS` |
| GET | `/v1/disputes/mine`, `/v1/disputes/{id}` | user JWT | Begona — 404 |
| POST | `/v1/disputes/{id}/evidence` | user JWT | `{file_id}` — avval file-service'ga `purpose=dispute_evidence` bilan yuklanadi; idempotent, ≤ 10 ta |
| GET | `/v1/admin/disputes?status=open&overdue=&booking_id=` | `disputes.resolve` | SLA bo'yicha navbat |
| GET | `/v1/admin/disputes/{id}` | `disputes.resolve` | Dalillar ≤ 5 daq havola bilan (file-service auditiga) |
| POST | `/v1/admin/disputes/{id}/resolve` | `disputes.resolve` | `{resolution, refund_tiyin, doctor_liable, note}`; idempotent; audit |

Internal API yo'q (`/internal/v1/*` — 403).

## Eventlar
| Publish | Consume |
|---|---|
| `dispute.opened`, `dispute.resolved`, `dispute.sla_breached`, `audit.recorded` | `refund.succeeded` (faqat `source=dispute`), `admin.settings_changed` |

## Sinxron bog'liqliklar
Chaqiradi: `booking-service` (`/internal/v1/bookings/{id}/summary?user_id=` — egalik, holat, to'langan summa),
`file-service` (`/internal/v1/files/{id}/attach`, `/url`), `admin-service` (global sozlamalar: `sla.dispute_hours`).

## Worker'lar
| Konteyner | Vazifa |
|---|---|
| `dispute-service-outbox-relay` | outbox → RabbitMQ |
| `dispute-service-event-consumer` | yuqoridagi consume'lar |
| `dispute-service-scheduler` | har 15 daqiqa SLA tekshiruvi |

## Ishga tushirish
```bash
uv sync
uv run pytest                             # Docker kerak (testcontainers: Postgres, Redis, RabbitMQ)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm dispute-service-migrate
curl localhost:8113/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `disputes`, `dispute_evidence` | har nizo (kam) | saqlanadi (moliyaviy tarix) |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
