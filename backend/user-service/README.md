# user-service

> NN=02 · Python · ichki port 8080 · host 8102 · Postgres 5402 · Redis 6302

## Vazifasi
Profil (ism, til, avatar), bemorlar (oila a'zolari), manzillar, sevimli shifokorlar, akkauntni o'chirish
so'rovi va faol bronlar proyeksiyasi. Telefon va rollar — auth-service; profil `user_id` bo'yicha (UUID bir xil).

- **Egalik** har so'rov ichida (`WHERE user_id = :sub`): begona bemor/manzil — 404 (IDOR himoyasi).
- **Profil** `user.registered` dan yaratiladi; event kechiksa — birinchi `GET /v1/users/me` da (natija bir xil).
- **Avatar:** file-service'da yuklangan va tekshirilgan fayl `attach` qilinadi; eskisi `release` (o'chiriladi).
- **O'chirish** (ARCHITECTURE 6.10) = anonimlashtirish: faol bron bo'lsa `409 ACTIVE_BOOKINGS_EXIST`;
  aks holda profil, bemorlar, manzillar tozalanadi, sevimlilar o'chadi → `user.deletion_requested`
  (auth akkauntni yopadi va raqamni bo'shatadi, notification kontaktni o'chiradi). Shifokor akkaunti — qo'llab-quvvatlash orqali.
- **Bemor o'chirish:** faol broni bor — 409; bron tarixi bor — yumshoq (`is_deleted`); hech bron qilinmagan — DELETE.
  Manzil — har doim yumshoq (bron o'z nusxasini saqlaydi).
- **Bronlar proyeksiyasi** (`booking_states`) — tartibsiz/takroriy eventlarga chidamli: yangiroq `occurred_at`
  qo'llanadi, yakuniy holat (`cancelled`, `expired`, `completed`, `no_show`) qaytarilmaydi.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| GET, PATCH | `/v1/users/me` | user JWT | `{full_name?, preferred_language?, avatar_file_id?}` (null — avatarni olib tashlash) |
| DELETE | `/v1/users/me` | user JWT | Akkauntni o'chirish (anonimlashtirish) |
| GET, POST | `/v1/users/me/patients` | user JWT | Ro'yxat / qo'shish (≤ 20) |
| GET, PATCH, DELETE | `/v1/users/me/patients/{id}` | user JWT | |
| GET, POST | `/v1/users/me/addresses` | user JWT | Bitta standart manzil (`is_default`) |
| GET, PATCH, DELETE | `/v1/users/me/addresses/{id}` | user JWT | |
| GET | `/v1/users/me/favorites` | user JWT | |
| PUT, DELETE | `/v1/users/me/favorites/{doctor_id}` | user JWT | Idempotent |
| GET | `/v1/admin/users/{id}/profile` | admin `users.manage` | Qo'llab-quvvatlash (faqat o'qish) |
| GET | `/internal/v1/users/{user_id}/patients/{patient_id}` | internal: booking | Egalik tekshiruvi bilan; boshqasiniki — 404 |
| GET | `/internal/v1/users/{user_id}/addresses/{address_id}` | internal: booking | Shunday |

## Eventlar
| Publish | Consume |
|---|---|
| `user.profile_updated` (ism/til o'zgarsa), `user.deletion_requested`, `audit.recorded` | `user.registered`, `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.expired`, `booking.completed`, `booking.no_show` |

## Sinxron bog'liqliklar
Chaqiradi: file-service (`attach`/`release`; timeout + retry (idempotency key) + circuit breaker; file-service
ishlamasa faqat avatar o'zgartirish 503). Chaqiruvchilar (`/internal/v1`): booking-service.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm user-service-migrate
curl localhost:8102/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `profiles` | har foydalanuvchi | o'chirilmaydi (anonimlashtiriladi) |
| `patients`, `addresses` | ≤ 20 / user | yumshoq o'chirish |
| `favorites` | ≤ 200 / user | — |
| `booking_states` | har bron | 6-bosqich: yakuniy holatdagi 1 yildan eski qatorlar (hozircha tozalanmaydi) |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
