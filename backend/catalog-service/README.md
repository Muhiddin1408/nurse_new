# catalog-service

> NN=06 · Python · ichki port 8080 · host 8106 · Postgres 5406 · Redis 6306

## Vazifasi
Klinikalar (hayot sikli), xonalar, mutaxassisliklar (C6 bemor qoidalari), shifokor profili + onboarding +
moderatsiya holat mashinasi, shifokor hujjatlari (file-service ID), klinika a'zolari (clinic_admin),
shifokor–klinika hamkorliklari, xizmatlar va ularning narxi (tiyin), denormalizatsiya qilingan reyting.

- **Shifokor (D2):** `draft → pending (submit) → approved | rejected`; `rejected → pending`; `approved ↔ suspended`.
  Submit talablari: ism, ≥1 mutaxassislik, litsenziya raqami va kelajakdagi muddati, `diploma` va `license` hujjatlari.
  Tasdiqlash/qayta faollashtirish — muddati o'tgan litsenziya bilan mumkin emas. Har o'tish qator qulfi ostida,
  takroriy o'tish idempotent, audit bilan.
- **Litsenziya (ADR-0011 #8):** `PUT /v1/doctor/license` to'xtatilgan shifokorga ham ochiq (monolitda deadlock edi);
  qayta faollashtirish — admin. Kunlik job: muddati o'tgan → `doctor.suspended (license_expired)`, 30 kun qolgan →
  bir marta `doctor.license_expiring`.
- **Klinika (ADR-0011):** `draft → active (publish) ↔ suspended`. Klinika admini faqat tavsif, telefon, ish vaqti, rasmni
  o'zgartiradi; nom/manzil/koordinata — admin.
- **Hamkorlik (D7):** invited → active ↔ paused → ended; declined. `paused` — slotlar yopiladi, bronlar saqlanadi;
  `ended` — booking kelajakdagi bronlarni bekor qiladi.
- **Xizmatlar (D5):** shifokorniki yoki klinikaniki (`doctor_id` NULL). Narx 50% dan ko'p oshsa — tasdiq (409);
  har o'zgarish narx tarixida va auditda. O'chirish — har doim `is_active=false`.
- **Bron qilinadigan xizmatlar (A3):** faol VA (shifokorniki YOKI shifokor faol hamkorlikdagi faol klinikaniki).
- **Ommaviy katalog (A9):** faqat `approved` shifokor va `active` klinika; reyting < 5 sharhda yashiriladi (C11).
- **Ruxsatlar:** shifokor `user_id` bo'yicha DB'dan topiladi va holati DB'dan tekshiriladi; klinika admini — faol
  a'zolik DB'dan; boshqarmaydigan klinika — 404. JWT'dagi rollar faqat ishora.

## Endpointlar (Kong uchun)
| Prefiks / yo'l | Auth | Tavsif |
|---|---|---|
| `GET /health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + Redis |
| `GET /v1/catalog/specializations` | — | |
| `GET /v1/catalog/clinics?city=&limit=&offset=`, `GET /v1/catalog/clinics/{id}` | — | faqat faol |
| `GET /v1/catalog/doctors?specialization=&clinic=&home_only=&lat=&lng=&radius=&q=&limit=&offset=` | — | geo: uy chaqiruvi (o'z radiusi) yoki klinika (≤ 50 km, `distance_km`) |
| `GET /v1/catalog/doctors/{id}`, `GET /v1/catalog/doctors/{id}/services?place=` | — | faqat tasdiqlangan (ADR-0011 #10) |
| `POST /v1/doctor/onboarding/start` | user JWT | idempotent (201/200) |
| `GET, PATCH /v1/doctor/onboarding/profile` | user JWT | faqat draft/rejected |
| `GET, POST /v1/doctor/onboarding/documents`, `DELETE .../{id}` | user JWT | `{kind, file_id}` — fayl file-service'da (`doctor_document`) |
| `POST /v1/doctor/onboarding/submit`, `GET /v1/doctor/onboarding/status` | user JWT | `missing[]`, `sla_due_at` |
| `GET, PATCH /v1/doctor/profile` | user JWT (approved) | to'lov rejimi, follow-up %, uy chaqiruvi bazasi/radiusi, rasm (`photo_file_id`) |
| `PUT /v1/doctor/license` | user JWT (approved/suspended) | |
| `GET, POST /v1/doctor/services`, `PATCH, DELETE /v1/doctor/services/{id}`, `GET .../{id}/price-history` | user JWT (approved) | |
| `GET /v1/doctor/clinic-invites`, `POST /v1/doctor/clinic-invites/{id}/accept|decline` | user JWT | |
| `GET /v1/doctor/affiliations`, `POST /v1/doctor/affiliations/{id}/leave` | user JWT | |
| `GET /v1/clinic/mine` | user JWT | boshqaradigan klinikalar |
| `GET, PATCH /v1/clinic/{clinic_id}/profile` | klinika admini | |
| `GET, POST /v1/clinic/{clinic_id}/rooms`, `PATCH .../rooms/{id}` | klinika admini | |
| `GET /v1/clinic/{clinic_id}/doctors`, `POST .../doctors/invite`, `POST .../doctors/{affiliation_id}/pause|resume|end` | klinika admini | |
| `GET, POST /v1/clinic/{clinic_id}/services`, `PATCH, DELETE .../services/{id}` | klinika admini | |
| `GET /v1/admin/moderation/doctors?status=`, `GET .../{id}`, `GET .../{id}/documents/{doc_id}/url`, `POST .../{id}/approve|reject|suspend|reinstate` | admin `doctors.moderate` | hujjat URL ≤ 5 daq (file-service auditi) |
| `GET, POST /v1/admin/clinics`, `GET, PATCH .../{id}`, `POST .../{id}/publish|suspend|reinstate`, `GET, POST .../{id}/members`, `DELETE .../{id}/members/{user_id}` | admin `clinics.manage` | |
| `GET, POST /v1/admin/specializations`, `PATCH .../{id}` | admin `clinics.manage` | |
| `GET /internal/v1/doctors/{id}/bookability`, `/internal/v1/services?ids=&bookable_for=`, `/internal/v1/access`, `/internal/v1/clinics/{id}`, `/internal/v1/rooms/{id}` | internal: schedule, pricing, booking, billing | `contracts/openapi/catalog-service.internal.yaml` |

Kong: `/v1/catalog/`, `/v1/doctor/onboarding/`, `/v1/doctor/profile`, `/v1/doctor/license`, `/v1/doctor/services`,
`/v1/doctor/clinic-invites`, `/v1/doctor/affiliations`, `/v1/clinic/mine`, `~/v1/clinic/[^/]+/(profile|rooms|doctors|services)`,
`/v1/admin/moderation/`, `/v1/admin/clinics`, `/v1/admin/specializations` → catalog-service.
(`/v1/doctor/schedule*`, `/v1/doctor/price-rules*`, `/v1/doctor/packages*`, `/v1/doctor/appointments*`,
`/v1/clinic/{id}/schedule|closures|rules|appointments*` — boshqa servislar.)

## Eventlar
| Publish | Consume |
|---|---|
| `doctor.onboarding_submitted`, `doctor.approved`, `doctor.rejected`, `doctor.suspended`, `doctor.reinstated`, `doctor.profile_updated`, `doctor.license_expiring`, `clinic.published` (publish va reinstate), `clinic.updated`, `clinic.suspended`, `clinic.member_added`, `clinic.member_removed`, `affiliation.invited`, `affiliation.accepted`, `affiliation.paused`, `affiliation.resumed`, `affiliation.ended`, `service.created`, `service.updated`, `service.deactivated`, `audit.recorded` | `rating.recalculated` (faqat yangiroq `occurred_at` qo'llanadi) |

Tizim aktyori (cron) uchun `*_by` = `00000000-0000-0000-0000-000000000000`.

## Sinxron bog'liqliklar
Chaqiradi: file-service (`attach`, `release`, `url`; timeout + retry + circuit breaker). Chaqiruvchilar
(`/internal/v1`): schedule, pricing, booking, billing.

## Konteynerlar
`catalog-service` (API), `-outbox-relay`, `-event-consumer`, `-scheduler` (litsenziya), `-migrate` (profil).

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker)
uv run ruff check . && uv run mypy app
cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm catalog-service-migrate
```

## Monolitdan farqlar
- Pul — tiyin (`price_tiyin`); fiskal kodlar API orqali yozilmaydi (ko'chirish/admin).
- `PriceRule` → schedule, paketlar → pricing (ADR-0011). Shifokor Telegram chati — notification.
- Klinika hayot sikli, a'zolar va mutaxassisliklar uchun admin API qo'shildi (monolitda faqat seed/Django admin).
- Taklif telefon emas, `doctor_id` bo'yicha (catalog telefon saqlamaydi; UI shifokorni katalog qidiruvidan tanlaydi).
- Xizmatni o'chirish har doim yumshoq (bronlar haqida ma'lumot booking-service'da).

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `doctors`, `clinics`, `services`, `affiliations` | o'n minglab | — |
| `service_price_history` | har narx o'zgarishi | — (audit) |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin |
