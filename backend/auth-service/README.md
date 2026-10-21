# auth-service

> NN=01 · Python · ichki port 8080 · host 8101 · Postgres 5401 · Redis 6301

## Vazifasi
Foydalanuvchi identifikatori (telefon), OTP orqali kirish, token juftligi va sessiyalar, rollar.
User access tokenni (RS256, `kid`) **faqat shu servis** imzolaydi; boshqa servislar uni public kalit bilan
tarmoqsiz tekshiradi (ARCHITECTURE 5.1). Profil (ism, til, bemorlar) — user-service.

- **OTP** (ADR-0004): kod HMAC bilan saqlanadi, 5 daqiqa, 5 urinish; SMS to'g'ridan-to'g'ri Eskiz'ga
  (login yo'lida navbat yo'q). SMS yetmasa — **503** (fail-closed), limitlar qaytariladi.
- **Rate-limit** (Redis, fail-closed): raqam bo'yicha 60 s cooldown va soatiga 5 kod, IP bo'yicha soatiga 20,
  raqam bo'yicha soatiga 10 ta noto'g'ri kod — keyin 1 soat blok. Kong qo'shimcha ravishda IP bo'yicha cheklaydi.
- **Refresh** — opaque (faqat SHA-256 xeshi bazada), 30 kun, har yangilashda rotatsiya. Rotatsiya qilingan
  token qayta kelsa — o'g'irlangan deb hisoblanadi, butun sessiya (`family_id`) bekor qilinadi (`REFRESH_REUSED`).
- **Rollar** ma'lumotdan: `client` — har doim; `doctor` — `doctor.approved` (to'xtatilganda ham saqlanadi);
  `clinic_admin` — faol klinika a'zoligi. Rol o'zgarishi keyingi token yangilanishida (≤ 15 daq) kuchga kiradi.
  `platform_admin` endi user roli emas — admin-service (ADR-0005).

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | Standart (ready: Postgres + Redis) |
| POST | `/v1/auth/otp/request` | — | `{phone, language?}` → `{detail, expires_in, retry_after}`; javob raqam bor-yo'qligidan qat'i nazar bir xil |
| POST | `/v1/auth/otp/verify` | — | `{phone, code}` → `{access, refresh, expires_in, is_new_user, user, tokens}` (`tokens` — monolit javobi bilan moslik) |
| POST | `/v1/auth/token/refresh` | — | `{refresh}` → yangi juftlik (rotatsiya) |
| POST | `/v1/auth/switch-role` | user JWT | `{refresh, role}` → yangi juftlik; rol keyingi kirishda eslab qolinadi |
| POST | `/v1/auth/logout` | user JWT | `{refresh}` — joriy qurilma sessiyasi |
| POST | `/v1/auth/logout/all` | user JWT | Barcha qurilmalar |
| GET | `/v1/auth/me` | user JWT | `id, phone, roles, active_role, doctor_id` |
| GET | `/v1/auth/.well-known/jwks.json` | — | Public kalitlar (RFC 7517), 5 daq kesh |
| GET | `/v1/admin/users?phone=&user_id=&is_active=&limit=&offset=` | admin `users.manage` | Qidiruv |
| POST | `/v1/admin/users/{id}/block` | admin `users.manage` | `{reason}` → sessiyalar bekor, `user.deactivated`, audit |
| POST | `/v1/admin/users/{id}/unblock` | admin `users.manage` | → `user.reactivated`, audit (o'chirilgan akkaunt tiklanmaydi) |

Internal API yo'q (leaf servis) — `/internal/v1/auth/*` hammaga yopiq.

Xato kodlari: `INVALID_PHONE`, `INVALID_CODE`, `OTP_ATTEMPTS_EXCEEDED`, `OTP_COOLDOWN` (`Retry-After`),
`OTP_PHONE_LIMIT`, `OTP_IP_LIMIT`, `OTP_VERIFY_BLOCKED`, `SMS_UNAVAILABLE`, `RATE_LIMITER_UNAVAILABLE`,
`ACCOUNT_BLOCKED`, `REFRESH_INVALID`, `REFRESH_REUSED`, `ROLE_NOT_AVAILABLE`.

## Eventlar
| Publish | Consume |
|---|---|
| `user.registered` (birinchi verify), `user.roles_changed`, `user.deactivated` (blok / o'chirish), `user.reactivated`, `audit.recorded` | `user.deletion_requested` (raqam bo'shatiladi, sessiyalar yopiladi), `doctor.approved`, `doctor.suspended`, `doctor.reinstated`, `clinic.member_added`, `clinic.member_removed` |

Proyeksiya tartibsiz eventlarga chidamli: har qator oxirgi qo'llangan eventning `occurred_at` ini saqlaydi,
eski event yangisini ustidan yozmaydi; `suspended` `approved` dan oldin kelsa — retry (vaqtinchalik xato).

## Sinxron bog'liqliklar
Chaqiradi: — (faqat Eskiz SMS, timeout 3 s + circuit breaker). Chaqiruvchilar (`/internal/v1`): —.

## Konteynerlar
`auth-service` (API — imzo kaliti faqat shunga ulanadi), `-outbox-relay`, `-event-consumer`, `-scheduler`
(soatlik tozalash), `-migrate` (profil). Postgres/Redis — faqat ichki tarmoqda.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker: Postgres, Redis, RabbitMQ)
uv run ruff check . && uv run mypy app

../../scripts/gen-jwt-keys.sh             # secrets/private/jwt-private.pem bir marta
cp .env.example .env                      # parollar, OTP_HASH_SECRET, ESKIZ_*
docker compose up -d --build
docker compose --profile migrate run --rm auth-service-migrate
curl localhost:8101/health/ready
```

Lokal sinov (SMS logga): `SMS_PROVIDER=console OTP_DEBUG_ECHO=true` — kod `debug_code` maydonida qaytadi.
Prod'da ikkalasi ham taqiqlangan (servis ishga tushmaydi).

## Muhit o'zgaruvchilari
`.env.example` da hammasi izoh bilan. Asosiylari: `JWT_PRIVATE_KEY_PATH`, `OTP_HASH_SECRET` (majburiy),
`SMS_PROVIDER`, `ESKIZ_EMAIL`, `ESKIZ_PASSWORD`, `ESKIZ_FROM`, OTP chegaralari.

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `users` | har yangi raqam | o'chirilmaydi (o'chirishda telefon NULL) |
| `otp_codes` | har kod so'rovi | scheduler: 24 soatdan eski — har soat |
| `refresh_tokens` | har kirish va yangilash (rotatsiya) | scheduler: muddati o'tganlari — har soat |
| `doctor_links`, `clinic_memberships` | shifokor/a'zo soni | — |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
