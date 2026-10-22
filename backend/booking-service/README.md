# booking-service

> NN=09 · Python · ichki port 8080 · host 8109 · Postgres 5409 · Redis 6309

## Vazifasi
Bron va uning holat mashinasi (orkestratsiya): yaratish sagasi, to'lov bilan tasdiqlash, bekor qilish siyosati,
ko'chirish, shifokor qabullari (boshlash / yakunlash / kelmadi), waitlist, "yana bron qilish", klinika va admin
ko'rinishlari. Narx — pricing quote'idan muzlatilgan snapshot (tiyin); bemor va manzil — bron paytidagi nusxa.

Holatlar: `pending_payment → confirmed → in_progress → completed | no_show`; `pending_payment → expired`;
`pending_payment|confirmed → cancelled`. Yakuniy holatga qayta o'tish — idempotent. Har o'tish `FOR UPDATE`.

## Yaratish sagasi (ADR-0011)
1. `Idempotency-Key` (ixtiyoriy, tavsiya) — `(client_id, key)` UNIQUE qator **birinchi**; takror so'rov → 200 o'sha
   bron, boshqa tana → 422, bajarilmoqda → 409. Saga yiqilsa kalit bo'shatiladi.
2. Parallel o'qishlar: catalog `bookability` + `services?bookable_for=` (A3), user bemor/manzil egaligi (A1),
   schedule slot (A2: shifokor, joy turi, davomiylik sig'ishi, **lead time**), admin sozlamalari.
3. Tekshiruvlar: uy chaqiruvi radiusi va yo'l buferi (C4), bemor yoshi/jinsi (C6), mijozda ≤ 3 to'lanmagan bron,
   bemorning vaqti kesishmasligi.
4. pricing `quote` (hold'dan oldin) → schedule `hold(hold_ref=booking_id)` → pricing `commit` →
   (to'lov kerak bo'lmasa schedule `confirm`) → bron + `booking.created` (+ `booking.confirmed`) bitta tx.
5. Kompensatsiya: xatoda hold `release` qilinadi; javob kelmasa ham hold TTL bilan, pricing band qilishi
   15 daqiqada o'zi bo'shaydi. Slot band — 409 `SLOT_TAKEN` + ≤ 3 muqobil vaqt.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| POST | `/v1/bookings` | user JWT | Bron yaratish (201 yangi / 200 takror) |
| GET | `/v1/bookings/mine?status=&date_from=&date_to=&cursor=&limit=` | user JWT | Cursor pagination |
| GET | `/v1/bookings/{id}` | user JWT (egasi) | |
| GET | `/v1/bookings/{id}/cancellation-preview` | user JWT | `{allowed, refund_tiyin, penalty_tiyin, reason_code}` |
| POST | `/v1/bookings/{id}/cancel` | user JWT | Siyosat: > 24 soat — 100%, > 2 soat — 50%, aks holda 0 (admin sozlamalari) |
| POST | `/v1/bookings/{id}/reschedule` | user JWT | `{new_slot_id}`; ≤ 2 marta, eski qabulgacha ≥ 24 soat; narx o'zgarmaydi |
| GET | `/v1/bookings/{id}/rebook` | user JWT | Shu shifokor/xizmatlar va ≤ 5 bo'sh vaqt (hech narsa yaratmaydi) |
| GET, POST | `/v1/waitlist` | user JWT | Bo'sh vaqt chiqsa xabar (band qilinmaydi) |
| DELETE | `/v1/waitlist/{id}` | user JWT | |
| GET | `/v1/doctor/appointments/today`, `/v1/doctor/appointments?from=&to=&status=` | shifokor (JWT `doctor_id`) | To'lanmagan bronlar ko'rinmaydi; batafsil manzil faqat qabul kuni |
| GET | `/v1/doctor/appointments/{id}` | shifokor | + bemorning shu shifokordagi tarixi |
| POST | `/v1/doctor/appointments/{id}/start` \| `complete` \| `no-show` \| `cancel` | shifokor | start — `start_at − 15 daq` dan; cancel — mijozga 100% |
| GET | `/v1/clinic/{clinic_id}/appointments?date=&q=` | klinika admini (catalog `access`, 60 s kesh) | |
| GET | `/v1/admin/bookings`, `/v1/admin/bookings/{id}` | `bookings.read` | |
| POST | `/v1/admin/bookings/{id}/complete` \| `no-show` \| `cancel` | `bookings.manage` | no-show `absent=doctor` — mijozga 100% |
| GET | `/internal/v1/bookings/{id}/payable` | payment | + fiskal elementlar (bo'sh kod — admin standarti) |
| POST | `/internal/v1/bookings/{id}/extend-payment` | payment | hold va `payment_due_at` +15 daq (faqat uzaytirish) |
| GET | `/internal/v1/bookings/{id}/review-eligibility?client_id=` | review | |
| GET | `/internal/v1/bookings/{id}/summary?user_id=` | dispute | |
| GET | `/internal/v1/bookings/{id}/authz?user_id=&doctor_id=` | ws-gateway | |

## Eventlar
| Publish | Consume |
|---|---|
| `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.expired`, `booking.rescheduled`, `booking.started`, `booking.completed`, `booking.no_show`, `booking.confirmation_failed`, `waitlist.slot_offered`, `audit.recorded` | `payment.captured` (schedule `confirm` → confirmed; slot yo'qolgan/bron yopilgan → `confirmation_failed`), `payment.reversed`, `refund.succeeded`, `slot.released` (hold muddati → pending bron `expired`; waitlist taklifi), `doctor.suspended`, `clinic.suspended`, `affiliation.ended`, `user.deletion_requested` (kelajakdagi bronlar tizim bekor qiladi, 100%), `admin.settings_changed` (kesh) |

## Sinxron bog'liqliklar
Chaqiradi (5/6): catalog, schedule, pricing, user, admin — timeout, retry (GET va idempotency kalitli POST),
circuit breaker. Chaqiruvchilar: payment, review, dispute, ws-gateway.

## Konteynerlar
`booking-service` (API), `-outbox-relay`, `-event-consumer`, `-scheduler` (expire — har daqiqa, avto-yakunlash —
soatlik, waitlist/idempotency tozalash — kunlik), `-migrate` (profil).

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker); upstream'lar — tests/fakes.py
uv run ruff check . && uv run mypy app
cp .env.example .env && docker compose up -d --build
docker compose --profile migrate run --rm booking-service-migrate
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `bookings`, `booking_items` | har bron | o'chirilmaydi (moliyaviy tarix) |
| `idempotency_keys` | har yaratish so'rovi | scheduler: 7 kundan keyin |
| `waitlist_entries` | navbat yozuvlari | muddati o'tganlari `expired` (qator qoladi) |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
