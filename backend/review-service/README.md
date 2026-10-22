# review-service

> NN=12 · Python · ichki port 8080 · host 8112 · Postgres 5412 · Redis 6312

## Vazifasi
Sharhlar (C11): yakunlangan bronga bitta sharh, avtomatik moderatsiya bayroqlari (so'kinish, telefon, havola),
moderator qarori, shifokorning bir martalik javobi va reyting hisoblash. Reyting saqlanmaydi — har o'zgarishda
`published` sharhlardan hisoblanib `rating.recalculated` bilan catalog'ga (denormalizatsiya, search ham oladi).

Holatlar: `pending` (bayroq bor) → `published | rejected`; `published` → `rejected` (shikoyat bo'yicha olib
tashlash). Reyting `MIN_REVIEWS_FOR_RATING` (5) dan kam bo'lsa `rating=null`; shifokor va klinika bo'yicha.

## Qoidalar
- Faqat o'z yakunlangan broniga, yakunlanganidan keyin admin `sla.review_window_days` (30) ichida; bitta bronga
  bitta (UNIQUE, parallel so'rov — 409).
- Egalik — `booking.completed` proyeksiyasi (`reviewable_bookings`); kelmagan bo'lsa — booking
  `review-eligibility` fallback (klinika keyin event bilan to'ldiriladi).
- Ism servisda saqlanmaydi: mijoz ilovasi `author_name` yuboradi, ommaviy ko'rinish — "Aziz T.", anonim — yo'q.
- Shifokor javobi — bir marta, telefon/havola/so'kinishsiz.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| POST | `/v1/reviews` | user JWT | `{booking_id, rating 1–5, comment, is_anonymous, author_name}` → 201; 404 begona, 409 `NOT_REVIEWABLE` / `REVIEW_WINDOW_CLOSED` / `ALREADY_REVIEWED` |
| GET | `/v1/reviews?doctor_id=\|clinic_id=&limit=&offset=` | — | Faqat published |
| GET | `/v1/reviews/mine` | user JWT | Holat va rad etish sababi bilan |
| GET | `/v1/doctor/reviews` | doctor JWT | O'z published sharhlari |
| POST | `/v1/doctor/reviews/{id}/reply` | doctor JWT | Bir marta (409 `ALREADY_REPLIED`) |
| GET | `/v1/admin/reviews?status=pending` | `reviews.moderate` | Moderatsiya navbati (bo'sh `status` — hammasi) |
| POST | `/v1/admin/reviews/{id}/publish`, `/{id}/reject` `{reason}` | `reviews.moderate` | Audit bilan |

Internal API yo'q.

## Eventlar
| Publish | Consume |
|---|---|
| `review.submitted`, `review.published`, `review.rejected`, `rating.recalculated`, `audit.recorded` | `booking.completed` (proyeksiya), `admin.settings_changed` |

## Sinxron bog'liqliklar
Chaqiradi: `booking-service` (`/internal/v1/bookings/{id}/review-eligibility` — faqat fallback), `admin-service`
(sozlamalar: `sla.review_window_days`).

## Muhit o'zgaruvchilari
`.env.example`: `BOOKING_SERVICE_URL`, `ADMIN_SERVICE_URL`, `MIN_REVIEWS_FOR_RATING`, `MAX_COMMENT_LENGTH`.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # Docker kerak (testcontainers)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm review-service-migrate
curl localhost:8112/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `reviews` | har sharh | saqlanadi |
| `reviewable_bookings` | har yakunlangan bron | saqlanadi (kichik qator) |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
