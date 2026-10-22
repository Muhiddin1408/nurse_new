# admin-service

> NN=03 · Python · ichki port 8080 · host 8103 · Postgres 5403 · Redis 6303

## Vazifasi
Platforma adminlari — user'lardan **alohida identity** (ADR-0005): login + argon2id parol + TOTP majburiy,
alohida RS256 kalit bilan admin JWT (`aud=admin`, `permissions[]`, 30 daq). Shuningdek: rollar va
permission katalogi, barcha servislarning audit jurnali, global platforma sozlamalari.

- **Kirish:** `login` → `mfa_token` (5 daq, bir martalik, Redis) → `totp` → admin JWT. Login bo'yicha
  5 xato → 15 daq blok; IP bo'yicha soatiga 30; noma'lum login uchun ham argon2 tekshiruvi (vaqt bo'yicha
  login borligini bilib bo'lmaydi); TOTP ±1 qadam, ishlatilgan qadam qayta qabul qilinmaydi (replay).
- **TOTP siri** bazada Fernet bilan shifrlangan (`TOTP_ENCRYPTION_KEY`).
- **Monolitdan ko'chirish:** Django `pbkdf2_sha256` xeshi qabul qilinadi va birinchi kirishda argon2'ga
  almashtiriladi; `AdminTOTPDevice.secret` shifrlab ko'chiriladi (`scripts/migrate/`).
- **Permission katalogi** — `app/core/permissions.py` (ARCHITECTURE 5.2 + `admins.manage`,
  `notifications.manage`, `files.read`, `payments.read`). Rol faqat katalogdagi permission'larni oladi;
  `superadmin` roli (`*`) migratsiyada yaratiladi. Oxirgi `admins.manage` egasini o'chirib bo'lmaydi.
- **Audit:** `audit.recorded` (12 ta publisher) → `audit_log` (faqat qo'shiladi, `event_id` UNIQUE).
- **Global sozlamalar** — tiplangan sxema (`app/core/platform_settings.py`, standartlar monolit
  `conf/settings.py` dan). Har bo'lim qat'iy tekshiriladi, `expected_version` bilan optimistic concurrency;
  o'zgarish → `admin.settings_changed {keys, version}` + audit (oldin/keyin).

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | Standart (ready: Postgres + Redis) |
| POST | `/v1/admin/auth/login` | — | `{login, password}` → `{mfa_token, expires_in, next: "totp"}` |
| POST | `/v1/admin/auth/totp` | — | `{mfa_token, code}` → `{access, expires_in, admin, permissions}` |
| GET | `/v1/admin/auth/me` | admin JWT | Profil, rollar, permission'lar |
| POST | `/v1/admin/auth/password` | admin JWT | `{current_password, new_password}` |
| GET, POST | `/v1/admin/admins` | `admins.manage` | Ro'yxat / yaratish → `totp_uri` (faqat shu javobda) |
| PATCH | `/v1/admin/admins/{id}` | `admins.manage` | `full_name`, `is_active`, `role_ids` |
| POST | `/v1/admin/admins/{id}/reset-totp` | `admins.manage` | Yangi TOTP sir → `totp_uri` |
| GET | `/v1/admin/permissions` | `admins.manage` | Permission katalogi |
| GET, POST | `/v1/admin/roles` | `admins.manage` | |
| PATCH, DELETE | `/v1/admin/roles/{id}` | `admins.manage` | Ishlatilayotgan rolni o'chirib bo'lmaydi (409) |
| GET | `/v1/admin/audit?actor_id=&object_type=&object_id=&action=&producer=&since=&until=&before_id=&limit=` | `audit.read` | Keyset pagination (`next_before_id`) |
| GET | `/v1/admin/settings` | `settings.manage` | `{version, values}`, `ETag` |
| PUT | `/v1/admin/settings` | `settings.manage` | `{expected_version, values: {<bo'lim>: {...}}}` — 409 / 422 |
| GET | `/internal/v1/settings` | internal: schedule, pricing, booking, payment, billing | `ETag "v<N>"`, `If-None-Match` → 304, `Cache-Control: max-age=60` |

Sozlamalar bo'limlari: `commission` (bazis punkt), `cancellation`, `payment`, `schedule`, `reminders`,
`pricing`, `payout` (tiyin), `sla`, `waitlist`, `fiscal`.

## Eventlar
| Publish | Consume |
|---|---|
| `admin.settings_changed`, `audit.recorded` (o'z amallari: admin/rol/sozlama o'zgarishi, kirish) | `audit.recorded` (navbat `admin-service.audit`) |

## Sinxron bog'liqliklar
Chaqiradi: —. Chaqiruvchilar (`/internal/v1`): schedule, pricing, booking, payment, billing (60 s keshlaydi,
`admin.settings_changed` da tashlaydi).

## Birinchi admin
```bash
docker compose run --rm admin-service python -m app.cli create-admin --login ops@medbron.uz --role superadmin
# parol so'raladi (stdin); chiqqan otpauth:// URI ni autentifikator ilovaga qo'shing
docker compose run --rm admin-service python -m app.cli reset-totp --login ops@medbron.uz
```

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker)
uv run ruff check . && uv run mypy app

../../scripts/gen-jwt-keys.sh             # secrets/private/admin-jwt-private.pem
cp .env.example .env                      # parollar, TOTP_ENCRYPTION_KEY, INTERNAL_TOKEN_*
docker compose up -d --build
docker compose --profile migrate run --rm admin-service-migrate
curl localhost:8103/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `audit_log` | har imtiyozli amal (barcha servislar) | o'chirilmaydi — qonuniy saqlash 5 yil; keyin partitsiya bo'yicha arxiv (6-bosqich runbook) |
| `admins`, `roles`, `platform_settings` | o'nlab qator | — |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
