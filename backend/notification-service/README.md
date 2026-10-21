# notification-service

> NN=05 · Python · ichki port 8080 · host 8105 · Postgres 5405 · Redis 6305

## Vazifasi
Barcha bildirishnomalar: SMS (Eskiz), Telegram bot, mobil push (faqat **FCM** — Android va iOS FCM orqali),
shablonlar (uz/ru), rejalashtirilgan eslatmalar, shifokorning kunlik digest'i va ops ogohlantirishlari.
**Hech kimni sinxron chaqirmaydi** — hamma ma'lumot eventlar va lokal proyeksiyalardan (pastki oqim
servisi: ishlamay qolsa bron/to'lov to'xtamaydi). OTP SMS bu yerda EMAS — auth-service o'zi (ADR-0004).

- **Xabarlar outbox'i** (`notifications`): event handler faqat qator yozadi — event bilan BITTA tranzaksiyada
  (`dedupe_key = event:<event_id>:<shablon>:<user>` → takroriy event takroriy SMS bermaydi). Yuborish —
  `dispatcher` konteyneri: SKIP LOCKED + 60 s lease (jarayon o'lsa xabar yo'qolmaydi), backoff 1/5/15/60 daq,
  5 urinishdan keyin `failed`. Provayder sekinlashsa event qayta ishlash to'xtamaydi.
- **Kanal ustuvorligi:** push (bepul) → Telegram (bepul) → SMS (pullik). Tranzaksion xabar YETKAZILISHI
  shart: bepul kanallar ishlamasa SMS ketadi (`sms_enabled=false` bo'lsa ham). Marketing — rozilik bilan, SMS'siz.
- **Til va format yuborish paytida:** parametrlar xom saqlanadi (`when` ISO, `amount_tiyin`, `place`) —
  vaqt Asia/Tashkent'da, summa so'mda.
- **Eslatmalar** (`booking.confirmed` da): T-24 soat, T-2 soat, qabuldan +2 soat baholash so'rovi. Vaqti
  o'tgani yaratilmaydi. Ikki qatlamli himoya: bekor/muddati o'tishda kutilayotganlari `cancelled`; yuborish
  paytida bron holati proyeksiyadan QAYTA tekshiriladi. Baholash so'rovi bron `completed` bo'lguncha
  1 soatdan kutadi (≤ 30 marta). Ko'chirishda eslatmalar qayta rejalashtiriladi.
- **Telegram bot:** `/start <token>` — akkauntni bog'lash (HMAC token, 15 daq, bitta chat — bitta akkaunt),
  `/today`, `/tomorrow` — shifokor qabullari (proyeksiyadan; bemor ismi/manzili ko'rsatilmaydi), `/stop`.
  Webhook `X-Telegram-Bot-Api-Secret-Token` bilan (sir sozlanmagan — endpoint yopiq).
- **Ops:** refund yiqildi, pul yechilib bron yo'q, reconciliation farqi, yangi nizo/SLA, yangi ariza — Telegram ops chat.

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| GET, PATCH | `/v1/notifications/preferences` | user JWT | `push_enabled`, `telegram_enabled`, `sms_enabled`, `marketing_opt_in` (+ `telegram_linked`) |
| POST, DELETE | `/v1/notifications/devices` | user JWT | `{token, platform: android|ios}` — FCM token (token boshqa akkauntga o'tsa — ko'chadi) |
| POST | `/v1/telegram/link-token` | user JWT | `{url: t.me/<bot>?start=<token>, expires_in, linked}` |
| POST | `/v1/telegram/webhook` | Telegram secret header | Bot yangilanishlari |
| GET | `/v1/admin/notifications/templates` | admin `notifications.manage` | Standart + o'zgartirilgan matnlar, parametrlar |
| PUT, DELETE | `/v1/admin/notifications/templates/{key}/{uz|ru}` | admin `notifications.manage` | Parametrlar standart bilan bir xil bo'lishi shart |
| GET | `/v1/admin/notifications/logs?user_id=&status=&template=&before=&limit=` | admin `notifications.manage` | Yuborish jurnali (matn saqlanmaydi) |

Til (`preferred_language`) — user-service profilida (`PATCH /v1/users/me`); bu yerga `user.profile_updated` bilan keladi.

## Eventlar (36 ta consume, publish yo'q)
| Guruh | Eventlar → natija |
|---|---|
| Kontakt | `user.registered` (telefon), `user.profile_updated` (ism, til), `user.roles_changed`, `user.deactivated` / `user.reactivated`, `user.deletion_requested` (telefon, ism, Telegram, qurilmalar o'chadi; navbat bekor) |
| Shifokor | `doctor.onboarding_submitted` (+ops), `doctor.approved` (kontakt `doctor_id → user_id`), `doctor.rejected`, `doctor.suspended`, `doctor.reinstated`, `doctor.profile_updated`, `doctor.license_expiring`, `affiliation.invited`, `affiliation.ended`, `clinic.member_added` |
| Bron | `booking.created` (faqat proyeksiya), `booking.confirmed` (mijoz + shifokor + eslatmalar), `booking.cancelled`, `booking.expired`, `booking.rescheduled`, `booking.completed`, `booking.no_show`, `booking.confirmation_failed` (+ops), `waitlist.slot_offered` |
| Pul | `payment.reversed`, `refund.succeeded`, `refund.failed` (ops), `payment.discrepancy_found` (ops), `payout.calculated`, `payout.paid` |
| Sharh/nizo | `review.published` (shifokorga), `review.rejected`, `dispute.opened` (ops), `dispute.resolved`, `dispute.sla_breached` (ops) |

## Konteynerlar
`notification-service` (API + bot webhook), `-event-consumer` (eventlar → proyeksiya + xabarlar outbox'i),
`-dispatcher` (yuborish, eslatmalar, 07:00 digest, jurnal tozalash), `-migrate` (profil). Outbox relay yo'q
(servis event publish qilmaydi).

## Sirlar
`.env`: `ESKIZ_*`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_LINK_SECRET` (majburiy).
FCM service account: `base64 -w0 service-account.json` → `FCM_CREDENTIALS_JSON=...` ni
`secrets/private/notification-service.env` ga (repoga EMAS; faqat dispatcher konteyneriga ulanadi).
Prod: `SMS_PROVIDER=eskiz`, `PUSH_PROVIDER=fcm|off` (console taqiqlangan).

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker)
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm notification-service-migrate
curl localhost:8105/health/ready
# Telegram webhook (bir marta):
curl -F "url=https://api.medbron.uz/v1/telegram/webhook" -F "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
  "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook"
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `notifications` | har xabar (~3–6 / bron) | dispatcher: yakunlanganlari 90 kundan keyin (kunlik) |
| `booking_projection` | har bron | 6-bosqich: 1 yildan eski yakuniy holatdagilar (hozircha tozalanmaydi) |
| `contacts`, `push_devices`, `doctor_contacts` | foydalanuvchi soni | o'chirishda tozalanadi |
| `templates` | o'nlab qator | — |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
