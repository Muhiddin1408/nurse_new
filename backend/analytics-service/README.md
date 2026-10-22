# analytics-service

> NN=15 · Python · ichki port 8080 · host 8115 · Postgres 5415 · ClickHouse 8123 / 9009 (faqat 127.0.0.1)

## Vazifasi
Barcha domen eventlarining o'zgarmas tarixi (ClickHouse `events_raw`) va undan hisobotlar: kunlik bronlar,
konversiya, bekor qilish / no-show ulushi, GMV va onlayn to'lovlar, shifokorlar reytingi. Pastki oqim servisi:
hech narsa publish qilmaydi, hech kimni chaqirmaydi, uni ham hech kim sinxron chaqirmaydi.

## Ingest (`#` → ClickHouse)
- Navbat `analytics-service.all-events` — `medbron.events` ga `#` bilan bog'langan (`audit.recorded` dan tashqari
  hammasi saqlanadi; audit — admin-service'niki, o'tkazib yuboriladi).
- libs'dagi `EventConsumer` emas, servisning o'z `Ingestor` i (`app/services/ingest.py`): har event turi uchun
  handler va per-event Postgres tranzaksiyasi o'rniga — partiya (`INGEST_BATCH_SIZE` yoki `INGEST_FLUSH_SECONDS`,
  qaysi oldin). Topologiya (navbat, DLX) — o'sha libs funksiyalari bilan.
- At-least-once: ack — partiya ClickHouse'ga yozilgandan KEYIN. Dedup — `ReplacingMergeTree`
  (`event_type, occurred_at, event_id`), hisobotlar `FINAL` bilan o'qiydi.
- ClickHouse yiqilsa partiya 1 → 2 → 4 … `INGEST_MAX_BACKOFF_SECONDS` bilan qayta yoziladi, xabarlar ack
  qilinmaydi (prefetch to'ladi — navbat brokerda o'sadi, xabar yo'qolmaydi). To'xtashda — `nack(requeue)`.
- Noto'g'ri envelope (JSON emas, maydon yo'q) → `medbron.dlx` (`x-dead-letter-reason=invalid_envelope`).
  Sxemaga mos kelmagan `data` ham yoziladi — bu xom tarix.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: ClickHouse + Postgres |
| GET | `/v1/admin/analytics/summary?from=&to=&doctor_id=&clinic_id=` | `analytics.read` | Hodisalar soni, GMV, onlayn to'lovlar (yechilgan − qaytarilgan − provayder qaytargan), konversiya / bekor qilish / no-show ulushi |
| GET | `/v1/admin/analytics/bookings/daily?…` | `analytics.read` | Kunma-kun (bo'sh kunlar 0 bilan) |
| GET | `/v1/admin/analytics/funnel?…` | `analytics.read` | Kogorta: oynada yaratilgan bronlar → to'lov kerak → to'landi → tasdiqlandi → yakunlandi / bekor / muddati o'tdi / kelmadi |
| GET | `/v1/admin/analytics/doctors/top?from=&to=&clinic_id=&sort=completed\|gmv\|no_show\|cancelled&limit=` | `analytics.read` | |

Sanalar — Asia/Tashkent kunlari (`from` va `to` ham kiradi), berilmasa oxirgi 30 kun, maksimal `REPORT_MAX_DAYS`.
Filtr shifokor/klinika bo'yicha — bronning `booking.created` dagi qiymati (to'lov/refund eventlari `booking_id`
orqali bog'lanadi). Internal API yo'q (`/internal/v1/*` — 403).

## Ko'rsatkichlar ta'rifi
- `created/confirmed/…` — oynada yuz bergan eventlar soni (vaqt bo'yicha).
- `conversion_rate`, `cancellation_rate`, `no_show_rate` — kogorta (oynada YARATILGAN bronlar) ichidagi ulush.
- `gmv_tiyin` — oynada yakunlangan qabullarning `money.total_tiyin` yig'indisi (klinikada to'langanlari ham).
- `net_payments_tiyin` — onlayn: `payment.captured` − `refund.succeeded` − `payment.reversed`.

## Eventlar
| Publish | Consume |
|---|---|
| — | `#` (katalogdagi 55 ta, `audit.recorded` dan tashqari) |

## Muhit o'zgaruvchilari
`.env.example` da hammasi izoh bilan: `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`,
`CLICKHOUSE_DATABASE`, `INGEST_BATCH_SIZE`, `INGEST_FLUSH_SECONDS`, `INGEST_PREFETCH`, `INGEST_MAX_BACKOFF_SECONDS`,
`REPORT_MAX_DAYS`.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # Docker kerak (testcontainers: ClickHouse, Postgres, RabbitMQ)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm analytics-service-migrate   # Alembic + `python -m app.cli clickhouse-migrate`
curl localhost:8115/health/ready
```

## Migratsiyalar
- Postgres — Alembic (hozircha faqat eventbus jadvallari).
- ClickHouse — `app/clickhouse/schema.py` (`MIGRATIONS`, versiyalangan, faqat oldinga; qo'llanganlari
  `schema_migrations` da). Yangi o'zgarish — ro'yxat oxiriga yangi versiya.

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| ClickHouse `events_raw` | har domen eventi | saqlanadi (oylik partitsiya; kerak bo'lsa TTL migratsiya bilan) |
