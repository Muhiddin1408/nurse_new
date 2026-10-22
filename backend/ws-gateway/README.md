# ws-gateway

> NN=16 · Go · ichki port 8080 · host 8116 · Postgres — · Redis 6316

## Vazifasi
Real-time yetkazish (ADR-0003): eventlar (bron holati, to'lov, jadval, waitlist taklifi) WebSocket orqali
klientga darhol yetadi — masalan, "To'lov tekshirilmoqda" → `payment.captured` → `booking.confirmed`
(ARCHITECTURE 6.2). Ma'lumot saqlamaydi; manba — RabbitMQ eventlari, holatni klient REST'dan oladi.

Asosiy qoidalar:
- **Kanallar:** `user:<id>` (avtomatik), `booking:<id>` (booking `/authz` — mijoz yoki shu bron shifokori;
  natija Redis'da 60 s), `doctor:<id>:schedule` (faqat token'dagi `doctor_id`). Begona/mavjud bo'lmagan bron —
  bir xil `FORBIDDEN` (A4).
- **PII minimal:** har event uchun oq ro'yxatdagi maydonlar (`internal/domain`) — bemor, manzil, pul taqsimoti
  chiqmaydi.
- **Gorizontal masshtab:** consumer → Redis `rtws:<kanal>`; HAR API nusxasi `rtws:*` ga obuna va faqat o'z
  ulanishlariga yuboradi. Nusxalar bir-birini bilmaydi.
- **Idempotentlik:** go-common consumer Postgres `processed_events` talab qiladi, bu yerda Postgres yo'q —
  shuning uchun `internal/eventbus` dagi consumer topologiya/retry/DLQ'ni go-common bilan bir xil qiladi,
  takrorni esa Redis `SET NX` (`EVENT_DEDUP_TTL`) bilan taniydi; handler xatosida belgi o'chiriladi (retry).
- **Backpressure:** har ulanishda chegaralangan navbat; to'lsa — klient 4008 bilan uziladi, tarqatish
  to'xtamaydi. Ping/pong, yozish timeout'i, xabar hajmi limiti.
- **Xavfsizlik:** user JWT (RS256), Origin oq ro'yxati (brauzer; Origin'siz mobil — token yetarli), ulanish
  limiti (foydalanuvchi × nusxa), token muddati tugaganda 4001, `user.deactivated` — 4003.

## Tuzilma
| Papka | Vazifasi |
|---|---|
| `cmd/ws-gateway` | WebSocket server + Redis `rtws:*` listener, graceful shutdown (1001) |
| `cmd/ws-gateway-consumer` | Event consumer → Redis pub/sub (alohida konteyner) |
| `internal/domain` | Kanallar, event → xabar xaritasi (sof) |
| `internal/realtime` | Hub (lokal ulanishlar), Client (read/write pump), Bus (Redis / lokal) |
| `internal/eventbus` | Consumer (Redis dedup, retry/DLQ) va 14 ta handler |
| `internal/clients` | booking `/authz` (kesh bilan) |
| `internal/httpapi` | `/ws` handshake, buyruqlar, marshrutlar |
| `internal/integration` | Redis + RabbitMQ bilan oqim testlari |

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Redis + RabbitMQ |
| GET | `/ws?token=<JWT>` (yoki `Authorization: Bearer`) | user JWT | WebSocket; protokol — `docs/openapi.yaml` |

## Eventlar
| Publish | Consume |
|---|---|
| — | `user.deactivated` (ulanishlar yopiladi), `schedule.availability_changed`, `booking.*` (9 ta), `waitlist.slot_offered`, `payment.captured`, `payment.failed` |

## Sinxron bog'liqliklar
Chaqiradi: `booking-service` (`/internal/v1/bookings/{id}/authz`). Chaqiruvchilar: —.

## Metrikalar
`ws_connections`, `ws_messages_queued_total`, `ws_messages_sent_total`, `ws_disconnects_total{reason}`,
`ws_rejected_total{what,reason}`, `ws_bus_published_total{result}`, `ws_bus_delivered_total`,
`ws_events_consumed_total{event_type,result}`.

## Ishga tushirish
```bash
make test                     # unit (Docker'siz)
make test-integration         # + Redis + RabbitMQ (testcontainers)
make lint

cp .env.example .env
docker compose up -d --build
curl localhost:8116/health/ready
```
