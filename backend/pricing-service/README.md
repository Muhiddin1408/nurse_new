# pricing-service

> NN=08 · Python · ichki port 8080 · host 8108 · Postgres 5408 · Redis 6308

## Vazifasi
Narx hisoblash (quote), promo kodlar, seans paketlari, birinchi bron / takroriy tashrif chegirmalari, to'lov
rejimi qarori (prepaid / deposit / at_clinic), komissiya taqsimoti. Pul — **tiyin**, yaxlitlash ROUND_HALF_UP
(`medbron_common.money`), faqat shu servisda (ADR-0007, ADR-0008, ADR-0011). Qoidalar monolitdan aynan
ko'chirilgan (C12, B8, B10):

1. Xizmat narxi → slot foizi (`price_adjust_percent`, −90..+100, schedule beradi) har xizmatga — chegirma emas.
2. **Bitta** eng katta chegirma (qo'shilmaydi): `first_booking` (platforma, admin `pricing.first_booking_discount_percent`),
   `follow_up` (shifokor, 14 kun ichida shu shifokorda yakunlangan bron), `package` (shifokor, faqat o'sha xizmat
   narxiga), `promo` (`borne_by` koddan). Noto'g'ri promo — 422 (boshqa chegirma yutsa ham bron to'xtaydi).
3. Taqsimot: `platform = total × commission_bp`, `provider = total × payme_fee_bp`, qoldiq — shifokorga;
   platforma ko'targan chegirmada shifokor chegirmasiz ulushini oladi, `platform_fee` manfiy bo'lishi mumkin.
4. To'lov rejimi: 0 → at_clinic; uy chaqiruvi → prepaid; mijozning ≥ N no-show'i (`no_show_by=client`) → prepaid;
   shifokor rejimi at_clinic / deposit (`deposit_percent`, 0 ga yaxlitlansa at_clinic) / prepaid.

**Band qilish** (ADR-0011): `commit` promo/paket seansini `reserved` qiladi → `booking.created` → `active`;
`booking.cancelled|expired` → `released` (limit va seans qaytadi; seanslar tugab yopilgan yozilish qayta
faollashadi — monolit xatosi #5 tuzatildi); 15 daqiqada `booking.created` kelmasa — scheduler bo'shatadi.
Limitlar `reserved + active` bo'yicha (parallel bronlar promo limitini oshira olmaydi — promo qatori `FOR UPDATE`).

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| POST | `/v1/pricing/quote` | user JWT | Oldindan ko'rish (band qilmaydi, komissiyani ko'rsatmaydi) |
| POST | `/v1/pricing/promo/validate` | user JWT | `{valid, reason, message, discount_tiyin}` (`other_discount_better`) |
| GET | `/v1/pricing/packages?doctor_id=` | ixtiyoriy | Shifokorning faol paketlari (`total_saving_tiyin`) |
| POST | `/v1/pricing/packages/{id}/enroll` | user JWT | Yozilish (xizmat bo'yicha bitta faol; 409 `PACKAGE_ALREADY_ACTIVE`) |
| GET | `/v1/pricing/packages/mine` | user JWT | Faol, muddati o'tmagan yozilishlar (`sessions_left`) |
| GET, POST | `/v1/doctor/packages` | user JWT, `doctor_id` claim | Shifokor paketlari (o'z xizmati yoki u ishlaydigan klinika xizmati) |
| PATCH | `/v1/doctor/packages/{id}` | shifokor | Mavjud yozilishlar o'z nusxasini saqlaydi |
| GET, POST | `/v1/admin/promo-codes` | admin `pricing.manage` | Ro'yxat (`uses`) / yaratish (audit) |
| GET, PATCH | `/v1/admin/promo-codes/{id}` | admin `pricing.manage` | O'zgartirish (audit) |
| POST | `/internal/v1/quotes` | internal: booking | `contracts/openapi/pricing-service.internal.yaml` — 10 daq Redis'da |
| POST | `/internal/v1/quotes/{id}/commit` | internal: booking | `{booking_id, client_id}`; idempotent; 404 `QUOTE_EXPIRED`, 409 `PROMO_LIMIT_REACHED` / `PACKAGE_EXHAUSTED` |

Xato kodlari: `SERVICES_UNAVAILABLE`, `DOCTOR_NOT_BOOKABLE`, `PROMO_INVALID` (`reason`: not_found | expired |
not_for_doctor | min_amount | limit_reached | already_used | first_booking_only), `QUOTE_EXPIRED`,
`PROMO_LIMIT_REACHED`, `PACKAGE_EXHAUSTED`, `PACKAGE_NOT_FOUND`, `PACKAGE_ALREADY_ACTIVE`, `SERVICE_NOT_OWNED`.

## Eventlar
| Publish | Consume |
|---|---|
| `audit.recorded` (promo kodlar) | `service.created/updated/deactivated` (xizmatlar keshi), `booking.created/confirmed/cancelled/expired/completed/no_show` (mijoz statistikasi, band qilishlar), `user.deletion_requested` (statistika o'chiriladi, yozilishlar yopiladi), `admin.settings_changed` (sozlamalar keshi tashlanadi) |

Proyeksiyalar tartibsiz/takroriy eventlarga chidamli (yangiroq `occurred_at`; yakuniy bron holati qaytarilmaydi).

## Sinxron bog'liqliklar
Chaqiradi: catalog (`bookability` — 60 s Redis kesh; `services?bookable_for=` — proyeksiyada yo'q xizmatlar va
fiskal kodlar), admin (`/internal/v1/settings`, Redis 60 s, ETag, oxirgi ma'lum qiymat zaxirasi). Timeout +
retry + circuit breaker (`ServiceClient`). Chaqiruvchilar (`/internal/v1`): booking-service.

## Konteynerlar
`pricing-service` (API), `-outbox-relay` (audit), `-event-consumer`, `-scheduler` (har daqiqa band qilish sweeper'i), `-migrate`.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker: Postgres, Redis)
uv run ruff check . && uv run mypy app

cp .env.example .env                      # yoki: python ../../scripts/dev-env.py pricing-service
docker compose up -d --build
docker compose --profile migrate run --rm pricing-service-migrate
curl localhost:8108/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `client_bookings` | har bron | 6-bosqich: 1 yildan eski yakuniy holatdagilar (hozircha tozalanmaydi) |
| `promo_redemptions`, `package_uses` | chegirmali bronlar | — (moliyaviy iz) |
| `services_cache` | xizmatlar soni | — |
| `promo_codes`, `packages`, `package_enrollments` | kichik | — |
| `outbox_events` | audit | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
