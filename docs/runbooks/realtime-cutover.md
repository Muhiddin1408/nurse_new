# Runbook: real-time va qo'shimchalarga o'tish (5-bosqich cutover)

Maqsad: sharhlar, nizolar, qidiruv, analitika va real-time monolitdan review-, dispute-, search-, analytics-service
va ws-gateway'ga o'tadi (ADR-0013). 2–4-bosqichlar allaqachon ko'chgan bo'lishi shart.

Kutilgan to'xtash: **sharh va nizo yozish uchun ≈ 5 daqiqa**. Qidiruv, bron va to'lov to'xtamaydi.

## 0. Tayyorgarlik
1. Beshala servis deploy qilingan: `alembic upgrade head` (review, dispute, search, analytics),
   analytics — `python -m app.cli clickhouse-migrate` (migrate konteyneri ikkalasini bajaradi). `/health/ready` = 200.
   **event-consumer'lar to'xtatilgan** (review, dispute) — ko'chirish bilan aralashmasin.
2. Elasticsearch (`search-service-elasticsearch`) va ClickHouse (`analytics-service-clickhouse`) — egasining
   compose'ida, ichki tarmoqda; ES heap va disk hajmi prod uchun sozlangan.
3. catalog-service'da `INTERNAL_TOKEN_SEARCH_SERVICE`, admin-service'da `INTERNAL_TOKEN_REVIEW_SERVICE` va
   `INTERNAL_TOKEN_DISPUTE_SERVICE` (yangi chaqiruvchilar).
4. ws-gateway: `WS_ALLOWED_ORIGINS` = frontend domeni; Kong access log formatidan query (token) olib tashlangan.
5. Dry-run:
   ```bash
   python scripts/migrate/feedback.py --dry-run --source "$MONOLITH_DATABASE_URL"
   ```

## 1. Monolitda yozishni muzlatish
Kong: `/api/v1/booking/*/review`, `/api/v1/booking/*/dispute` (va admin moderatsiya yo'llari) → 503 + `Retry-After`.

## 2. Ma'lumot ko'chirish
```bash
python scripts/migrate/feedback.py --source "$MONOLITH_DATABASE_URL" \
  --review "$REVIEW_DATABASE_URL" --dispute "$DISPUTE_DATABASE_URL" | tee feedback-cutover.json
```
- Idempotent; `inserted` == `plan`.
- `skipped`: `bronda bir nechta nizo — faqat oxirgisi` (monolit qayta ochishga ruxsat berardi) — kutilgan.
- `notes`: `mijoz foydasiga, lekin refundsiz → rejected/closed` — qo'lda ko'rib chiqing.
- Reytinglar catalog'da monolitdan (3-bosqich) — qayta hisoblash shart emas.
- Ko'chmaydi: qidiruv indeksi (4-qadamda quriladi), analitika tarixi (monolit zaxirasidan hisobot).

## 3. Servislarni yoqish
event-consumer'lar (review, dispute, search, analytics, ws-gateway), dispute scheduler (SLA), search scheduler.

## 4. Qidiruv indeksi
```bash
curl -X POST -H "Authorization: Bearer $ADMIN" https://<domen>/v1/admin/search/reindex
curl -H "Authorization: Bearer $ADMIN" https://<domen>/v1/admin/search/reindex   # status=done
```
Keyin frontend `/v1/search/*` ga o'tadi; monolit ES yozuvi to'xtatiladi.

## 5. Tekshiruv (15 daqiqa)
- `feedback-cutover.json`: kutilmagan `skipped` yo'q.
- `GET /v1/reviews?doctor_id=<faol shifokor>` monolit bilan bir xil son; `GET /v1/admin/disputes?status=open` ↔ monolit.
- `GET /v1/search/doctors?q=<ism>` — `engine=elasticsearch`, `degraded=false`.
- `GET /v1/admin/analytics/summary?from=<bugun>&to=<bugun>` — `created` o'smoqda.
- WebSocket: `wss://<domen>/ws?token=…` ulanadi, sinov to'lovida `payment.captured` keladi.
- e2e (stage): `tests/e2e/test_realtime_flow.py`.

## Orqaga qaytish
Kong'da monolit yo'llarini qayta oching. Muzlatish ichida yangi sharh/nizo tushmagan bo'lsa monolit ma'lumoti
o'zgarmagan (skript manbani faqat o'qiydi); tushgan bo'lsa — qo'lda (servisdan eksport).
