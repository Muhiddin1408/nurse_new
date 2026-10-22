# payment-service

> NN=10 · Python · ichki port 8080 · host 8110 · Postgres 5410 · Redis 6310

## Vazifasi
Pul harakati: to'lov urinishlari (Payme Merchant API, Click SHOP API), webhooklar, refundlar (retry bilan),
ikki yozuvli daftar (ledger), fiskal chek elementlari (provayder OFD'si), kunlik reconciliation. Pul — tiyin.

Bron bilan bog'liqlik **bir tomonlama** (ADR-0006): payment booking'ni sinxron chaqiradi (`/payable`,
`/extend-payment`), booking payment'ni hech qachon chaqirmaydi — natija faqat event orqali:
`payment.captured` → booking tasdiqlaydi; tasdiqlay olmasa `booking.confirmation_failed` → shu yerda to'liq refund.

Holatlar: `created → processing → succeeded → (partially_)refunded`; `created|processing → cancelled|expired`;
`succeeded → reversed` (provayder o'zi qaytardi). Bitta bronda pul faqat BIR marta yechiladi
(`uq_payments_money_taken_per_booking`). Refund: `pending → succeeded | failed | manual_required`.

## Pul qoidalari (ADR-0012)
- Taqsimot — bron `money_split` snapshot'idan (`medbron_common.money.share_of`, billing bilan yagona formula);
  zaklad (qisman to'lov) — proporsional.
- Refund: shifokor o'z ulushini qaytaradi (`doctor_share_tiyin`, to'lovdagi ulushdan oshmaydi), qolganini — shu
  jumladan qaytmaydigan provayder komissiyasini — platforma ko'taradi. Nizoda `doctor_liable=false` → ulush 0.
- Daftar: `client_payments` (aktiv), `platform_revenue`, `doctor_payable`, `provider_fees`; trial balance doim 0.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| POST | `/v1/payments` | user JWT | Checkout `{booking_id, provider}` → `{payment, checkout_url, expires_at}`; idempotent; 409 `NOT_PAYABLE` / `ALREADY_PAID` |
| GET | `/v1/payments?booking_id=` | user JWT | O'z to'lovlari |
| GET | `/v1/payments/{id}` | user JWT | Begona — 404 |
| POST | `/v1/payments/payme/webhook` | Basic `Paycom:<key>` + IP | JSON-RPC, har doim HTTP 200 |
| POST | `/v1/payments/click/prepare`, `/complete` | MD5 imzo + IP | form-urlencoded, har doim HTTP 200 |
| GET | `/v1/admin/payments?booking_id=&status=&provider=`, `/v1/admin/payments/{id}` | `payments.read` | Refundlar bilan |
| GET | `/v1/admin/refunds?status=&booking_id=` | `payments.read` | `manual_required` — admin navbati |
| POST | `/v1/admin/refunds` (`Idempotency-Key`) | `payments.refund` | Qo'lda refund (qoldiqdan oshmaydi) |
| POST | `/v1/admin/refunds/{id}/complete` | `payments.refund` | Kabinetda qaytarilganini tasdiqlash |
| POST | `/v1/admin/refunds/{id}/retry` | `payments.refund` | failed/manual → pending |
| GET | `/v1/admin/ledger?booking_id=`, `/v1/admin/ledger/balances?doctor_id=` | `payments.read` | Daftar va qoldiqlar |
| GET | `/v1/admin/reconciliation?status=&type=` | `payments.read` | Farqlar |
| POST | `/v1/admin/reconciliation/registry?provider=&day=` (`text/csv`) | `payments.read` | Kabinet reestri bilan solishtirish |
| POST | `/v1/admin/reconciliation/{id}/resolve` | `payments.refund` | Farqni yopish |

Internal API yo'q (`/internal/v1/*` — 403).

## Eventlar
| Publish | Consume |
|---|---|
| `payment.created`, `payment.captured`, `payment.failed`, `payment.reversed`, `refund.succeeded`, `refund.failed`, `payment.discrepancy_found`, `audit.recorded` | `booking.cancelled` / `booking.no_show` (`refund_tiyin`), `booking.confirmation_failed` (to'liq refund), `dispute.resolved`, `payout.paid` (ledger), `admin.settings_changed` |

## Sinxron bog'liqliklar
Chaqiradi: `booking-service` (`/payable`, `/extend-payment`), `admin-service` (sozlamalar: fiskal TIN).
Provayderlarga chiquvchi so'rovlar (Click reversal / `ofd_data/submit_items`) — `app/clients/providers.py`
(timeout, idempotent retry, circuit breaker, `provider_logs`; ichki token tashqariga chiqmaydi).

## Worker'lar
| Konteyner | Vazifa |
|---|---|
| `payment-service-outbox-relay` | outbox → RabbitMQ |
| `payment-service-event-consumer` | yuqoridagi consume'lar |
| `payment-service-scheduler` | har daqiqa refundlar; 5 daq Click fiskal; 10 daq eskirgan urinishlar; 03:00 ichki reconciliation; 04:30 jurnal retention |

## Muhit o'zgaruvchilari
`.env.example` da hammasi izoh bilan. Muhimlari: `PAYME_MERCHANT_ID`, `PAYME_SECRET_KEY`, `PAYME_ALLOWED_IPS`,
`CLICK_SERVICE_ID`, `CLICK_MERCHANT_ID`, `CLICK_MERCHANT_USER_ID`, `CLICK_SECRET_KEY`, `CLICK_ALLOWED_IPS`,
`REFUND_MAX_ATTEMPTS`. Kalit bo'sh — webhook fail-closed (hamma so'rov rad etiladi).

## Ishga tushirish
```bash
uv sync
uv run pytest                             # Docker kerak (testcontainers: Postgres, Redis, RabbitMQ)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm payment-service-migrate
curl localhost:8110/health/ready
```

## Migratsiyalar
```bash
uv run alembic revision --autogenerate -m "..."   # keyin faylni ko'zdan kechiring
```
Migratsiya deploy'ning alohida qadami — container CMD'da emas (BLUEPRINT 9). Monolitdan ko'chirish —
`scripts/migrate/money.py`, cutover — `docs/runbooks/money-cutover.md`.

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `payments`, `refunds`, `ledger_*` | har to'lov | saqlanadi (moliyaviy hisobot, audit) |
| `provider_logs` | har webhook / chiquvchi so'rov | scheduler: `PROVIDER_LOG_RETENTION_DAYS` (365) |
| `discrepancies` | farq bo'lganda | saqlanadi |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
