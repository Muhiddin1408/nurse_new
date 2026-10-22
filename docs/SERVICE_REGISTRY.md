# MedBron — Servislar reestri

> **Yagona haqiqat manbai** (BLUEPRINT 11-bo'lim). Port, baza, bog'liqlik shu yerdan olinadi.
> `.env.example`, `docker-compose.yml`, `kong.*.yml` shu jadvalga zid bo'lsa — CI yiqiladi
> (`scripts/check-registry.py`, 1-bosqich). Eventlar ro'yxati `contracts/events/catalog.yaml` dan
> generatsiya qilingan va u bilan sinxron tutiladi.

## 1. Raqamlash sxemasi

- Har servisga bitta raqam `NN`: app host porti `81NN`, Postgres `54NN`, Redis `63NN`.
- **Ichki port hamma servisda `8080`** (Go gRPC — `9090`). Servislar bir-biriga
  `http://<servis-nomi>:8080` bilan murojaat qiladi; host portlar faqat lokal debug uchun.
- Postgres bazasi va useri = servis nomi pastki chiziq bilan: `booking_service`.
- Yangi servis — keyingi bo'sh raqam. Raqam qayta ishlatilmaydi (o'chirilgan servis raqami "band" qoladi).

## 2. Asosiy jadval

| NN | Servis | Til | Ichki port | Host port | Postgres host port | Redis host port | Publishes | Consumes | Calls (sync) |
|---|---|---|---|---|---|---|---|---|---|
| 01 | auth-service | Py | 8080 | 8101 | 5401 | 6301 | `user.registered`, `user.roles_changed`, `user.deactivated`, `user.reactivated` | `user.deletion_requested`, `doctor.approved`, `doctor.suspended`, `doctor.reinstated`, `clinic.member_added`, `clinic.member_removed` | — |
| 02 | user-service | Py | 8080 | 8102 | 5402 | 6302 | `user.profile_updated`, `user.deletion_requested` | `user.registered`, `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.expired`, `booking.completed`, `booking.no_show` | file |
| 03 | admin-service | Py | 8080 | 8103 | 5403 | 6303 | `admin.settings_changed` | `audit.recorded` | — |
| 04 | file-service | Py | 8080 | 8104 | 5404 | — | — | — | — |
| 05 | notification-service | Py | 8080 | 8105 | 5405 | 6305 | — | 36 ta (katalogga qarang: `user.*`, `doctor.*`, `booking.*`, `payment.reversed`, `payment.discrepancy_found`, `refund.*`, `payout.*`, `review.*`, `dispute.*`, `affiliation.invited`, `affiliation.ended`, `waitlist.*`, `clinic.member_added`) | — |
| 06 | catalog-service | Py | 8080 | 8106 | 5406 | 6306 | `doctor.onboarding_submitted`, `doctor.approved`, `doctor.rejected`, `doctor.suspended`, `doctor.reinstated`, `doctor.profile_updated`, `doctor.license_expiring`, `clinic.published`, `clinic.updated`, `clinic.suspended`, `clinic.member_added`, `clinic.member_removed`, `affiliation.invited`, `affiliation.accepted`, `affiliation.paused`, `affiliation.resumed`, `affiliation.ended`, `service.created`, `service.updated`, `service.deactivated` | `rating.recalculated` | file |
| 07 | schedule-service | **Go** | 8080 | 8107 | 5407 | 6307 | `slot.released`, `schedule.availability_changed` | `admin.settings_changed`, `doctor.approved`, `doctor.suspended`, `doctor.reinstated`, `clinic.published`, `clinic.suspended`, `affiliation.accepted`, `affiliation.paused`, `affiliation.resumed`, `affiliation.ended`, `booking.cancelled`, `booking.expired`, `booking.rescheduled` | catalog, admin |
| 08 | pricing-service | Py | 8080 | 8108 | 5408 | 6308 | — | `user.deletion_requested`, `admin.settings_changed`, `service.created`, `service.updated`, `service.deactivated`, `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.expired`, `booking.completed`, `booking.no_show` | catalog, admin |
| 09 | booking-service | Py | 8080 | 8109 | 5409 | 6309 | `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.expired`, `booking.rescheduled`, `booking.started`, `booking.completed`, `booking.no_show`, `booking.confirmation_failed`, `waitlist.slot_offered` | `user.deletion_requested`, `admin.settings_changed`, `doctor.suspended`, `clinic.suspended`, `affiliation.ended`, `slot.released`, `payment.captured`, `payment.reversed`, `refund.succeeded` | catalog, schedule, pricing, user, admin |
| 10 | payment-service | Py | 8080 | 8110 | 5410 | 6310 | `payment.created`, `payment.captured`, `payment.failed`, `payment.reversed`, `refund.succeeded`, `refund.failed`, `payment.discrepancy_found` | `admin.settings_changed`, `booking.cancelled`, `booking.no_show`, `booking.confirmation_failed`, `payout.paid`, `dispute.resolved` | booking, admin |
| 11 | billing-service | Py | 8080 | 8111 | 5411 | 6311 | `payout.calculated`, `payout.paid` | `admin.settings_changed`, `booking.cancelled`, `booking.completed`, `booking.no_show`, `payment.reversed`, `refund.succeeded` | catalog, admin |
| 12 | review-service | Py | 8080 | 8112 | 5412 | 6312 | `review.submitted`, `review.published`, `review.rejected`, `rating.recalculated` | `admin.settings_changed`, `booking.completed` | booking, admin |
| 13 | dispute-service | Py | 8080 | 8113 | 5413 | 6313 | `dispute.opened`, `dispute.resolved`, `dispute.sla_breached` | `admin.settings_changed`, `refund.succeeded` | booking, file, admin |
| 14 | search-service | Py | 8080 | 8114 | 5414 | 6314 | — | `doctor.approved`, `doctor.suspended`, `doctor.reinstated`, `doctor.profile_updated`, `clinic.published`, `clinic.updated`, `clinic.suspended`, `affiliation.accepted`, `affiliation.paused`, `affiliation.resumed`, `affiliation.ended`, `service.created`, `service.updated`, `service.deactivated`, `schedule.availability_changed`, `rating.recalculated` | catalog |
| 15 | analytics-service | Py | 8080 | 8115 | 5415 | — | — | `#` — `audit.recorded` dan boshqa barcha 55 ta event (navbat `#` ga bind, audit filtrlanadi) | — |
| 16 | ws-gateway | **Go** | 8080 | 8116 | — | 6316 | — | `user.deactivated`, `schedule.availability_changed`, `booking.*` (9 ta), `waitlist.slot_offered`, `payment.captured`, `payment.failed` | booking |

> `audit.recorded` ni imtiyozli amal bajaradigan **barcha** servislar publish qiladi (katalogda `publishers:` ro'yxati), yagona consumer — admin-service. Jadvalda takrorlanmasligi uchun "Publishes" ustunida ko'rsatilmagan.

## 3. Infratuzilma

| Komponent | Konteyner nomi | Ichki port | Host port | Izoh |
|---|---|---|---|---|
| Kong (proxy) | `medbron-kong` | 8000 / 8443 | 8000 / 8443 | Yagona tashqi kirish nuqtasi |
| Kong (admin API) | `medbron-kong` | 8001 | 8100 (faqat `127.0.0.1`) | Prod'da yopiq. NN=00 gateway uchun band (8001 hostda boshqa dastur bilan to'qnashadi) |
| Monolit (o'tish davri) | host jarayon | — | 8099 | `manage.py runserver 0.0.0.0:8099`; Kong `host.docker.internal:8099` ga yo'naltiradi. 8000 — Kong'ning o'zi (8000 ga yo'naltirish = proksi sikli) |
| RabbitMQ | `medbron-rabbitmq` | 5672 / 15672 | 5672 / 15672 | Bitta broker, exchange `medbron.events` |
| Jaeger | `medbron-jaeger` | 4317 (OTLP gRPC) / 16686 (UI) | 4317 / 16686 | |
| Prometheus | `medbron-prometheus` | 9090 | 9090 | |
| Grafana | `medbron-grafana` | 3000 | 3000 | |
| S3 obyekt ombori (SeaweedFS, ADR-0010) | `medbron-s3` | 8333 | 9000 | Bucketlar: `public-media` (anonim o'qish), `private-documents` (faqat presigned). Servislar faqat S3 API bilan |
| Elasticsearch 8 | `search-service-elasticsearch` | 9200 | 9200 | search-service compose'ida (ichki tarmoq; database-per-service) |
| ClickHouse | `analytics-service-clickhouse` | 8123 / 9000 | 8123 / 9009 | analytics-service compose'ida (ichki tarmoq; host 9000 S3'da band) |
| Docker network | `medbron-net` | — | — | `external: true`, `backend/infra` yaratadi |

## 4. Servislar bo'yicha tafsilot

Har servisda (BLUEPRINT 5.3): `GET /health/live`, `GET /health/ready`, `GET /metrics`,
`X-Request-ID` middleware, xato formati `{"detail": {"code": "...", "message": "..."}}`.

Routerlar: `/v1/<x>` — user JWT, `/v1/admin/<x>` — admin JWT + permission, `/internal/v1/<x>` — `X-Internal-Token`.

### 01 auth-service
- **Egalik:** telefon raqami (identifikator), OTP kodlar (HMAC), refresh sessiyalar (opaque, rotatsiya, oila), rollar proyeksiyasi (`doctor_id` claim).
- **Tashqi API:** `POST /v1/auth/otp/request` → 200 `{expires_in, retry_after}`, `POST /v1/auth/otp/verify` → `{access, refresh, is_new_user, user, tokens}`, `POST /v1/auth/token/refresh`, `POST /v1/auth/switch-role`, `POST /v1/auth/logout`, `POST /v1/auth/logout/all`, `GET /v1/auth/me`, `GET /v1/auth/.well-known/jwks.json`.
- **Admin API:** `GET /v1/admin/users`, `POST /v1/admin/users/{id}/block|unblock` (`users.manage`).
- **Internal API:** yo'q (leaf). OTP SMS'ni auth o'zi yuboradi (ADR-0004), adapter `libs/py-common` (`medbron_common.sms`).
- **Redis:** OTP rate-limit (60 s cooldown, telefon 5/soat, IP 20/soat), noto'g'ri kodlar bloki (10/soat), Eskiz tokeni. Redis ishlamasa — fail-closed (503).
- **Cron (scheduler):** 24 soatdan eski OTP va muddati o'tgan refresh tokenlarni tozalash (soatlik).

### 02 user-service
- **Egalik:** profil (ism, til, avatar), bemorlar (oila a'zolari), manzillar, sevimli shifokorlar, akkaunt o'chirish so'rovi, bronlar proyeksiyasi (`booking_states`).
- **Tashqi API:** `GET|PATCH|DELETE /v1/users/me`, `/v1/users/me/patients[/{id}]`, `/v1/users/me/addresses[/{id}]`, `GET /v1/users/me/favorites`, `PUT|DELETE /v1/users/me/favorites/{doctor_id}`.
- **Admin API:** `GET /v1/admin/users/{id}/profile` (`users.manage`, faqat o'qish).
- **Internal API (booking chaqiradi):** `GET /internal/v1/users/{user_id}/patients/{patient_id}`, `GET /internal/v1/users/{user_id}/addresses/{address_id}` — egalik tekshiruvi bilan, 404 boshqasiniki bo'lsa.
- **Calls:** file (`attach`/`release` avatar).
- **Ruxsat etilgan chaqiruvchilar:** booking-service.

### 03 admin-service
- **Egalik:** admin akkauntlar (login + argon2id parol + TOTP, sir Fernet bilan shifrlangan), rollar va permission katalogi, audit jurnali, global sozlamalar (tiplangan sxema, versiya).
- **Tashqi API:** `POST /v1/admin/auth/login` (parol → `mfa_token`), `POST /v1/admin/auth/totp` (→ admin JWT `aud=admin`), `GET /v1/admin/auth/me`, `POST /v1/admin/auth/password`, `/v1/admin/admins[/{id}[/reset-totp]]`, `/v1/admin/roles[/{id}]`, `GET /v1/admin/permissions` (`admins.manage`), `GET /v1/admin/audit` (`audit.read`), `GET|PUT /v1/admin/settings` (`settings.manage`, `expected_version`).
- **Internal API:** `GET /internal/v1/settings` (`ETag "v<N>"`, `If-None-Match` → 304; chaqiruvchilar 60s keshlaydi, `admin.settings_changed` da tashlaydi).
- **Navbat:** `admin-service.audit` (faqat `audit.recorded`).
- **Ruxsat etilgan chaqiruvchilar:** schedule, pricing, booking, payment, billing, review, dispute.

### 04 file-service
- **Egalik:** fayl metadata, S3 obyektlari (ADR-0010), presigned URL, thumbnail, haqiqiy tur tekshiruvi (magic bytes), EXIF/GPS tozalash, hajm limiti.
- **Tashqi API:** `POST /v1/files/uploads` (presigned PUT), `POST /v1/files/{id}/complete`, `GET /v1/files/{id}`, `GET /v1/files/{id}/url` (egasi), `DELETE /v1/files/{id}`.
- **Admin API:** `GET /v1/admin/files/{id}[/url]` (`files.read` — maxfiy hujjat, auditga).
- **Internal API:** `POST /internal/v1/files/{id}/attach` (`owner_user_id`, `owner_ref`, `purpose`; egasi servis = `X-Caller`), `POST /internal/v1/files/{id}/release`, `GET /internal/v1/files/{id}/url?ttl=&viewer_type=&viewer_id=` (≤ 5 daq, auditga).
- **Ruxsat etilgan chaqiruvchilar:** user, catalog, dispute.
- **Cron (scheduler):** 24 soatda `complete`/`attach` qilinmagan fayllarni o'chirish (soatlik).

### 05 notification-service
- **Egalik:** kanallar (SMS — Eskiz, Telegram bot, push — faqat FCM), push tokenlar, bildirishnoma sozlamalari, shablonlar (uz/ru), xabarlar outbox'i (darhol va rejalashtirilgan), kontakt proyeksiyasi (`user_id → phone, language, telegram_chat_id`), `doctor_id → user_id`, bronlar proyeksiyasi (eslatma himoyasi, digest, bot `/today`).
- **Tashqi API:** `GET|PATCH /v1/notifications/preferences`, `POST|DELETE /v1/notifications/devices`, `POST /v1/telegram/webhook` (Telegram secret header bilan), `POST /v1/telegram/link-token`.
- **Admin API:** `/v1/admin/notifications/templates[/{key}/{lang}]`, `/v1/admin/notifications/logs` (`notifications.manage`).
- **Calls:** yo'q — hamma kerakli ma'lumot event + lokal proyeksiyadan (pastki oqim servisi, hech kimni bloklamaydi).
- **Cron (dispatcher):** xabarlar navbati (har 2 s), digest (har kuni 07:00 Asia/Tashkent), jurnal tozalash (90 kun).

### 06 catalog-service
- **Egalik:** klinikalar, xonalar, mutaxassisliklar, shifokor profili + onboarding + moderatsiya holat mashinasi, shifokor hujjatlari (fayl ID'lari), klinika a'zolari (clinic_admin), shifokor–klinika aloqalari (affiliation), xizmatlar va ularning narxi, denormalizatsiya qilingan reyting.
- **Tashqi API:** `GET /v1/catalog/specializations|clinics[/{id}]|doctors[/{id}[/services]]` (tokensiz; faqat tasdiqlangan shifokor / faol klinika), `/v1/doctor/onboarding/*` (start, profile, documents, submit, status), `/v1/doctor/profile`, `/v1/doctor/license`, `/v1/doctor/services[/{id}[/price-history]]`, `/v1/doctor/clinic-invites[/{id}/accept|decline]`, `/v1/doctor/affiliations[/{id}/leave]`, `/v1/clinic/mine`, `/v1/clinic/{id}/profile|rooms|doctors|services` (taklif — `doctors/invite`, `doctors/{aff}/pause|resume|end`).
- **Admin API:** `/v1/admin/moderation/doctors` (`doctors.moderate`; hujjat havolasi ≤ 5 daq), `/v1/admin/clinics[/{id}[/publish|suspend|reinstate|members]]` (`clinics.manage`), `/v1/admin/specializations` (`clinics.manage`).
- **Internal API:** `GET /internal/v1/doctors/{id}/bookability` (status, uy chaqiruvi radiusi, baza koordinatalari, `clinic_payment_mode`, `follow_up_discount_percent`), `GET /internal/v1/services?ids=` (narx, joy, davomiylik, egasi, MXIK), `GET /internal/v1/access?user_id=&doctor_id=|clinic_id=` (resurs authz: shu user shu shifokor/klinikani boshqaradimi), `GET /internal/v1/clinics/{id}`, `GET /internal/v1/rooms/{id}`, `GET /internal/v1/export/doctors|clinics?after=&limit=` (search reindex — keyset sahifalash, faqat ommaviy ko'rinadigan maydonlar).
- **Tizim aktyori:** cron o'zgarishlarida `*_by` = `00000000-0000-0000-0000-000000000000`.
- **Calls:** file.
- **Ruxsat etilgan chaqiruvchilar:** schedule, pricing, booking, billing, search.
- **Cron:** litsenziya muddati eslatmasi (kunlik).

### 07 schedule-service (Go)
- **Egalik:** ish qoidalari (WorkingRule), istisno kunlar, klinika yopilish kunlari, ta'tillar (TimeOff), slotlar va ularning holati (`free → held → booked`, `blocked`), uy chaqiruvi yo'l buferi, xona bandligi, dinamik narx foizi (`price_adjust_percent` slot darajasida — pricing o'qiydi), lokal proyeksiya: bron qila oladigan shifokorlar + faol affiliation'lar.
- **Tashqi API (eng yuklangan):** `GET /v1/schedule/doctors/{id}/slots?date=|date_from=&date_to=` (tokensiz; Redis cache-aside 30s, stampede lock), `/v1/doctor/schedule/rules|day-offs|time-off[/{id}]`, `/v1/doctor/schedule/slots/{id}/block|unblock`, `/v1/doctor/schedule/calendar`, `/v1/doctor/schedule/regenerate`, `/v1/doctor/price-rules[/{id}]` (C12), `/v1/clinic/{id}/schedule|closures[/{id}]|rules[/{rule_id}/room]`. Faol bronga tegadigan o'zgarish → 409 `LIVE_BOOKINGS` (faqat `keep_bookings`; bronlarni shifokor booking orqali bekor qiladi).
- **Internal API:** `POST /internal/v1/slots/{id}/hold` (`hold_ref`, `ttl_seconds`; compare-and-set, `Idempotency-Key`), `POST /internal/v1/slots/{id}/confirm` (`hold_ref` mos bo'lishi shart), `POST /internal/v1/slots/{id}/release` (idempotent, faqat booking qo'lda chaqira oladi — asosiy yo'l event), `POST /internal/v1/slots/{id}/extend`, `GET /internal/v1/slots/{id}`, `GET /internal/v1/doctors/{id}/free-slots`. Muddati o'tgan hold o'qishda bo'sh hisoblanadi (lazy expiry); uy chaqiruvi buferi qo'shni slotlarni `blocked_by_slot_id` bilan yopadi.
- **Calls:** catalog (authz, xona), admin (hold TTL, min lead time, gorizont; ETag'siz 60 s kesh — go-common httpclient ixtiyoriy header yubormaydi).
- **Ruxsat etilgan chaqiruvchilar:** booking.
- **Cron (`schedule-scheduler` konteyneri):** slot generatsiyasi (kunlik 02:00 Toshkent, ishga tushganda va qoida o'zgarganda; gorizont — admin sozlamasi; idempotent), muddati o'tgan hold'larni bo'shatish (har daqiqa, `SKIP LOCKED` + CAS), 90 kundan eski bronsiz slotlarni o'chirish. Alert: `generate_slots` 26 soatdan beri muvaffaqiyatsiz.

### 08 pricing-service
- **Egalik:** narx hisoblash (quote), promo kodlar va redemption'lar, paketlar va yozilishlar, birinchi bron / follow-up chegirmalari, to'lov rejimi qarori (prepaid / deposit / at_clinic), komissiya va provayder komissiyasi taqsimoti, mijoz statistikasi proyeksiyasi (yakunlangan bronlar, no-show soni, shifokor bo'yicha oxirgi tashrif).
- **Tashqi API:** `POST /v1/pricing/quote` (UI'da narxni oldindan ko'rsatish), `POST /v1/pricing/promo/validate`, `GET /v1/pricing/packages?doctor_id=`, `GET /v1/pricing/packages/mine`, `POST /v1/pricing/packages/{id}/enroll`, `GET, POST /v1/doctor/packages`, `PATCH /v1/doctor/packages/{id}`.
- **Admin API:** `GET, POST /v1/admin/promo-codes`, `GET, PATCH /v1/admin/promo-codes/{id}` (`pricing.manage`).
- **Internal API:** `POST /internal/v1/quotes` — kirish: `client_id, doctor_id, service_ids, slot_start_at, price_adjust_percent, place, promo_code`; chiqish: `items[], money_split, payment_mode, prepay_tiyin, quote_id`. Quote 10 daqiqa Redis'da (booking `quote_id` bilan qayta hisoblamaydi). `POST /internal/v1/quotes/{id}/commit` (`booking_id`, `client_id`) — promo/paket band qilinadi; 15 daqiqada `booking.created` kelmasa o'zi bo'shaydi.
- **Calls:** catalog (xizmat narxlari, shifokor sozlamalari), admin (komissiya, deposit foizi).
- **Ruxsat etilgan chaqiruvchilar:** booking.

### 09 booking-service
- **Egalik:** bron va uning holat mashinasi (orkestratsiya), bron raqami, idempotency kalitlari, bekor qilish siyosatini qo'llash, ko'chirish (reschedule), waitlist, "yana bron qilish", faol bronlar uchun shifokor/klinika ko'rinishlari.
- **Tashqi API:** `POST /v1/bookings` (`Idempotency-Key`), `GET /v1/bookings/mine`, `GET /v1/bookings/{id}`, `GET /v1/bookings/{id}/cancellation-preview`, `POST /v1/bookings/{id}/cancel`, `POST /v1/bookings/{id}/reschedule`, `GET /v1/bookings/{id}/rebook`, `/v1/waitlist`, `/v1/doctor/appointments` (start / complete / no-show / izoh), `/v1/clinic/{id}/appointments`.
- **Admin API:** `GET /v1/admin/bookings[/{id}]` (`bookings.read`), `POST /v1/admin/bookings/{id}/complete|no-show|cancel` (`bookings.manage`).
- **Internal API:** `GET /internal/v1/bookings/{id}/payable` (payment uchun: holat, `prepay_tiyin`, `client_id`, `payment_due_at`, fiskal elementlar), `POST /internal/v1/bookings/{id}/extend-payment` (payment: +15 daq), `GET /internal/v1/bookings/{id}/review-eligibility?client_id=`, `GET /internal/v1/bookings/{id}/summary?user_id=` (dispute uchun), `GET /internal/v1/bookings/{id}/authz?user_id=` (ws-gateway uchun).
- **Calls (5/6):** catalog, schedule, pricing, user, admin.
- **Ruxsat etilgan chaqiruvchilar:** payment, review, dispute, ws-gateway.
- **Cron:** to'lanmagan bronlarni expire qilish (har daqiqa), 24 soatdan keyin avto-yakunlash (soatlik), waitlist va idempotency kalitlarini tozalash (kunlik).

### 10 payment-service
- **Egalik:** to'lov urinishlari (Payme Merchant API, Click SHOP API; `cash` — enum, onlayn emas), webhooklar, refundlar (retry, `manual_required` navbati), ikki yozuvli daftar (ledger), fiskal chek elementlari (provayder OFD'si), kunlik reconciliation, provayder jurnali (ADR-0012).
- **Tashqi API:** `POST /v1/payments` (`booking_id`, `provider` → `checkout_url`), `GET /v1/payments?booking_id=`, `GET /v1/payments/{id}`, `POST /v1/payments/payme/webhook` (Basic Auth, IP allowlist), `POST /v1/payments/click/prepare|complete` (MD5 imzo, IP allowlist).
- **Admin API:** `/v1/admin/payments`, `/v1/admin/refunds` (qo'lda refund `Idempotency-Key` bilan, `complete`, `retry` — `payments.refund`), `/v1/admin/ledger` (+ `/balances`), `/v1/admin/reconciliation` (+ `registry` CSV import, `resolve`) — o'qish `payments.read`.
- **Internal API:** yo'q (payment'ni hech kim sinxron chaqirmaydi).
- **Calls:** booking (`/payable` — money snapshot va fiskal elementlar bilan, `/extend-payment`), admin (sozlamalar: fiskal TIN).
- **Cron:** refundlar (har daqiqa), Click fiskal cheklari (5 daq), eskirgan urinishlar → `expired` (10 daq), ichki reconciliation (03:00), provayder jurnali retention (04:30).

### 11 billing-service
- **Egalik:** shifokor daromad daftari (`earning_entries`: earning / refund / reversal — ADR-0012), haftalik payout davrlari, to'lov rekvizitlari (javobda faqat niqoblangan raqam), daromad paneli, CSV ko'chirma.
- **Tashqi API:** `GET /v1/doctor/earnings?period=|from&to`, `GET|PUT /v1/doctor/payout-account`, `GET /v1/doctor/payouts`, `GET /v1/doctor/payouts/{id}/statement`, `GET /v1/clinic/{id}/earnings` (clinic_admin, catalog `/access`).
- **Admin API:** `/v1/admin/payouts` (ro'yxat, `build`, `{id}/mark-paid`, `{id}/statement` — `payouts.manage`).
- **Internal API:** yo'q.
- **Calls:** catalog (klinika a'zoligi authz), admin (payout min summasi va kuni).
- **Cron:** haftalik payout hisoblash (admin `payout.weekday`, 04:00 Toshkent) — o'tgan to'liq hafta.

### 12 review-service
- **Egalik:** sharhlar, avtomatik moderatsiya bayroqlari (telefon, havola, so'kinish), shifokor javobi, reyting hisoblash (minimal 5 ta sharhdan).
- **Tashqi API:** `POST /v1/reviews` (yakunlangan bron, 30 kun ichida), `GET /v1/reviews?doctor_id=`, `POST /v1/doctor/reviews/{id}/reply`.
- **Admin API:** `/v1/admin/reviews` (`reviews.moderate`).
- **Calls:** booking (`/review-eligibility` — `booking.completed` proyeksiyasi bo'lmasa fallback), admin (sozlamalar: `sla.review_window_days`).
- **Lokal proyeksiya:** `booking.completed` dan "sharh qoldirish mumkin bo'lgan bronlar".

### 13 dispute-service
- **Egalik:** nizolar, dalillar (fayl ID), SLA, yechim.
- **Tashqi API:** `POST /v1/disputes`, `GET /v1/disputes/mine`, `POST /v1/disputes/{id}/evidence`.
- **Admin API:** `/v1/admin/disputes` (`disputes.resolve`).
- **Calls:** booking (`/summary`), file (dalil biriktirish), admin (sozlamalar: `sla.dispute_hours`).
- **Cron:** SLA buzilishini tekshirish (har 15 daqiqa).

### 14 search-service
- **Egalik:** CQRS o'qish modeli — Elasticsearch indekslari (`doctors_v1`, `clinics_v1`), alias orqali qayta indekslash.
- **Tashqi API:** `GET /v1/search/doctors?q=&specialization=&city=&place=&lat=&lng=&radius_km=&available_on=&sort=`, `GET /v1/search/clinics`, `GET /v1/search/suggest`.
- **Admin API:** `POST /v1/admin/search/reindex` (`search.manage`) — yangi versiyali indeks (`doctors_v<N>`) catalog `GET /internal/v1/export/*` dan quriladi, keyin alias almashtiriladi (2026-10-21 qarori). Oddiy rejimda — faqat eventlar.
- **Calls:** catalog (faqat reindex paytida).
- **Dvigatel:** Elasticsearch 8 (monolit bilan bir xil, 2026-10-21 qarori) — servisning o'z compose'ida, ichki tarmoqda.
- **Postgres:** faqat `processed_events`, eventlar tartibi (`source_version`) va reindex holati.

### 15 analytics-service
- **Egalik:** ClickHouse'dagi barcha eventlar (`events_raw`), agregatlar (kunlik bronlar, konversiya, cancellation rate, GMV), hisobotlar.
- **Admin API:** `/v1/admin/analytics/*` (`analytics.read`).
- **Navbat:** `analytics-service.all-events` ← `#` binding.

### 16 ws-gateway (Go)
- **Egalik:** WebSocket ulanishlari. Ma'lumot saqlamaydi.
- **Tashqi:** `GET /ws?token=<JWT>`; obuna: `user:<id>` (avtomatik), `booking:<id>`, `doctor:<id>:schedule`.
- **Calls:** booking (`/authz`, natija Redis'da 60s).
- **Redis:** pub/sub `rtws:<channel>` (gorizontal masshtab).

## 5. Navbatlar (har consumer servisda bitta)

| Navbat | Binding'lar |
|---|---|
| `auth-service.events` | `user.deletion_requested`, `doctor.approved`, `doctor.suspended`, `doctor.reinstated`, `clinic.member_added`, `clinic.member_removed` |
| `user-service.events` | `user.registered`, `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.expired`, `booking.completed`, `booking.no_show` |
| `admin-service.audit` | `audit.recorded` |
| `notification-service.events` | katalogdagi 36 ta key |
| `catalog-service.events` | `rating.recalculated` |
| `schedule-service.events` | 13 ta key (2-jadval) |
| `pricing-service.events` | 11 ta key |
| `booking-service.events` | 9 ta key |
| `payment-service.events` | 6 ta key |
| `billing-service.events` | 6 ta key |
| `review-service.events` | `admin.settings_changed`, `booking.completed` |
| `dispute-service.events` | `admin.settings_changed`, `refund.succeeded` |
| `search-service.events` | 16 ta key |
| `analytics-service.all-events` | `#` |
| `ws-gateway.events` | 14 ta key |

Barcha navbatlar: `durable=true`, `x-queue-type=quorum`, `x-dead-letter-exchange=medbron.dlx`.
Binding'lar koddan (`libs` dagi `Events` konstantalari) e'lon qilinadi va `scripts/check-contracts.py`
ularni katalogdagi `consumers` bilan solishtiradi.

## 6. Sinxron bog'liqlik grafi (DAG)

```
booking  → catalog, schedule, pricing, user, admin      (5)
payment  → booking, admin                               (2)
review   → booking, admin                               (2)
dispute  → booking, file, admin                         (3)
ws-gw    → booking                                      (1)
schedule → catalog, admin                               (2)
pricing  → catalog, admin                               (2)
billing  → catalog, admin                               (2)
search   → catalog (faqat reindex)                      (1)
catalog  → file                                         (1)
user     → file                                         (1)
auth, admin, file, notification, analytics → —         (leaf)
```

Topologik tartib (pastdan yuqoriga): `file, admin, auth, notification, analytics` →
`catalog, user` → `schedule, pricing, billing, search` → `booking` → `payment, review, dispute, ws-gateway`.
Sikl yo'q, maksimal chiqish darajasi 5 (limit 6). CI: `scripts/check-contracts.py --graph`.
