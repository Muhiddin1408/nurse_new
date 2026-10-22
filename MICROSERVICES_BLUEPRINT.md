# Mikroservis arxitekturasi — Claude Code uchun to'liq prompt (Trasso andozasi)

> **Qanday ishlatiladi:** bu faylni yangi proyekt ildiziga `CLAUDE.md` (yoki `docs/BLUEPRINT.md`) qilib qo'ying va Claude Code'ga yozing:
> *"BLUEPRINT.md dagi arxitekturani shu proyekt uchun qur. Avval 0-bosqichni bajar."*
>
> `{{...}}` bilan belgilangan joylarni o'zingizning proyektingizga moslab to'ldiring (yoki Claude o'zi so'raydi).

---

## 0. Sen kimsan va vazifang nima

Sen — tajribali backend arxitektor va muhandissan. Vazifang: **{{PROJECT_NAME}}** proyekti uchun production darajasidagi mikroservis backend qurish. Arxitektura "Trasso" proyektida sinovdan o'tgan andozaga asoslanadi (logistika / taksi / yuk tashish platformasi, 29 ta servis), lekin u yerda topilgan xatolar bu yerda **takrorlanmasligi** shart (12-bo'limga qara).

**Domen:** {{DOMAIN_DESCRIPTION — masalan: "yetkazib berish platformasi: mijoz buyurtma beradi, kuryer oladi, to'lov onlayn"}}

**Klientlar:** {{CLIENTS — masalan: Flutter mobil ilova (mijoz + haydovchi), admin web panel, biznes portal, Telegram bot}}

### Ishlash qoidalari (majburiy)
1. **Bosqichma-bosqich ishla** (13-bo'lim). Har bosqich oxirida nima qilinganini, nima tekshirilganini va keyingi qadamni qisqa yoz.
2. Kod yozishdan oldin **`docs/ARCHITECTURE.md`**, **`docs/SERVICE_REGISTRY.md`** va **`contracts/events/`** ni yarat va menga ko'rsat. Ular — yagona haqiqat manbai (single source of truth). Kod ularga zid bo'lmasligi kerak.
3. Har yangi servis **servis shabloni**dan (5-bo'lim) ko'chiriladi — "o'zimcha" tuzilma yo'q.
4. Biror narsa noaniq bo'lsa (domen qoidasi, to'lov provayderi, SMS provayderi) — taxmin qilma, **so'ra**.
5. Har servis: testlar yashil, `docker compose up` ishlaydi, `/health/live` va `/health/ready` 200 qaytaradi — shundan keyingina "tayyor".
6. Hech qachon maxfiy kalit, `.env`, `.pem`, `.p8`, `.jks`, `.keystore` faylini repoga qo'shma.

---

## 1. Arxitektura tamoyillari

| # | Tamoyil | Ma'nosi |
|---|---|---|
| 1 | **Database-per-service** | Har servisning o'z Postgres bazasi. Boshqa servis bazasiga **hech qachon** to'g'ridan-to'g'ri ulanmaydi. |
| 2 | **Async-first** | Holat o'zgarishi haqida xabar — **event** (RabbitMQ). Sinxron HTTP faqat "hozir javob kerak" bo'lganda (narx hisoblash, ruxsat tekshirish, ma'lumot o'qish). |
| 3 | **Transactional Outbox** | Event DB tranzaksiyasi ichida `outbox_events` ga yoziladi, alohida relay jarayoni brokerga chiqaradi. "DB'ga yozildi, lekin event yo'qoldi" holati bo'lmaydi. |
| 4 | **Idempotent consumer** | Har consumer `processed_events` jadvali orqali dublikatni o'tkazib yuboradi (at-least-once delivery). |
| 5 | **Stateless auth** | JWT **RS256**. Faqat auth-service (user) va admin-service (admin) **private key** bilan imzolaydi; qolgan servislar **public key** bilan lokal tekshiradi — tarmoq chaqiruvisiz. |
| 6 | **Internal API ajratilgan** | Servislararo endpointlar faqat `/internal/v1/*` ostida, `X-Internal-Token` header bilan. Gateway ularni tashqariga **ochmaydi**. |
| 7 | **Sinxron bog'liqlik grafi — DAG** | Servislararo HTTP chaqiruvlarda **sikl bo'lmasligi kerak** (A→B→A taqiqlangan). Teskari yo'nalish faqat event orqali. |
| 8 | **Yagona event bus** | Bitta broker, bitta asosiy topic exchange. Per-servis broker yoki per-domen exchange **yo'q**. |
| 9 | **Kontrakt birinchi** | Event va internal API sxemalari `contracts/` da yoziladi; kod ulardan keyin. |
| 10 | **Observability standart** | Har servisda: strukturali log, Prometheus `/metrics`, OpenTelemetry tracing, Sentry. |
| 11 | **Immutable deploy** | Docker image tegi = commit SHA. `latest` ishlatilmaydi. Migratsiya — deploy'ning alohida qadami. |

---

## 2. Texnologiyalar steki

| Qatlam | Tanlov |
|---|---|
| Asosiy servislar | **Python 3.12 + FastAPI**, Pydantic v2, pydantic-settings, SQLAlchemy 2 (async) + asyncpg, Alembic, httpx, aio-pika, redis-py, PyJWT[crypto] |
| Yuqori yuklamali servislar (matching, real-time, GPS, OTP) | **Go 1.22+**: chi/echo, pgx, go-redis, amqp091-go, envconfig, OpenTelemetry |
| Ma'lumotlar | PostgreSQL 16 (kerak bo'lsa PostGIS), Redis 7, ClickHouse (analitika / GPS tarixi), MinIO (S3) |
| Broker | RabbitMQ 3.13 (topic exchange + DLX + retry) |
| API Gateway | Kong (DB-less, deklarativ `kong.yml`) |
| Real-time | ws-gateway (Go) + Redis pub/sub fan-out |
| Observability | Prometheus, OpenTelemetry → Jaeger, Sentry |
| CI/CD | GitLab CI (yoki GitHub Actions), umumiy shablonlar, Docker Compose deploy |
| Python paket menejeri | `uv` (Dockerfile ichida) |

---

## 3. Repo tuzilmasi

```
{{project}}/
├── docs/
│   ├── ARCHITECTURE.md          # umumiy sxema, oqimlar, qarorlar (ADR)
│   ├── SERVICE_REGISTRY.md      # servis → port, DB, Redis, egasi, bog'liqliklar
│   └── adr/0001-*.md            # arxitektura qarorlari
├── contracts/
│   ├── events/                  # har event uchun JSON Schema + catalog.yaml
│   │   ├── catalog.yaml         # event_type → publisher, consumers, schema, versiya
│   │   └── order.created.v1.json
│   └── openapi/                 # internal API OpenAPI fayllari
├── libs/
│   ├── py-common/               # umumiy Python paket (outbox, consumer, security, http client, telemetry)
│   └── go-common/               # umumiy Go modul (outbox, consumer, auth, httpapi middleware)
├── backend/
│   ├── infra/                   # RabbitMQ, DLX bootstrap, Jaeger, (PgBouncer), umumiy docker network
│   ├── gateway-kong/            # kong.dev.yml, kong.prod.yml
│   ├── ws-gateway/              # Go
│   └── <name>-service/          # har servis — shablon bo'yicha
├── ci-templates/                # backend-service.yml, web-app.yml
├── scripts/
│   ├── new-service.sh           # shablondan yangi servis yaratish
│   ├── gen-jwt-keys.sh          # RS256 kalit juftligi (faqat lokal, gitignore'da)
│   └── check-contracts.py       # event catalog vs kod tekshiruvi (CI'da)
└── docker-compose.dev.yml       # hamma servisni lokal ko'tarish (ixtiyoriy)
```

> **Eslatma:** Trasso'da umumiy kod (`outbox_relay`, `event_publisher`, `security.py`) har servisga **nusxa ko'chirilgan** — 25 ta nusxa, har biri asta-sekin farqlanib ketgan. Bu yerda ular `libs/py-common` va `libs/go-common` da **bir marta** yoziladi va versiyalanadi.

---

## 4. Servislar katalogi (andoza)

Domeningizga mosini tanlang. Kerak bo'lmaganini olib tashlang, yangisini shu formatda qo'shing. **Har servis bitta biznes imkoniyatiga (bounded context) egalik qiladi.**

| Servis | Til | Egasi bo'lgan ma'lumot / vazifa |
|---|---|---|
| **auth-service** | Py | Telefon + OTP login, user JWT chiqarish (RS256 private key), refresh token, rollar |
| **otp-service** | Go | OTP kodlar (yetkazishda tasdiqlash va h.k.), rate-limit |
| **user-service** | Py | Profil, manzillar, referal, foydalanuvchi o'chirish |
| **admin-service** | Py | Admin login (alohida admin JWT), rollar/permissionlar, audit log, global sozlamalar (`/internal/v1/settings`) |
| **kyc-service** | Py | Hujjat tekshiruvi (shaxs, guvohnoma) |
| **file-service** | Py | MinIO ustida yuklash/yuklab olish, presigned URL, thumbnail |
| **driver-service** | Py | Haydovchi/ijrochi profili, holati, onlayn/oflayn |
| **vehicle-service** | Py | Transport vositalari, hujjatlari |
| **order-service** | Py | Buyurtma hayot sikli (state machine) — **faqat orkestratsiya, hisob-kitob emas** |
| **pricing-service** | Py | Narx hisoblash, tariflar, surge |
| **matching-service** | Go | Buyurtmaga ijrochi topish (ranking, auksion) |
| **route-service** | Go | Marshrut, ETA, OSRM bilan ishlash |
| **tracking-service** | Go | GPS qabul qilish, geofence, ClickHouse'ga tarix |
| **geocoding-service** | Py | Manzil ↔ koordinata, cache, kvota |
| **payment-service** | Py | To'lov provayderlari, hold/capture/refund, ledger (double-entry) |
| **billing-service** | Py | Hisob-faktura, obuna, komissiya, payout |
| **notification-service** | Py | SMS, Telegram, email — provayder abstraksiyasi |
| **device-service** | Py | Push token'lar, push yuborish (FCM/APNs) |
| **chat-service** | Py | Mijoz ↔ ijrochi chat |
| **rating-service** | Py | Baholash |
| **dispute-service** | Py | Nizolar, dalillar |
| **support-service** | Py | Qo'llab-quvvatlash tiketlari |
| **analytics-service** | Py | Barcha eventlarni ClickHouse'ga yig'ish, hisobotlar |
| **content-service** | Py | Blog, FAQ, statik kontent |
| **ws-gateway** | Go | WebSocket — eventlarni klientlarga real-time yetkazish |
| **gateway-kong** | — | Tashqi kirish nuqtasi |

---

## 5. Servis shabloni

### 5.1 Python servis tuzilmasi

```
<name>-service/
├── app/
│   ├── main.py                 # FastAPI app, lifespan, middleware, /health, /metrics
│   ├── core/
│   │   ├── config.py           # pydantic-settings, get_settings() lru_cache
│   │   ├── database.py         # async engine, AsyncSessionLocal, Base
│   │   ├── security.py         # get_current_user / get_current_admin / verify_internal_token (libs'dan)
│   │   ├── permissions.py      # require_permission("drivers.read")
│   │   └── telemetry.py        # OTel setup (env bo'lmasa no-op)
│   ├── models/                 # SQLAlchemy modellar + outbox.py + processed_event.py
│   ├── schemas/                # Pydantic request/response
│   ├── repositories/           # faqat DB so'rovlari
│   ├── services/               # biznes logika (routerlar va repositorylar orasida)
│   ├── routers/
│   │   ├── <name>.py           # /v1/<name>       — user JWT
│   │   ├── admin_<name>.py     # /v1/admin/<name> — admin JWT + permission
│   │   └── internal_<name>.py  # /internal/v1/<name> — X-Internal-Token
│   ├── clients/                # boshqa servislarga HTTP clientlar (base.py dan meros)
│   └── workers/
│       ├── outbox_relay.py     # alohida konteyner: python -m app.workers.outbox_relay
│       └── event_consumer.py   # alohida konteyner: python -m app.workers.event_consumer
├── migrations/                 # Alembic
├── tests/                      # pytest + pytest-asyncio, testcontainers
├── Dockerfile                  # multi-stage, uv, non-root user, HEALTHCHECK
├── docker-compose.yml          # postgres, redis, app, outbox-relay, event-consumer
├── .env.example                # BARCHA o'zgaruvchilar, izoh bilan, haqiqiy qiymatsiz
├── .gitignore                  # .env, secrets/, *.pem, *.p8, *.jks
├── .gitlab-ci.yml              # ci-templates/backend-service.yml ni include qiladi
├── pyproject.toml              # versiyalar QAT'IY (==)
└── README.md                   # nima qiladi, endpointlar, eventlar, ishga tushirish
```

**Qatlam qoidasi:** `routers → services → repositories → models`. Router DB'ga to'g'ridan-to'g'ri murojaat qilmaydi; repository biznes qaror qabul qilmaydi; `clients/` faqat `services/` dan chaqiriladi.

### 5.2 Go servis tuzilmasi

```
<name>-service/
├── cmd/
│   ├── <name>-service/main.go  # HTTP (+gRPC) server
│   ├── <name>-relay/main.go    # outbox relay
│   └── <name>-consumer/main.go # event consumer (kerak bo'lsa)
├── internal/
│   ├── config/                 # envconfig
│   ├── domain/                 # entity, biznes qoidalar
│   ├── service/                # use-case'lar
│   ├── store/                  # pgx repositorylar + outbox.go
│   ├── cache/                  # redis
│   ├── clients/<svc>/          # boshqa servis clientlari
│   ├── eventbus/               # consumer
│   ├── outbox/                 # publisher.go, relay.go
│   ├── auth/                   # JWT RS256 verify
│   ├── httpapi/{handlers,middleware}/
│   └── telemetry/
├── migrations/                 # golang-migrate SQL
├── docs/openapi.yaml
├── Dockerfile, docker-compose.yml, Makefile, .env.example, README.md
```

### 5.3 Har servisda majburiy endpointlar
- `GET /health/live` — jarayon tirik (DB tekshirilmaydi)
- `GET /health/ready` — DB, Redis, (kerak bo'lsa) broker ulanishi
- `GET /metrics` — Prometheus (`http_requests_total`, `http_request_duration_seconds`)
- `X-Request-ID` middleware — kelmasa yaratiladi, logga va chiquvchi so'rovlarga uzatiladi
- Xato formati yagona: `{"detail": {"code": "ORDER_NOT_FOUND", "message": "..."}}`

---

## 6. Autentifikatsiya va avtorizatsiya

1. **User JWT** — auth-service RS256 private key bilan imzolaydi. Claim'lar: `sub` (user_id), `role`, `roles[]`, `phone`, `iss`, `exp`, `type=access`. Access TTL 15–60 daqiqa, refresh token DB'da (rotatsiya bilan).
2. **Admin JWT** — admin-service imzolaydi, `aud=admin`, `permissions[]` claim ichida. Admin token user endpointiga kirsa → `403 WRONG_AUDIENCE`.
3. Har servis public key'ni `/secrets/jwt-public.pem` va `/secrets/admin-jwt-public.pem` dan o'qiydi (volume, read-only). **DB lookup yo'q.**
4. **Internal token:** `X-Internal-Token` — har servis uchun alohida token (`INTERNAL_TOKEN_<CALLER>`), qabul qiluvchi ruxsat etilgan chaqiruvchilar ro'yxatini tekshiradi. `hmac.compare_digest` bilan solishtiriladi.
5. **Resurs darajasidagi ruxsat (authz):** "bu buyurtma shu userga tegishlimi" — servis o'zi tekshiradi; Kong faqat marshrutlaydi.
6. Kong'da: rate-limiting (`/v1/auth`, `/v1/otp` — qattiq), CORS, request-size-limiting, `/internal/*` tashqariga **yopiq**.

---

## 7. Servislararo aloqa

### 7.1 Qachon nima ishlatiladi

| Holat | Mexanizm |
|---|---|
| Javob darhol kerak (narx, ruxsat, ma'lumot o'qish) | Sinxron HTTP `/internal/v1/*` |
| "X sodir bo'ldi" — boshqalar reaksiya qiladi | Event (RabbitMQ) |
| Ko'p qadamli biznes jarayon (to'lov → buyurtma → payout) | Saga: eventlar zanjiri + kompensatsiya eventlari |
| Klientga real-time | Event → ws-gateway → Redis pub/sub → WebSocket |
| Juda tez-tez, past kechikishli chaqiruv (matching ↔ route) | gRPC (ixtiyoriy) |

### 7.2 HTTP client standarti (`libs/py-common/http_client.py`)
- `httpx.AsyncClient`, **bitta** client har servis uchun (connection pooling)
- Timeout majburiy: connect 2s, read 5s (servis bo'yicha sozlanadi)
- Retry: faqat idempotent so'rovlar (GET, `Idempotency-Key` li POST), exponential backoff + jitter, max 3
- **Circuit breaker** (masalan, 5 ta ketma-ket xato → 30s ochiq)
- `X-Internal-Token`, `X-Request-ID` va `traceparent` avtomatik qo'shiladi
- Fallback: ixtiyoriy ma'lumot (masalan, haydovchi avatari) kelmasa so'rov yiqilmaydi

### 7.3 Bog'liqlik qoidasi
- `docs/SERVICE_REGISTRY.md` da har servis uchun `calls:` ro'yxati yuritiladi.
- CI'da skript sinxron chaqiruv grafini tekshiradi va **sikl topilsa build yiqiladi**.
- Bitta servis **6 tadan ortiq** servisni sinxron chaqirsa — bu signal: mas'uliyatni bo'lish yoki eventga o'tkazish kerak.

---

## 8. Event bus (RabbitMQ)

### 8.1 Topologiya (`backend/infra/`)
- **Bitta broker** (`{{prefix}}-rabbitmq`), barcha servislar umumiy external docker network (`{{project}}-net`) orqali ulanadi.
- **Bitta asosiy exchange:** `{{project}}.events` (topic, durable). **Hamma** publisher shu yerga yozadi. Per-domen exchange (`order.topic`, `ride.events`) **yaratilmaydi**.
- `dlx` (topic) → `{{project}}.dlq` (barcha dead-letter'lar).
- `{{project}}.retry` exchange + TTL'li navbatlar (5s, 30s, 5m) → muddat tugagach asosiy exchange'ga qaytadi.
- Topologiyani `infra/eventbus_topology.py` bir marta, idempotent tarzda e'lon qiladi (`dlx-bootstrap` konteyneri, `restart: no`).
- Healthcheck: `rabbitmq-diagnostics ping` **emas** — oddiy TCP tekshiruv (`</dev/tcp/127.0.0.1/5672`). Ping Erlang tugunini ko'taradi va yuklangan serverda brokerni bo'g'adi.

### 8.2 Nomlash
- Routing key = `event_type` = `<domen>.<o'tgan_zamon_fe'l>`: `order.created`, `order.delivered`, `payment.captured`, `driver.blocked`
- Navbat nomi: `<servis>.<maqsad>` — masalan `notification-service.order-events`. **Har consumer servisda bitta navbat**, kerakli routing key'lar unga bind qilinadi (har event uchun alohida navbat emas).
- Navbat argumentlari: `x-dead-letter-exchange=dlx`, `durable=true`, kerak bo'lsa `x-queue-type=quorum`.

### 8.3 Envelope (barcha eventlar uchun yagona)
```json
{
  "event_id": "uuid",
  "event_type": "order.created",
  "event_version": 1,
  "occurred_at": "2026-01-01T12:00:00Z",
  "producer": "order-service",
  "trace_id": "...",
  "aggregate_type": "order",
  "aggregate_id": "uuid",
  "data": { ... }
}
```
AMQP xususiyatlari: `message_id=event_id`, `type=event_type`, `content_type=application/json`, `delivery_mode=PERSISTENT`. Publisher kanali `publisher_confirms=True`.

### 8.4 Outbox (har publisher servisda)
Jadval `outbox_events`: `id, event_id (unique), aggregate_type, aggregate_id, event_type, payload JSONB, status (pending|published|failed), attempts, next_attempt_at, last_error, created_at, published_at`. Indeks: `next_attempt_at WHERE status='pending'`.

Relay algoritmi:
1. `SELECT ... WHERE status='pending' AND next_attempt_at<=now() ORDER BY created_at LIMIT N FOR UPDATE SKIP LOCKED`
2. Har qatorni publish qil (timeout bilan) → `published` qil
3. Xato bo'lsa → `attempts++`, `next_attempt_at = now() + min(base*2^(n-1), max)`; `attempts>=MAX` → `failed` + alert
4. Batch to'la bo'lsa darhol takrorla, aks holda `POLL_INTERVAL` kut
5. SIGTERM'da toza to'xta
6. Eski `published` qatorlarni tozalovchi cron (masalan, 7 kundan keyin)

**Muhim:** biznes yozuvi va outbox yozuvi **bitta tranzaksiyada**. Servis kodida brokerga to'g'ridan-to'g'ri `publish()` chaqirish **taqiqlangan**.

### 8.5 Consumer
- `prefetch_count` sozlanadi (10–50).
- `processed_events(event_id PK, event_type, processed_at)` — `INSERT ... ON CONFLICT DO NOTHING RETURNING` bilan idempotentlik; dublikat bo'lsa ack qilib o'tkazib yuboriladi. Iloji bo'lsa, idempotentlik belgisi va biznes o'zgarishi **bitta tranzaksiyada** yoziladi.
- Xato turlari:
  - vaqtinchalik (tarmoq, DB lock) → retry navbatiga (`x-retry-count` header bilan), limitdan keyin DLQ;
  - doimiy (yaroqsiz JSON, sxema mos emas) → darhol DLQ.
- Xatoni **yutib yubormaslik** kerak (log qilib ack qilish = event yo'qotish).
- Noma'lum `event_version` → log + DLQ.

### 8.6 Event katalogi (`contracts/events/catalog.yaml`)
```yaml
order.created:
  version: 1
  publisher: order-service
  consumers: [matching-service, analytics-service, ws-gateway]
  schema: order.created.v1.json
  description: Yangi buyurtma yaratildi, matching boshlanadi
```
CI tekshiruvi (`scripts/check-contracts.py`):
- koddagi har `publish("x.y")` katalogda bormi;
- har consumer bind qilgan key'ning publisher'i bormi (**publisher'siz consumer = xato**);
- payload JSON Schema'ga mosmi (testlarda).

Event kalitlari kodda string bilan emas, **`libs/` dagi konstantalar / enum** orqali ishlatiladi (`Events.ORDER_CREATED`).

---

## 9. Ma'lumotlar qatlami

- Har servis — o'z Postgres bazasi va o'z DB useri. Prod'da markaziy PgBouncer (transaction pooling) orqali ulanish mumkin, lekin bazalar baribir alohida.
- Docker ichida `DATABASE_URL_DOCKER` ustun keladi; `DATABASE_URL` host'dan Docker'siz ishga tushirish uchun.
- Migratsiyalar: Alembic / golang-migrate. **Container CMD'da migratsiya qilinmaydi** — CI deploy'da `up -d` dan oldin alohida qadam (`MIGRATE_CMD`).
- Pul: `NUMERIC(18,2)` yoki tiyinlarda `BIGINT`, hech qachon `float`. To'lovlar uchun double-entry ledger.
- Barcha vaqtlar `timestamptz`, UTC.
- ID — UUID (v4 yoki v7).
- Multi-tenant ma'lumot (B2B tashkilotlar) bo'lsa — Postgres RLS.
- Boshqa servis ma'lumoti kerak bo'lsa: (a) sinxron so'rov, yoki (b) event orqali **lokal proyeksiya/kesh** jadvali (eventual consistency, egasi baribir bitta servis).

---

## 10. Gateway va real-time

### Kong (`backend/gateway-kong/`)
- `kong.dev.yml`, `kong.prod.yml` — deklarativ, DB-less.
- Har servis uchun route guruhlari: `<svc>-user` (`/v1/<x>`), `<svc>-admin` (`/v1/admin/<x>`). `/internal/*` prod'da **e'lon qilinmaydi**.
- Global pluginlar: CORS, request-size-limiting, prometheus, correlation-id (`X-Request-ID`).
- Auth endpointlariga qattiq rate-limiting.
- MinIO bucket'larini ochish kerak bo'lsa — faqat ommaviy bucket'lar; maxfiy hujjatlar (KYC) faqat presigned URL orqali.

### ws-gateway (Go)
- Klient `wss://.../ws?token=<JWT>` bilan ulanadi, JWT lokal tekshiriladi.
- Obuna (`order:<id>`, `driver:<id>`) — resursga ruxsat tegishli servisning `/internal/v1/.../authz` endpointi orqali tekshiriladi (natija Redis'da qisqa muddat keshlanadi).
- RabbitMQ'dan `order.#`, `match.#`, `tracking.#` va h.k. ni o'qiydi → Redis pub/sub (`rtws:<channel>`) → shu kanalga obuna bo'lgan har bir ws-gateway nusxasi klientlarga yuboradi (gorizontal masshtab).
- Event → WS xabar mapping'i alohida faylda va test bilan qoplangan.
- Ping/pong, backpressure (sekin klientni uzish), ulanish limiti.

---

## 11. Observability, CI/CD, xavfsizlik

### Observability
- Log: JSON, maydonlar: `ts, level, service, request_id, trace_id, msg`. Shaxsiy ma'lumot (telefon, karta) maskalanadi.
- Tracing: OpenTelemetry (FastAPI, SQLAlchemy, httpx, aio-pika instrumentatsiyasi) → `OTEL_EXPORTER_OTLP_ENDPOINT` (Jaeger). Env bo'lmasa — no-op.
- Metrikalar: HTTP + biznes (`orders_created_total`, `outbox_pending`, `consumer_lag`, `dlq_messages`).
- Alertlar: DLQ > 0, outbox `failed` > 0, `outbox_pending` o'sib bormoqda, 5xx ulushi, p95 latency.

### Docker
- Multi-stage build, `uv` bilan o'rnatish, non-root `app` user, `HEALTHCHECK` → `/health/live`.
- `docker-compose.yml` da: `postgres`, `redis`, `<svc>`, `<svc>-outbox-relay`, `<svc>-event-consumer` (bitta image, turli `command`).
- `init: true`, `restart: unless-stopped`, healthcheck'larda `start_period`.
- Tashqi network: `{{project}}-net` (`external: true`). Servislar bir-biriga **konteyner nomi va ichki port** bilan murojaat qiladi.
- Host portlari **faqat** `SERVICE_REGISTRY.md` dan olinadi — takrorlanish taqiqlangan.

### `docs/SERVICE_REGISTRY.md` (majburiy jadval)
| Servis | Til | Ichki port | Host port | Postgres host port | Redis host port | Publishes | Consumes | Calls (sync) |
|---|---|---|---|---|---|---|---|---|

Qoidalar:
- Ichki port **hamma Python servislarda 8080**, Go'da 8080 (gRPC 9090) — bir xil. Farq faqat host portda.
- Host portlar diapazon bo'yicha: app `81xx`, Postgres `54xx`, Redis `63xx`. Har servisga bitta raqam (masalan, order = 12 → 8112, 5412, 6312).
- `.env.example` qiymatlari reestrga mos bo'lishi kerak (CI tekshiradi).

### CI/CD (`ci-templates/`)
- Bosqichlar: `lint` (ruff/mypy, golangci-lint) → `test` → `contracts-check` → `build` (tag = `$CI_COMMIT_SHA`) → `migrate` → `deploy`.
- `dev` branch → dev serverga avtomatik; `main` → prod (qo'lda tasdiqlash bilan).
- Deploy: `docker compose pull && docker compose up -d --no-deps $APP_SERVICES` → health tekshiruvi → yiqilsa loglar chiqariladi va job qizil.
- `.env` va `secrets/` serverda turadi, CI ularga tegmaydi.

### Xavfsizlik
- `.gitignore`: `.env`, `secrets/`, `*.pem`, `*.key`, `*.p8`, `*.jks`, `*.keystore`, `google-services.json`. Pre-commit'da `gitleaks`.
- Mobil imzo kalitlari va APNs kalitlari — faqat CI secret / vault.
- RabbitMQ, Postgres, MinIO — `guest/guest` yoki standart parollar **yo'q**, hatto dev'da ham `.env` orqali.
- To'lov webhook'lari: imzo tekshiruvi + idempotentlik + IP allowlist.
- OTP / login: rate-limit (IP + telefon), brute-force bloklash.

---

## 12. Trasso'da topilgan xatolar — bu yerda TAKRORLANMASIN

| # | Trasso'dagi muammo | Bu yerdagi qoida |
|---|---|---|
| 1 | `order-service` 18 ta servisni sinxron chaqiradi va 26k qatorgacha o'sgan ("distributed monolith") | 7.3: max 6 sinxron bog'liqlik, orkestratsiya va hisob-kitob ajratiladi |
| 2 | Sinxron sikllar: order↔matching, order↔payment, order↔ride, user↔auth, driver↔vehicle | Sinxron graf DAG, CI tekshiradi |
| 3 | Aralash exchange'lar: `hamroh-yuk.events`, `ride.events`, `kyc.topic`, `*.cross_events`. Natijada ba'zi consumerlar (masalan driver-service `match.found`, `order.completed` ni `match.topic`/`order.topic` dan kutadi) eventni **hech qachon olmaydi** | Bitta exchange (8.1) |
| 4 | Publisher'i yo'q consumer kalitlari: `rating.new` (aslida `rating.created`), `order.driver_accepted`, `user.role_upgraded` | Event katalogi + CI tekshiruvi (8.6), kalitlar konstantada |
| 5 | Har event uchun alohida navbat (`driver-service.<event>`) | Har servisda bitta navbat, ko'p binding |
| 6 | Consumer xatoni log qilib ack qiladi — event jim yo'qoladi | Retry → DLQ (8.5) |
| 7 | Umumiy kod (outbox, publisher, security) 25 ta servisga nusxalangan | `libs/py-common`, `libs/go-common` |
| 8 | `.env.example` lar eskirgan: RabbitMQ porti 5672/5673/5675/5680; servis porti har joyda boshqacha | Ichki port standart, SERVICE_REGISTRY + CI tekshiruvi |
| 9 | Host port to'qnashuvlari (analytics = ride: 5450/6393/8097; Redis 6392 uch joyda) | Reestrdagi raqamlash sxemasi |
| 10 | `.p8` va `.jks` kalitlar repo papkasida, `.gitignore` da yo'q | 11-bo'lim, gitleaks |
| 11 | Ikki xil OTP yo'li (auth ichida va otp-service) | Har vazifaning bitta egasi |

---

## 13. Bajarish rejasi (bosqichlar)

**0-bosqich — Loyihalash (kod yo'q)**
- Domenni tahlil qil, bounded context'larni aniqla, 4-bo'limdan servislar ro'yxatini tanla va asosla.
- Yarat: `docs/ARCHITECTURE.md` (komponent diagrammasi — Mermaid, asosiy biznes oqimlari sequence diagram bilan), `docs/SERVICE_REGISTRY.md`, `contracts/events/catalog.yaml` (barcha eventlar), sinxron bog'liqlik grafi (DAG ekanini ko'rsat).
- **To'xta va tasdiqlashimni kut.**

**1-bosqich — Poydevor**
- `backend/infra/` (RabbitMQ + DLX/retry bootstrap + Jaeger + network), `libs/py-common`, `libs/go-common` (outbox, consumer, security, http client, telemetry, health) — unit testlar bilan.
- `scripts/new-service.sh`, `scripts/gen-jwt-keys.sh`, `scripts/check-contracts.py`, `ci-templates/`.

**2-bosqich — Identity**
- auth-service, admin-service, user-service, otp-service, file-service, notification-service. Login → JWT → boshqa servisda tekshirish end-to-end ishlashi kerak.

**3-bosqich — Asosiy domen**
- Asosiy oqim servislari (masalan: order, pricing, matching, route, driver, vehicle). Asosiy biznes oqimi end-to-end test bilan (`tests/e2e/`).

**4-bosqich — Pul**
- payment-service (ledger, provayder adapterlari, webhook), billing. Saga + kompensatsiya testlari.

**5-bosqich — Real-time va qo'shimchalar**
- tracking, ws-gateway, device (push), chat, rating, dispute, support, analytics, content.

**6-bosqich — Gateway va prod tayyorgarlik**
- Kong konfiguratsiyasi, yuklama testi (k6), alertlar, runbook (`docs/runbooks/`), xavfsizlik tekshiruvi.

---

## 14. "Tayyor" mezoni (har servis uchun checklist)

- [ ] Shablon tuzilmasiga mos, qatlam qoidasi buzilmagan
- [ ] `README.md`: vazifasi, endpointlar, publish/consume qilinadigan eventlar, env o'zgaruvchilar
- [ ] `.env.example` to'liq va SERVICE_REGISTRY'ga mos
- [ ] Alembic migratsiyalar, `outbox_events` va `processed_events` jadvallari (kerak bo'lsa)
- [ ] Barcha eventlar outbox orqali, katalogda bor, JSON Schema bilan
- [ ] Consumer idempotent, retry/DLQ ishlaydi
- [ ] `/health/live`, `/health/ready`, `/metrics`, tracing ulangan
- [ ] User / admin / internal routerlar ajratilgan, auth dependency'lar qo'yilgan
- [ ] HTTP clientlarda timeout + retry + circuit breaker
- [ ] Unit + integratsiya testlari (testcontainers bilan Postgres/Redis/RabbitMQ), coverage ≥ 70%
- [ ] `ruff`, `mypy` (yoki `golangci-lint`) toza
- [ ] `docker compose up` bilan ko'tariladi, healthcheck'lar yashil
- [ ] Kong'ga route qo'shilgan (internal'dan tashqari)
- [ ] Hech qanday maxfiy ma'lumot commit qilinmagan
