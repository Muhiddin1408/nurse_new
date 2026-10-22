# Incident runbook

Alert kelganda shu yerdan boshlang. Navbatchi har bir incidentda: **1) ta'sirni baholaydi → 2) to'xtatadi → 3) tuzatadi → 4) yozib qoldiradi.**
Buyruqlar prod hostida, servis papkasidan (`backend/<servis>/`; har servisning o'z `docker-compose.yml` i). `X-Request-ID` — loglarni servislar bo'ylab bog'lash kaliti.

## Alert → bo'lim

| Alert | Bo'lim |
|---|---|
| GatewayHigh5xx, ServiceDown, High5xxRate, CircuitBreakerOpen | [#gateway](#gateway), [#servis](#servis) |
| PaymentWebhookInternalError, PaymentWebhookErrorRateHigh | [#tolov](#tolov) |
| RefundFailed, RefundManualQueue, RefundWorkerStale | [#qaytarish](#qaytarish) |
| PaymentDiscrepancy, ReconciliationNotRun | [#solishtirish](#solishtirish) |
| BookingConfirmationFailed | [#bron](#bron) |
| DeadLetterQueueNotEmpty, ConsumerDeadLettering | [dlq.md](dlq.md) |
| OutboxEventFailed, OutboxPendingGrowing, ConsumerLagHigh, WorkerDown, PeriodicJobFailing | [#eventlar](#eventlar) |
| SearchDegraded, SearchReindexFailed | [#qidiruv](#qidiruv) |
| RealtimeDeliveryStalled | [#realtime](#realtime) |
| AnalyticsIngestFailing | [#analitika](#analitika) |
| DisputeSlaBreached | [#nizo](#nizo) |
| SlotEndpointSlow | [#servis](#servis) |

## Umumiy

```bash
docker compose ps                                  # qaysi konteyner tushgan / qayta ishga tushmoqda
docker compose logs --since 15m <servis> | grep -i error
curl -s localhost:8080/health/ready                # konteyner ichida: DB, Redis, RabbitMQ holati
```

- Oxirgi deploy'dan keyin boshlangan bo'lsa — **avval orqaga qaytaring** (oldingi image tag), sababini keyin qidiring.
- Pul bilan bog'liq incident (to'lov, qaytarish, solishtirish) — darhol mas'ul dasturchi va moliya bo'limiga xabar.

## gateway

1. Grafana: Kong 5xx qaysi `service` da — bitta servismi yoki hammasimi?
2. Hammasi bo'lsa — Kong o'zi: `docker compose -f gateway-kong/docker-compose.yml logs --since 10m kong`.
   Konfiguratsiya o'zgargan bo'lsa: `kong config parse` bilan tekshiring, oldingi `kong.prod.yml` ni qaytaring.
3. 502/504 bitta servisda — [#servis](#servis). Kong DNS keshi (servis IP o'zgargan): `KONG_DNS_VALID_TTL=5`, kerak bo'lsa Kong'ni qayta ishga tushiring.
4. 429 ko'paygan — rate-limit Redis'ga ulanishni tekshiring (`fault_tolerant: true` — Redis yo'q bo'lsa limit o'chadi, so'rov o'tadi).

## servis

1. `docker compose logs --since 15m <servis>` — traceback, DB timeout, `CircuitBreakerOpen`.
2. `/health/ready` 503 — qaysi bog'liqlik: Postgres (ulanishlar soni, disk), Redis, RabbitMQ.
3. CircuitBreakerOpen — chaqirilayotgan **boshqa** servis tushgan; uni tuzating, breaker o'zi yopiladi.
4. SlotEndpointSlow — schedule-service: Postgres sekin so'rovlar (`pg_stat_statements`), slot generatsiyasi bir vaqtda ishlayaptimi.
5. Yuklama sababli — replika qo'shing (servislar stateless).

## tolov

Payme/Click webhook'i xato qaytarsa provayder qayta yuboradi, lekin to'lov **mijozda yechilgan, bizda tasdiqlanmagan** holatda qolishi mumkin.

1. `provider_logs` jadvali (payment-service DB): oxirgi so'rov/javoblar, `X-Request-ID`.
2. 401/403 ko'p — kalit yoki IP o'zgargan: `PAYME_SECRET_KEY`/`CLICK_SECRET_KEY`, `*_ALLOWED_IPS` va Kong `ip-restriction` (`render_prod.py`) provayder hujjati bilan solishtiring.
3. 5xx — payment-service logi; DB yoki booking-service (`/internal`) javob bermayapti.
4. Tuzatilgandan keyin provayder qayta yuborgan webhook'lar idempotent qabul qilinadi; qolgan farqlar — [#solishtirish](#solishtirish).

## qaytarish

1. `GET /v1/admin/refunds?status=failed` (admin JWT) — xato matni `last_error`.
2. Vaqtinchalik xato (provayder timeout) — `POST /v1/admin/refunds/{id}/retry`.
3. Provayder orqali qaytarib bo'lmaydi (karta yopilgan va h.k.) — qo'lda (bank) qaytaring, keyin `POST /v1/admin/refunds/{id}/complete`.
4. RefundWorkerStale — payment `scheduler` konteyneri ishlayaptimi: `docker compose ps payment-service-scheduler` (`backend/payment-service/` da).

## solishtirish

1. `GET /v1/admin/reconciliation` — farqlar ro'yxati (bizda bor/provayderda yo'q yoki aksincha, summa farqi).
2. Provayder reyestri qo'lda: `POST /v1/admin/reconciliation/registry`.
3. Har bir farq sababini aniqlang va `POST /v1/admin/reconciliation/{id}/resolve` bilan izoh qoldiring. **Ledger yozuvini qo'lda o'zgartirmang** — faqat kompensatsiya yozuvi.
4. ReconciliationNotRun — payment `scheduler` va provayder API kalitlari.

## bron

To'lov o'tgan, bron tasdiqlanmagan (slot band qilib bo'lmadi).
1. booking-service logi `X-Request-ID` bo'yicha; schedule-service slot holati.
2. Avtomatik kompensatsiya (refund) boshlanganini tekshiring — [#qaytarish](#qaytarish).
3. Mijozga qo'ng'iroq: boshqa vaqt yoki pulni qaytarish.

## eventlar

1. OutboxPendingGrowing/OutboxEventFailed — servisning `outbox-relay` konteyneri va RabbitMQ (`rabbitmqctl list_queues`).
2. ConsumerLagHigh — `event-consumer` replikalarini oshiring; sekin handler — log.
3. WorkerDown — `docker compose ps | grep -E 'outbox-relay|event-consumer|scheduler|dispatcher'`, qayta ishga tushiring.
4. PeriodicJobFailing — `scheduler` logida qaysi job.
5. Xabar DLQ'ga tushgan bo'lsa — [dlq.md](dlq.md).

## qidiruv

search-service Elasticsearch'siz ham ishlaydi: Postgres zaxirasi (faqat matn + mutaxassislik, javobda `degraded=true`).
1. Elasticsearch: `curl -s es:9200/_cluster/health` — `red` bo'lsa disk/heap.
2. Indeks buzilgan/eskirgan — qayta indekslash (catalog export'dan): `POST /v1/admin/search/reindex`, holati `GET /v1/admin/search/reindex`.
3. SearchReindexFailed — catalog-service export endpointi va search `event-consumer` logi.

## realtime

1. ws-gateway logi va Redis pub/sub (`redis-cli pubsub channels`).
2. Notification `dispatcher` ishlayaptimi — eventlar ws-gateway'ga yetib kelyaptimi.
3. Klientlar avtomatik qayta ulanadi; ma'lumot yo'qolmaydi (sahifa REST bilan yangilanadi).

## analitika

Biznes oqimiga ta'sir qilmaydi — kechiktirish mumkin. analytics `event-consumer` logi va DB diski; tuzatilgach eventlar navbatdan qayta o'qiladi.

## nizo

SLA muddati o'tgan nizolar: admin panel → "Nizolar" (muddat bo'yicha saralangan). Qaror qabul qiling yoki mas'ulga topshiring.

## Incidentdan keyin

Qisqa yozuv (`docs/incidents/YYYY-MM-DD-<nom>.md`): nima bo'ldi, ta'sir (qancha mijoz/summa), vaqt chizig'i, sabab, takrorlanmasligi uchun chora (alert, test, kod).
