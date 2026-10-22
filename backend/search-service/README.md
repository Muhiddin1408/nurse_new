# search-service

> NN=14 · Python · ichki port 8080 · host 8114 · Postgres 5414 · Redis 6314 · Elasticsearch 9200 (faqat 127.0.0.1)

## Vazifasi
CQRS o'qish modeli: shifokor va klinika qidiruvi (matn — imlo xatosini kechiradi, mutaxassislik, shahar, joy,
geo, bo'sh vaqt, saralash), avtoto'ldirish. Haqiqat — catalog/schedule/review; bu yerda faqat ulardan
qayta tiklanadigan nusxa. Dvigatel — **Elasticsearch 8** (2026-10-21 qarori, monolit bilan bir xil).

## Arxitektura
- **Proyeksiya Postgres'da** (`doctors`, `clinics`, `affiliations`, `services`, `specializations`): eventlar
  qisman, ES hujjati esa to'liq — har o'zgarishdan keyin ta'sirlangan hujjat proyeksiyadan TO'LIQ qayta quriladi.
- **Tartibsizlikdan himoya:** har jihat (`profile_at`, `status_at`, `rating_at`, `availability_at`, ...) o'z vaqtiga
  ega; `occurred_at` eski event qo'llanmaydi (`search_stale_events_total`).
- **ES yozuvi:** qator qulfi ostida `doc_version += 1` → `version_type=external` — parallel/takroriy yozuvda eski
  hujjat yangisini ustidan yozmaydi.
- **Alias:** o'qish/yozish `doctors`/`clinics` alias'i orqali, fizik indeks `<alias>_v<N>`.
- **Reindex** (`POST /v1/admin/search/reindex`, 2026-10-21 qarori): scheduler yangi `_v<N+1>` indeksni catalog
  `GET /internal/v1/export/*` dan to'ldiradi; shu davrda eventlar IKKALA indeksga yoziladi; oxirida alias atomik
  almashtiriladi, eski indeks o'chiriladi. Xatoda — alias eski indeksda, yangilari o'chiriladi.
- **Zaxira:** ES javob bermasa qidiruv Postgres proyeksiyasidan (`engine=postgres`, `degraded=true`, faqat matn +
  mutaxassislik, `total=null`). Alert — `search_requests_total{engine="postgres"}`.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis + Elasticsearch |
| GET | `/v1/search/doctors?q=&specialization=&city=&place=clinic\|home&lat=&lng=&radius_km=&available_on=&sort=relevance\|rating\|price\|distance\|availability&limit=&offset=` | — | Faqat bron qilsa bo'ladigan (tasdiqlangan + kamida bitta taklif) |
| GET | `/v1/search/clinics?q=&specialization=&city=&lat=&lng=&radius_km=&sort=` | — | Faqat faol klinikalar |
| GET | `/v1/search/suggest?q=` (≥ 2 belgi) | — | Shifokorlar, klinikalar (`search_as_you_type`), mutaxassisliklar |
| POST | `/v1/admin/search/reindex` | `search.manage` | 202, navbatga; bir vaqtda bitta (409 `REINDEX_RUNNING`) |
| GET | `/v1/admin/search/reindex` | `search.manage` | Oxirgi urinishlar (kim, qachon, natija) |

Qoidalar: geo — klinikada `radius_km` ichidagi klinika; uyda — mijoz manzili shifokorning o'z
`home_visit_radius_km` i ichida (uy bazasi faqat reindex snapshot'idan). `available_on` — "shu kungacha bo'sh
vaqti bor" (`next_available_at ≤ kun oxiri`). Sahifalash `offset ≤ 1000`, `limit ≤ 50`. Javob Redis'da 15 s.

## Eventlar
| Publish | Consume |
|---|---|
| — | `doctor.approved`, `doctor.profile_updated`, `doctor.suspended`, `doctor.reinstated`, `clinic.published`, `clinic.updated`, `clinic.suspended`, `affiliation.accepted`, `affiliation.paused`, `affiliation.resumed`, `affiliation.ended`, `service.created`, `service.updated`, `service.deactivated`, `schedule.availability_changed`, `rating.recalculated` |

## Sinxron bog'liqliklar
Chaqiradi: `catalog-service` — FAQAT reindex va mutaxassisliklar lug'ati (`/internal/v1/export/*`). Oddiy
qidiruv yo'lida sinxron chaqiruv yo'q. Chaqiruvchilar (`/internal/v1`): —.

## Worker'lar
| Konteyner | Vazifa |
|---|---|
| `search-service-event-consumer` | yuqoridagi 16 event → proyeksiya → ES (ishga tushganda alias'larni yaratadi) |
| `search-service-scheduler` | reindex navbati (5 s), mutaxassisliklar lug'ati (03:30 + ishga tushganda) |

Outbox relay yo'q — servis hech narsa publish qilmaydi.

## Muhit o'zgaruvchilari
`.env.example` da hammasi izoh bilan. Muhimlari: `CATALOG_SERVICE_URL`, `INTERNAL_TOKEN` (catalog'da
`INTERNAL_TOKEN_SEARCH_SERVICE`), `ELASTICSEARCH_URL`, `ELASTICSEARCH_USERNAME/PASSWORD` (prod), `ES_JAVA_OPTS`,
`INDEX_PREFIX`, `SEARCH_CACHE_SECONDS`.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # Docker kerak (testcontainers: Postgres, Redis, RabbitMQ, Elasticsearch 8)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm search-service-migrate
curl localhost:8114/health/ready
# bo'sh indeksni catalog'dan to'ldirish (birinchi deploy):
curl -XPOST -H "Authorization: Bearer <admin>" localhost:8114/v1/admin/search/reindex
```

## Ma'lumot o'sishi
| Jadval / indeks | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| proyeksiya jadvallari | shifokor/klinika/xizmat soni | o'smaydi (upsert) |
| `reindex_runs` | har reindex | saqlanadi (audit izi) |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
| ES `doctors_v<N>`, `clinics_v<N>` | shifokor/klinika soni | reindex eski versiyani o'chiradi |
