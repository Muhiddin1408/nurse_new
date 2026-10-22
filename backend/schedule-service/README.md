# schedule-service

> NN=07 · Go · ichki port 8080 · host 8107 · Postgres 5407 · Redis 6307

## Vazifasi
Shifokor jadvali va slotlar egasi: ish qoidalari, dam olish kunlari, ta'tillar, klinika yopiq kunlari, xonalar
(D8), vaqtga bog'liq narx foizlari (C12, ADR-0011 — catalog'dan ko'chdi), slot generatsiyasi va slot holati
(`free → held → booked`, `blocked`). Bron sagasida booking-service slotni `hold/confirm/extend/release` qiladi.

Asosiy qoidalar:
- **Hold — CAS.** `SELECT … FOR UPDATE` + holat tekshiruvi; muddati o'tgan hold o'qishda FREE hisoblanadi
  (lazy, E8), har daqiqalik job `UPDATE … WHERE status='held' AND hold_expires_at < now` bilan bo'shatadi
  (qayta band qilingan slotga tegmaydi, ADR-0011 #3) va `slot.released(hold_expired)` yuboradi.
- **Uy chaqiruvi buferi.** Hold'da `[start−bufer, end+bufer)` bilan kesishgan FREE qo'shnilar `travel_buffer`
  bilan yopiladi va `blocked_by_slot_id` yoziladi — bo'shaganda faqat o'z buferi ochiladi (ADR-0011 #4).
- **Generatsiya** — har kuni 02:00 (Toshkent) va qoida o'zgarganda; gorizont admin
  `schedule.generation_days_ahead`. Faqat tasdiqlangan shifokor; klinika qoidasi — faol hamkorlik + faol
  klinika (proyeksiya, ADR-0011 #6). Idempotent: `UNIQUE(doctor_id, start_at)`, mavjud slotlar bilan
  kesishgan nomzodlar va band xona tashlab ketiladi.
- **Faol bronlar daxlsiz.** Qoida/ta'til/dam olish kuni faol bronga tegsa — 409 `LIVE_BOOKINGS` +
  ro'yxat; faqat `resolution=keep_bookings`. Saqlangan bron keyin bekor bo'lsa, slot sotuvga qaytmaydi:
  qoida qoplamasa — `rule_changed`, boshqa sabab amalda bo'lsa — o'sha sabab bilan yopiladi.
- **Yopish sabablari bir-birini bosmaydi.** Biror sabab bekor bo'lganda (klinika qayta faol, ta'til
  o'chirildi…) slot faqat boshqa hech bir holat sababi (dam olish kuni, yopiq kun, ta'til, shifokor/klinika/
  hamkorlik faol emas) qolmagan bo'lsa ochiladi; aks holda amaldagi sabab bilan yopiq qoladi.
- **Narx foizi** slot o'qilganda hisoblanadi (Asia/Tashkent, eng kichik `priority`; tenglikda eng kichik foiz).

## Tuzilma
| Papka | Vazifasi |
|---|---|
| `cmd/schedule-service` | HTTP server |
| `cmd/schedule-relay` | Outbox relay (alohida konteyner) |
| `cmd/schedule-consumer` | Event consumer (alohida konteyner) |
| `cmd/schedule-scheduler` | Davriy vazifalar: generatsiya, hold expiry, tozalash (alohida konteyner) |
| `internal/domain` | Entity va sof biznes qoidalari |
| `internal/service` | Use-case'lar; event publish faqat shu yerdan (outbox, bitta tx) |
| `internal/store` | pgx repositorylar |
| `internal/cache` | Redis |
| `internal/clients` | Boshqa servis clientlari |
| `internal/eventbus` | Event handlerlar |
| `internal/httpapi` | Marshrut va handlerlar |
| `migrations` | golang-migrate SQL |

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | Standart |
| GET | `/v1/schedule/doctors/{id}/slots?date=` yoki `?date_from=&date_to=` | — | Bo'sh slotlar; kun — Redis 30 s (stampede lock), oraliq ≤ 31 kun / 200 slot; lead time |
| GET, POST | `/v1/doctor/schedule/rules` | doctor | Ish qoidalari (kesishuv — 409) |
| PATCH, DELETE | `/v1/doctor/schedule/rules/{id}[?resolution=keep_bookings]` | doctor | O'zgartirish/o'chirish |
| GET, POST / DELETE | `/v1/doctor/schedule/day-offs`, `/day-offs/{id}` | doctor | Dam olish kunlari |
| GET, POST / DELETE | `/v1/doctor/schedule/time-off`, `/time-off/{id}` | doctor | Ta'til (≤ 90 kun) |
| POST | `/v1/doctor/schedule/slots/{id}/block`, `/unblock` | doctor | Qo'lda yopish/ochish |
| GET | `/v1/doctor/schedule/calendar?from=&to=` | doctor | Taqvim (barcha holatlar, `booking_id`) |
| POST | `/v1/doctor/schedule/regenerate` | doctor | Qo'lda generatsiya |
| GET, POST / PATCH, DELETE | `/v1/doctor/price-rules`, `/price-rules/{id}` | doctor | Narx foizlari (C12) |
| GET | `/v1/clinic/{clinic_id}/schedule?date=` | clinic_admin | Klinika kuni (shifokorlar bo'yicha) |
| GET, POST / DELETE | `/v1/clinic/{clinic_id}/closures`, `/closures/{id}` | clinic_admin | Yopiq kunlar (bronlar ro'yxati qaytadi) |
| GET | `/v1/clinic/{clinic_id}/rules` | clinic_admin | Klinikadagi qoidalar |
| POST | `/v1/clinic/{clinic_id}/rules/{rule_id}/room` | clinic_admin | Xona biriktirish (D8) |
| GET | `/internal/v1/slots/{id}` | internal | Slot (`price_adjust_percent` bilan) |
| POST | `/internal/v1/slots/{id}/hold`, `/confirm`, `/extend`, `/release` | internal | Bron sagasi |
| GET | `/internal/v1/doctors/{id}/free-slots` | internal | Muqobil slotlar |

Auth: **doctor** — user JWT, `doctor_id` claim va proyeksiyada `approved` (aks holda 403
`ROLE_REQUIRED`/`DOCTOR_NOT_ACTIVE`). **clinic_admin** — catalog `GET /internal/v1/access` (`role=clinic_admin`,
Redis 60 s); begona klinika — 404. **internal** — `X-Internal-Token` + `X-Caller: booking-service`.
To'liq sxema: `docs/openapi.yaml`, internal — `contracts/openapi/schedule-service.internal.yaml`.

## Eventlar
| Publish | Consume |
|---|---|
| `schedule.availability_changed`, `slot.released` | `admin.settings_changed`, `affiliation.accepted`, `affiliation.ended`, `affiliation.paused`, `affiliation.resumed`, `booking.cancelled`, `booking.expired`, `booking.rescheduled`, `clinic.published`, `clinic.suspended`, `doctor.approved`, `doctor.reinstated`, `doctor.suspended` |

## Sinxron bog'liqliklar
Chaqiradi (timeout + retry + circuit breaker, go-common `httpclient`):
- `catalog-service` — `doctors/{id}/bookability` (qoida yaratishda: faol hamkorlik, uy chaqiruvi),
  `access` (klinika admini, 60 s kesh), `rooms/{id}`.
- `admin-service` — `GET /internal/v1/settings` (`schedule.*`; Redis 60 s, `admin.settings_changed` da
  tashlanadi). Admin ishlamasa — oxirgi ma'lum qiymat (`admin:settings:v1:last`), u ham bo'lmasa — admin
  standartlari bilan bir xil zaxira (hold 10 daq, lead 60 daq, gorizont 30 kun).

Chaqiruvchilar (`/internal/v1`): `booking-service`.

## Ishga tushirish
```bash
make test                # unit
make test-integration    # Docker kerak
make lint
cp .env.example .env && make up && make migrate
curl localhost:8107/health/ready
```

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | consumer: 30 kundan keyin (soatiga bir) |
| `slots` | shifokor × gorizont (≈ 30 kun × kuniga 10–30) | scheduler: 90 kundan eski, hech qachon band qilinmaganlar (`SLOT_RETENTION_DAYS`); bron tarixi borlari qoladi |
| `working_rules`, `price_rules`, `time_offs`, `schedule_exceptions`, `clinic_closures` | qo'lda kiritiladi (kichik) | — |
| Redis `slots:v1:*`, `catalog:access:*`, `admin:settings:v1` | kesh | TTL 30 s / 60 s / 60 s |

## Davriy vazifalar (`schedule-scheduler`)
| Job | Qachon | Izoh |
|---|---|---|
| `generate_slots` | ishga tushganda + har kuni `GENERATE_HOUR` (Toshkent) | advisory lock — bir nechta nusxada ham bitta |
| `expire_holds` | ishga tushganda + har `EXPIRE_INTERVAL` | CAS, `SKIP LOCKED`, partiya `EXPIRE_BATCH_SIZE` |
| `purge_slots` | har 24 soat | `SLOT_RETENTION_DAYS` |

Metrikalar (`:9100/metrics`): `periodic_job_runs_total{job,result}`, `periodic_job_last_success_timestamp{job}`,
`slot_holds_expired_total`. Tavsiya etilgan alert (infra): `generate_slots` 26 soatdan beri muvaffaqiyatsiz.

## Testlar
- `make test` — domen (generatsiya, narx, vaqt zonasi), marshrut/auth chegaralari, event katalogi qamrovi.
- `make test-integration` — Postgres 16 + Redis 7 (testcontainers), catalog/admin — httptest soxtalari:
  hold/confirm/extend/release, 20 parallel hold (aniq bittasi yutadi), bufer egaligi, expiry job (CAS) va
  lazy expiry, qoida/ta'til/dam olish/yopiq kun ziddiyatlari, xona, narx, barcha 13 event handler,
  generatsiya va tozalash. Qamrov (`-coverpkg=./internal/...`): ~82%.
