# medbron-common (Python)

Barcha Python servislari uchun umumiy kod — **bir marta** yoziladi va versiyalanadi (BLUEPRINT 3,
Trasso #7: 25 ta nusxa farqlanib ketgan). Go'dagi ekvivalenti — `libs/go-common`, ikkalasining
mosligi `libs/testdata` vektorlari bilan tekshiriladi.

| Modul | Vazifasi |
|---|---|
| `app.create_app` | FastAPI poydevori: middleware, xato formati, `/health/*`, `/metrics`, tracing |
| `config.ServiceSettings` | Umumiy sozlamalar; `INTERNAL_TOKEN_<CALLER>` → ruxsat etilgan chaqiruvchilar |
| `security` | RS256 user/admin JWT (kid rotatsiyasi, `WRONG_AUDIENCE`), `require_roles`, `require_permission`, `verify_internal_token` |
| `errors` | `AppError` ierarxiyasi → `{"detail": {"code", "message"}}` |
| `http_client.ServiceClient` | Timeout, retry (faqat idempotent), backoff+jitter, circuit breaker, header'lar |
| `eventbus.Outbox` / `OutboxRelay` | Transactional outbox (`FOR UPDATE SKIP LOCKED`, backoff, `failed`, purge) |
| `eventbus.EventConsumer` | Idempotent consumer (`processed_events` + handler bitta tx), retry 5s/30s/5m → DLQ |
| `eventbus.dlq` | DLQ ko'rish va manba navbatiga replay (CLI) |
| `eventbus.topology` | `medbron.events`, DLX/DLQ, retry (headers exchange) |
| `events` | `Events.*` konstantalari, `CATALOG`, JSON Schema validatsiyasi (generatsiya qilingan) |
| `logging` | JSON log, telefon/karta maskalash |
| `sms` | SMS adapterlari: Eskiz (token Redis'da, 401 → qayta login, circuit breaker) va console; prod'da console taqiqlangan |
| `phone` | `normalize_uz_phone` (+998XXXXXXXXX), `mask_phone` |
| `audit` | `record_audit(outbox, session, Actor, ...)` → `audit.recorded` (biznes o'zgarishi bilan bitta tx) |
| `signing.TokenSigner` | RS256 imzolash va JWKS — faqat auth/admin; `read_private_pem` (env base64 yoki fayl) |
| `db.transaction(commit_on=...)` | Tranzaksiya; ro'yxatdagi xatolarda ham COMMIT (urinishlar hisoblagichi, oila bekor qilinishi) |
| `workers.run_periodic` | Davriy vazifalar: `Every(sekund)`, `DailyAt(soat, daqiqa, tz)`; xato butun scheduler'ni yiqitmaydi |
| `context.client_ip` | Mijoz IP'si (Kong qo'ygan `X-Real-IP`), faqat haqiqiy IP qaytadi |
| `testing.sample_data` | JSON Schema'dan kontraktga mos minimal event `data` (+ overrides, validatsiya bilan) |
| `health`, `metrics`, `telemetry`, `workers`, `db`, `geo`, `testing` | Yordamchilar |

`events/_catalog_gen.py` va `events/schemas/` — **generatsiya qilingan** (`python scripts/gen-events.py`),
qo'lda tahrirlanmaydi.

```bash
uv sync
uv run pytest              # unit + integratsiya (Docker: testcontainers Postgres/RabbitMQ)
uv run ruff check . && uv run mypy src
```
