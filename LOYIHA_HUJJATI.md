# MedBron — Loyiha to'liq hujjati

> Har bir papka, har bir fayl va har bir funksiya: **nima qiladi**, **nima uchun yozilgan**, **qanday ishlaydi**.

---

## 1. Loyiha nima

Tibbiy xizmatlarni bron qilish platformasi (Django + DRF). Mijoz shifokorni topadi, bo'sh vaqtni tanlaydi, bron qiladi, to'laydi va SMS oladi. Xizmat klinikada ham, uy chaqiruvi shaklida ham bo'lishi mumkin.

Kod **fazalar** bo'yicha yozilgan — izohlarda "Faza 1", "Faza 4", "Faza 7" deb tez-tez eslatiladi. Bu monolitdan mikroservislarga o'tish yo'l xaritasi:

| Faza | Mazmun | Kodda qaysi qism |
|---|---|---|
| 1 | Monolit, ACID tranzaksiya, slot bandligi | `apps/*`, `api/booking`, `api/schedule`, `api/catalog`, `api/account` |
| 2 | gRPC, servis discovery | `api/notifications/klient.py` |
| 3 | Kafka, Outbox, idempotent consumer | `api/events`, `api/notifications/consumer.py` |
| 4 | Saga, to'lov, kompensatsiya | `api/payments/*` |
| 5 | CQRS, Elasticsearch | `api/search/*` |
| 6 | Kesh, yuk testi | `api/schedule/cache.py`, `apps/loadtest/slots.js` |
| 7 | Kubernetes, metrika, tracing | `k8s/`, `api/observability/*`, `analytics/*` |

### Arxitektura qatlamlari

```
apps/*        →  faqat MODEL (baza sxemasi, constraint, indeks)
api/*/services.py →  BIZNES LOGIKA (domen qoidalari, tranzaksiya)
api/*/views.py    →  HTTP qatlam (so'rov/javob, status kodlari)
api/*/serializers.py → FORMAT tekshiruvi (UUID'mi? bo'shmasmi?)
api/*/urls.py     →  marshrutlash
```

**Asosiy qoida:** modul boshqa modulning **modelini** import qilmaydi, faqat uning `services.py` sidagi funksiyasini chaqiradi va **DTO** (dataclass) qabul qiladi. Sabab: Faza 4 da bu funksiya chaqiruvi tarmoq chaqiruviga aylanadi, logika o'zgarmaydi.

---

## 2. Loyiha daraxti

```
nurse_new/
├── manage.py                  Django CLI kirish nuqtasi
├── conf/                      Sozlamalar, root URL, WSGI/ASGI
├── apps/                      MODELLAR (Django app'lar)
│   ├── utils/                 BaseModel + OutboxEvent
│   ├── account/               User, Patient, Address, OtpCode
│   ├── catalog/               Clinic, Doctor, Specialization, Service
│   ├── schedule/              WorkingRule, ScheduleException, TimeSlot
│   ├── booking/               Booking, BookingItem + concurrency testlari
│   ├── payment/               Payment + saga testlari
│   └── notifications/         ProcessedEvent, SmsLog
├── api/                       BIZNES LOGIKA + HTTP
│   ├── v1.py                  /api/v1/ marshrutlari
│   ├── account/               OTP autentifikatsiya
│   ├── patients/              Bemor va manzil CRUD
│   ├── catalog/               Shifokor/klinika katalogi (DTO qaytaradi)
│   ├── schedule/              Slot generatsiya + bandlik (loyiha yuragi)
│   ├── booking/               Bron oqimi
│   ├── payments/              Payme webhook + saga
│   ├── events/                Outbox pattern (Kafka)
│   ├── notifications/         gRPC klient, circuit breaker, SMS
│   ├── search/                Elasticsearch CQRS (o'qish modeli)
│   └── observability/         health, metrics, tracing
├── analytics/                 ClickHouse OLAP omborxonasi
├── proto/gen/notification/v1/ gRPC kontrakti (.proto — manba)
├── gen/notification/v1/       protoc chiqishi (generatsiya qilingan, tahrirlamang)
├── loadtest/slots.js          k6 yuk testi
├── k8s/booking-service.yaml   Kubernetes deploy
├── conftest.py                pytest fixture'lari (barcha testlarga ko'rinadi)
├── pytest.ini                 DJANGO_SETTINGS_MODULE va test kashfiyoti
└── db.sqlite3                 Dev bazasi
```

---

## 3. `manage.py` va `conf/` — poydevor

### `manage.py`
| Funksiya | Nima qiladi | Nega |
|---|---|---|
| `main()` | `DJANGO_SETTINGS_MODULE=conf.settings` o'rnatadi, `execute_from_command_line(sys.argv)` chaqiradi | Django'ning standart kirish nuqtasi. `runserver`, `migrate`, `run_outbox_publisher` — hammasi shu orqali |

### `conf/settings.py`
Standart Django 5.0 sozlamalari + loyihaga xos qo'shimchalar:

| Sozlama | Qiymat | Nega |
|---|---|---|
| `INSTALLED_APPS` | `jazzmin` + `apps.*` | jazzmin — admin panel dizayni |
| `DATABASES` | SQLite **yoki** PostgreSQL | `POSTGRES_DB` env berilsa PostgreSQL, aks holda SQLite. Pastda batafsil |
| `TIME_ZONE` | `UTC`, `USE_TZ=True` | **Barcha vaqtlar UTC'da saqlanadi.** Mahalliy vaqtga o'girish faqat `schedule/services.py` da |
| `TIMEOUT_SECONDS = 2.0` | gRPC timeout | "SMS 2 soniyada ketmasa, mijozni kuttirmaymiz" |
| `MAX_ATTEMPTS = 3` | Retry soni | `notifications/klient.py` ishlatadi |
| `BASE_BACKOFF = 0.1` | Backoff bazasi | Eksponensial kutish: 0.1s → 0.2s → 0.4s |
| `RETRYABLE` | `{UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED}` | **Faqat shu gRPC kodlarda qayta uriniladi.** `INVALID_ARGUMENT` da qayta urinish mantiqsiz — so'rovning o'zi xato |
| `NotificationUnavailable` | Exception klassi | Chaqiruvchi buni **yutishi** kerak (fail-open) |

#### `env(name, default)` yordamchisi
Maxfiy qiymatlar (parol, kalit) **hech qachon kodda turmasligi kerak** — ular Kubernetes Secret'idan muhit o'zgaruvchisi sifatida keladi (`k8s/booking-service.yaml` dagi `envFrom.secretRef` shu uchun). Kodadagi default'lar faqat lokal ishlab chiqish uchun.

#### `AUTH_USER_MODEL = "account.User"`
Format **`app_label.ModelName`**, modul yo'li emas. `apps.account` app'ining label'i oxirgi bo'lak — `account`.

#### `REST_FRAMEWORK`
| Kalit | Qiymat | Nega |
|---|---|---|
| `EXCEPTION_HANDLER` | `api.booking.utils.custom_exception_handler` | Xato kodlari bitta joyda: `InvalidBookingRequest`→400, `BookingError`→409 |
| `DEFAULT_AUTHENTICATION_CLASSES` | `JWTAuthentication` (simplejwt bo'lsa) | — |
| `DEFAULT_PERMISSION_CLASSES` | `IsAuthenticated` | **"Default deny".** Ochiq endpoint'lar o'zida `permission_classes = []` yozadi — ya'ni ochiqlik **ataylab va ko'rinadigan qaror** bo'ladi, tasodif emas |
| `DEFAULT_THROTTLE_CLASSES` | `ScopedRateThrottle` | View'lardagi `throttle_scope` ishlashi uchun |
| `DEFAULT_THROTTLE_RATES` | `{"otp": "10/hour"}` | OTP endpoint'lari IP bo'yicha. Bu `services.py` dagi telefon bo'yicha cheklovning **o'rniga emas, ustiga** |

#### `SIMPLE_JWT`
`ACCESS_TOKEN_LIFETIME=15min`, `REFRESH_TOKEN_LIFETIME=30kun`, `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True` (ishlatilgan refresh token qora ro'yxatga tushadi), `SIGNING_KEY=env("JWT_SIGNING_KEY", SECRET_KEY)`.

`_HAS_SIMPLEJWT = find_spec(...)` — paket o'rnatilmagan bo'lsa `INSTALLED_APPS` ga qo'shilmaydi va JWT auth yoqilmaydi. **Bu tekshiruvsiz paket yo'q holatda Django umuman ishga tushmaydi** (`ModuleNotFoundError` `apps.populate()` da). Paket hozir o'rnatilgan (`djangorestframework-simplejwt` 5.5.1), shuning uchun JWT faol.

#### `CACHES`
`REDIS_URL` berilgan bo'lsa Redis, aks holda `LocMemCache`.
> ⚠️ `LocMemCache` **har protsessda alohida**: bir necha worker/pod bo'lganda ular bir-birining keshini ko'rmaydi va `invalidate_doctor_day` faqat o'z protsessida ishlaydi. **Prodda Redis majburiy.**

#### `DATABASES` — env orqali almashtiriladi
```python
if env('POSTGRES_DB'):   # PostgreSQL
else:                    # SQLite (lokal qulaylik)
```
**Nega bu almashtirish kerak:** SQLite bitta yozuvchiga mo'ljallangan. `apps/booking/tests.py` dagi 100 tomonli parallel bandlik testi SQLite'da `database table is locked` beradi — ya'ni loyihaning **asosiy invariantini** SQLite'da isbotlab bo'lmaydi. Compare-and-set logikasi to'g'ri, lekin uni sinash uchun haqiqiy parallel yozishni ko'taradigan baza kerak.

```bash
POSTGRES_DB=medbron POSTGRES_USER=postgres POSTGRES_PASSWORD=... pytest
```

#### Tashqi servislar
`KAFKA_BOOTSTRAP_SERVERS`, `NOTIFICATION_GRPC_ADDR`, `ELASTICSEARCH_URL`, `CLICKHOUSE_HOST` — hammasi `env()` orqali, lokal default'lar bilan.

`OTEL_EXPORTER_OTLP_ENDPOINT` — **bo'sh qoldirilsa tracing o'chiq**. Ilgari manzil kodda qattiq yozilgandi (`jaeger-collector:4317`) va lokalda/testlarda mavjud bo'lmagan hostga uzluksiz urinib stderr'ni ogohlantirishlar bilan to'ldirardi. Kuzatuv vositasi ishlab chiqishni xalaqit qilmasligi kerak.

#### To'lov provayderlari
`PAYME_SECRET_KEY`, `PAYME_MERCHANT_ID`, `CLICK_MERCHANT_ID` — **default yo'q**, faqat muhitdan.

Kalit bo'sh bo'lsa `verify_payme_signature()` har so'rovni rad etadi — bu **xavfsiz nosozlik (fail-closed)**. Teskarisi falokat bo'lardi: hujumchi `PerformTransaction` yuborib, pul to'lamasdan bronni tasdiqlatib olardi.

```python
if not DEBUG and not PAYME_SECRET_KEY:
    raise ImproperlyConfigured("PAYME_SECRET_KEY o'rnatilmagan")
```
> Prodda bu sozlamasiz ishga tushish — jimgina to'lovni buzish demak. **Erta va shovqinli yiqilish, keyin sekin sirli nosozlikdan yaxshiroq.**

> ~~⚠️ **Xavfsizlik (hali qolgan):** `DEBUG = True`, `SECRET_KEY` kodda ochiq, `ALLOWED_HOSTS = []` — bular faqat dev uchun. Prodda `env` orqali berilishi kerak.~~ ✅ Muhitdan olinadi, prodda majburiy (20-bo'lim, 10–12-bandlar).

### `conf/urls.py`
```
/admin/     → Django admin
/api/v1/    → api/v1.py
```

### `conf/wsgi.py`, `conf/asgi.py`
`application` obyektini yaratadi — gunicorn/uvicorn shu obyektni ishga tushiradi.

### `api/v1.py` — versiyalangan marshrutlar
```
/api/v1/accounts/  → api.account.urls   (OTP)
/api/v1/booking/   → api.booking.urls   (slot, bron)
/api/v1/catalog/   → api.catalog.urls   (shifokor, klinika)
/api/v1/patient/   → api.patients.urls  (bemor, manzil)
```
> ~~`api.payments.urls` bu yerga **hali ulanmagan** — Payme webhook marshruti ochilmagan.~~ ✅ Ulandi: `payment/`, `schedule/`, `observability/` ham qo'shilgan.

---

## 4. `apps/` — modellar qatlami

### 4.1 `apps/utils/models.py`

#### `BaseModel` (abstract)
| Maydon | Nega |
|---|---|
| `id = UUIDField(default=uuid4)` | **Avtoinkrement emas, UUID.** Sabab izohda: "keyinchalik servislarga ajratganda avtoinkrement id'lar to'qnashadi — har bir servisning o'z bazasi bo'ladi" |
| `created_at` (db_index) | Indeks — vaqt bo'yicha saralash tez bo'lishi uchun |
| `updated_at` (auto_now) | Oxirgi o'zgarish |

#### `OutboxEvent`
**Transactional Outbox pattern.** Muammo: `Booking.save()` va `kafka.send()` — ikki xil tizim, ikkalasini bitta tranzaksiyaga sig'dirish mumkin emas. Baza yozildi, Kafka yiqildi → hodisa yo'qoladi.

Yechim: hodisani **shu bazaga**, domen yozuvi bilan **bitta tranzaksiyada** yozamiz. Keyin alohida jarayon (`OutboxPublisher`) uni Kafka'ga uzatadi.

| Maydon | Vazifasi |
|---|---|
| `topic` | Kafka topic nomi (`booking.events`) |
| `key` | Partition kaliti (masalan `booking_id`) — **tartib shu bo'yicha kafolatlanadi** |
| `event_type` | `"BookingConfirmed"` |
| `payload` | JSONField — hodisa ma'lumoti |
| `status` | `pending` / `published` / `failed` |
| `attempts`, `last_error` | Retry hisobi va diagnostika |
| `Index(["status", "created_at"])` | Publisher shu indeksdan navbatdagi ishni topadi |

### 4.2 `apps/account/models.py`

> ~~⚠️ **Faylda `User` klassi IKKI MARTA e'lon qilingan.** Ikkinchisi (`AbstractBaseUser` asosidagi) birinchisini bekor qiladi — izohda "TUZATILGAN MODEL — oldingi versiyani shu bilan almashtiring" deb yozilgan, lekin eski versiya o'chirilmagan. Buni tozalash kerak. Amalda ishlaydigan — **ikkinchi** `User`.~~ ✅ Tuzatildi (20-bo'lim).

#### `User` (ishlaydigan versiya — `AbstractBaseUser` + `PermissionsMixin`)
- `USERNAME_FIELD = "phone"` — **telefon asosiy identifikator**, email emas ("O'zbekistonda email kam ishlatiladi")
- `AbstractBaseUser` beradi: `password`, `last_login`, `set_password`, `check_password`
- `PermissionsMixin` beradi: `is_superuser`, `groups`, `user_permissions`
- Ularsiz `request.user`, DRF permission'lari va Django admin ishlamaydi
- `Role`: `client` / `doctor` / `clinic_admin` / `platform_admin`
- `Index(["role", "is_active"])` — "faol shifokorlar" so'rovi uchun

#### `UserManager(BaseUserManager)`
| Metod | Nima qiladi | Nega |
|---|---|---|
| `create_user(phone, full_name)` | Telefonsiz `ValueError`, `set_unusable_password()` qo'yadi | **OTP orqali kiradigan foydalanuvchida parol yo'q** |
| `create_superuser(phone, password)` | `is_staff`, `is_superuser`, `is_phone_verified=True`, `role=PLATFORM_ADMIN` | `createsuperuser` komandasi uchun. Bunda parol **bor** |

Nega custom manager kerak: Django'ning standarti `username` kutadi, bizda u yo'q.

#### `Patient`
Bemor — foydalanuvchining **o'zi yoki oila a'zosi**. Dizayndagi "Bemorni tanlash: Siz / Onam / Otam" ekrani.
- `owner = FK(User)` — kim qo'shgan
- `relation` — "Onam", "Otam", "O'zim"
- `birth_date`, `gender`, `weight_kg` (dori dozasi uchun)
- **Bron `User`ga emas, `Patient`ga bog'lanadi** — chunki ota o'g'li uchun bron qilishi mumkin

#### `Address`
Uy chaqiruvi manzili. `entrance` (podyezd), `floor`, `apartment`, `comment` ("Podyezd oldida it bor"), `latitude`/`longitude` (geo-qidiruv uchun), `is_default`.

#### `OtpCode`
SMS tasdiqlash kodi. **Kod ochiq saqlanmaydi — faqat xeshi** (`code_hash`).

Izohdagi mulohaza: 6 xonali kod uchun xeshlash brute-force'dan to'liq himoya qilmaydi (10⁶ variant), lekin urinishlar soni cheklangani uchun ikkalasi **birga** ishlaydi.

- `MAX_ATTEMPTS = 5`, `TTL_MINUTES = 5`
- `ip_address` — IP bo'yicha rate limit uchun
- `is_expired` (property) — `expires_at < now()`

### 4.3 `apps/catalog/models.py`

| Model | Vazifasi | Muhim tafsilot |
|---|---|---|
| `Clinic` | Klinika | `status`: draft/pending/active/suspended (moderatsiya). `Index(["latitude","longitude"])` — geo |
| `Specialization` | Gastroenterolog, Terapevt, LOR | `is_pediatric` — "Bolalar" filtri uchun |
| `Doctor` | Shifokor | `user = OneToOne(User, on_delete=PROTECT)` — foydalanuvchini o'chirish shifokor profilini yo'q qilmasligi kerak. `accepts_home_visits`, `home_visit_radius_km` |
| `DoctorAffiliation` | Shifokor ↔ Klinika | **`Doctor` `Clinic`ga to'g'ridan-to'g'ri bog'lanmaydi** — chunki shifokor klinikada ishlashi HAM, mustaqil uy chaqiruvi qabul qilishi HAM mumkin. `UniqueConstraint(doctor, clinic)` |
| `Service` | Konsultatsiya, ukol, UZI | `place`: clinic/home. `duration_minutes` (min 5) — **slot uzunligi shundan olinadi**. `price`. `clinic` va `doctor` ikkalasi ham nullable, lekin `CheckConstraint(clinic IS NOT NULL OR doctor IS NOT NULL)` — xizmat yo klinikaniki, yo shifokorniki |

### 4.4 `apps/schedule/models.py`

#### `WorkingRule`
Takrorlanuvchi jadval: "dushanba, 09:00–17:00, klinikada". Bundan aniq `TimeSlot`lar generatsiya qilinadi.
- `weekday` (0=Dushanba … 6=Yakshanba), `start_time`, `end_time`, `slot_minutes` (default 30)
- `valid_from` / `valid_to` — qoida qachondan qachongacha amal qiladi
- `clinic = null` → **uy chaqiruvi rejimi**

#### `ScheduleException`
Ta'til, bayram, kasallik. Shu kunlarga slot generatsiya qilinmaydi. `UniqueConstraint(doctor, date)`.

#### `TimeSlot` — **loyihaning eng muhim modeli**
```python
UniqueConstraint(fields=["doctor", "start_at"], name="uniq_doctor_slot_start")
```
Bu bir qator **butun tizimning asosiy invariantini** kafolatlaydi:

> *Bitta shifokor bitta vaqtda faqat bitta joyda band bo'ladi — klinikadami, uy chaqiruvidami, farqi yo'q.*

Constraint'da `clinic` **ataylab yo'q**. Shu tufayli shifokor bir vaqtda "klinikada" va "uyda" bo'lolmaydi. Bu qoidani baza o'zi qo'riqlaydi, kod emas — kod xato qilishi mumkin, baza yo'q.

| Element | Vazifasi |
|---|---|
| `Status` | `free` → `held` (to'lov kutilmoqda) → `booked`; `blocked` (yopilgan) |
| `start_at`, `end_at` | **Har doim UTC** |
| `hold_expires_at` | HELD holati uchun: shu vaqtdan keyin avtomatik FREE ga qaytadi |
| `CheckConstraint(end_at > start_at)` | Teskari oraliq bazaga tushmaydi |
| `Index(["doctor","status","start_at"])` | "Falon shifokorning ertangi bo'sh vaqtlari" so'rovi uchun |
| `is_expired_hold` (property) | HELD + `hold_expires_at < now()` |

### 4.5 `apps/booking/models.py`

#### `Booking`
Holatlar ketma-ketligi:
```
PENDING_PAYMENT → CONFIRMED → COMPLETED
                ↘ CANCELLED / EXPIRED / NO_SHOW
```
| Maydon | Nega |
|---|---|
| `number` (unique) | Mijozga ko'rsatiladigan raqam `MB-260729-K4T9`. **UUID'ni mijozga ko'rsatmang — telefonda aytib bo'lmaydi** |
| `client`, `patient`, `doctor` — `on_delete=PROTECT` | Bron bor bo'lsa, bu yozuvlarni o'chirib bo'lmaydi (moliyaviy/huquqiy iz) |
| `slot = OneToOneField` | **Bitta slot — bitta bron.** Yana bir himoya qatlami |
| `address` | Uy chaqiruvi bo'lsa manzil, klinikada bo'lsa `null` |
| `Index(["status","created_at"])` | Muddati o'tgan bronlarni topish uchun |

#### `BookingItem`
Bir bronda bir necha xizmat bo'lishi mumkin. `service_name`, `price`, `duration_minutes` — **SNAPSHOT** sifatida saqlanadi.

> Nega snapshot: klinika ertaga narxni oshirsa, eski bronlar va ulardan chiqadigan hisobotlar **o'zgarmasligi kerak**. Bu moliyaviy ma'lumot bilan ishlashning asosiy qoidasi.

### 4.6 `apps/payment/models.py`

#### `Payment`
- `Provider`: payme / click / cash
- `Status`: created → processing → succeeded / failed / refunded
- `external_id` — provayderdagi tranzaksiya ID
- **`idempotency_key` (unique)** — bir so'rov ikki marta kelsa, pul ikki marta yechilmaydi

### 4.7 `apps/notifications/models.py`

| Model | Vazifasi |
|---|---|
| `ProcessedEvent` | `event_id` = **primary key**. Idempotentlik reestri: shu hodisa ishlanganmi? PK bo'lgani uchun ikkinchi `create()` `IntegrityError` beradi — bu aynan kerak |
| `SmsLog` | Har bir SMS yozuvi — audit va nizolarni hal qilish uchun ("mijoz SMS kelmadi deyapti"). **Kontent saqlanmaydi**, faqat shablon nomi — shaxsiy ma'lumotni keraksiz joyda ko'paytirmaslik uchun |

### 4.8 `apps/*/apps.py`, `admin.py`, `views.py`

Deyarli hammasi bo'sh stub. **Bitta muhim istisno:**

```python
# apps/booking/apps.py
class BookingConfig(AppConfig):
    def ready(self):
        from observability.tracing import setup_tracing
        setup_tracing(service_name="booking-service")
```
Django ishga tushganda **bir marta** OpenTelemetry tracing yoqiladi.
> ~~⚠️ Import yo'li xato: `observability.tracing` emas, `api.observability.tracing` bo'lishi kerak.~~ ✅ Tuzatildi.

### 4.9 `apps/utils/management/commands/run_outbox_publisher.py`
```
python manage.py run_outbox_publisher
```
`OutboxPublisher().run_forever()` chaqiradi — alohida jarayon sifatida ishlaydi (Kubernetes'da alohida Deployment).

---

## 5. `api/account/` — OTP autentifikatsiya

### `services.py` — biznes logika

**Exception ierarxiyasi:**
```
AuthError
├── TooManyRequests   → HTTP 429
└── InvalidCode       → HTTP 400
```

#### `request_otp(phone, ip_address)`
> **BU ENDPOINT PUL SARFLAYDI.** Har bir SMS haqiqiy pul. Rate limit qo'yilmasa hujumchi bir kechada butun SMS byudjetini yoqib yuboradi, va begona odamning telefoniga uzluksiz SMS yog'diradi.

**Uch qatlam himoya:**
1. `RESEND_COOLDOWN_SECONDS = 60` — oxirgi koddan 60 soniya o'tmasa rad etiladi
2. `MAX_CODES_PER_PHONE_HOUR = 5` — bitta raqamga soatda 5 ta
3. `MAX_CODES_PER_IP_HOUR = 20` — bitta IP'dan soatda 20 ta

Keyingi qadamlar:
4. Eski kodlarni bekor qiladi (`is_used=True`) — **bir vaqtda faqat bitta kod amal qiladi**
5. `f"{secrets.randbelow(1_000_000):06d}"` — 6 xonali kod. **`secrets`, `random` emas**: `random` bashorat qilinadigan (Mersenne Twister), `secrets` kriptografik. Tasdiqlash kodi uchun bu farq muhim
6. `make_password(code)` — xeshni saqlaydi
7. `_send_sms(phone, code)`

> Izohda: "Faza 3 da bularni Redis'ga ko'chiring — hozircha DB count yetarli, lekin har so'rovda uchta COUNT so'rovi ketadi va yuklama ostida bu sezilaadi."

#### `verify_otp(phone, code) → (User, is_new)`
Ro'yxatdan o'tish va kirish — **bitta oqim**: raqam tizimda bo'lmasa, foydalanuvchi shu yerda yaratiladi. Bu mobil ilovalar uchun odatiy yondashuv va bitta ekranni kamaytiradi.

Tartib muhim:
1. Eng yangi ishlatilmagan kodni oladi; yo'q yoki eskirgan bo'lsa → `InvalidCode`
2. **Urinishni AVVAL sanaydi** (`F("attempts") + 1`) — tekshiruvdan keyin emas. Aks holda xato yuz berganda hisoblagich oshmay qolishi mumkin
3. `attempts > MAX_ATTEMPTS` → kodni kuydiradi
4. `check_password(code, otp.code_hash)` — Django xesh solishtiruvi
5. `transaction.atomic()` ichida: kodni `is_used=True`, foydalanuvchini topadi yoki yaratadi
6. `is_active=False` → `AuthError("Akkaunt bloklangan")`
7. Logga `_mask(phone)` yozadi — to'liq raqam emas

#### `issue_tokens(user) → {"access", "refresh"}`
JWT juftligi. `access` 15 daqiqa (har so'rovda ketadi), `refresh` 30 kun (kamdan-kam ketadi).
> Nega ikkitasi: access o'g'irlansa tez eskiradi; refresh kamdan-kam uzatiladi, ushlab olish ehtimoli past.
`refresh["role"] = user.role` — gateway shu maydonga qarab marshrutlaydi.
`RefreshToken.for_user(user)` — `rest_framework_simplejwt.tokens` dan. `access_token` refresh'dan olinadi, ya'ni ikkalasi bir xil `jti` zanjiriga tegishli bo'ladi.

#### Ichki yordamchilar
| Funksiya | Nima qiladi |
|---|---|
| `_send_sms(phone, code)` | ~~`DEBUG=True` → konsolga log; aks holda `NotImplementedError`~~ ✅ `otp` shabloni + `get_sms_client()` (console/Eskiz). Xato → `SmsUnavailable` → 503. **Kodni hech qachon prod loglariga yozmang** |
| `_normalize_phone(phone)` | `+998 99 756 71 95` → `+998997567195`. Normalizatsiyasiz bitta odam bazada bir necha marta paydo bo'ladi |
| `_mask(phone)` | `+998997******` — logga to'liq raqam yozmaslik |

### `views.py`
| View | Endpoint | Nima qiladi |
|---|---|---|
| `RequestOtpView` | `POST /api/v1/accounts/auth/otp/request` | `ScopedRateThrottle` (scope `"otp"`) — **DRF throttle servis darajasidagi limit ustiga qo'shimcha qatlam**. Throttle IP bo'yicha tez ishlaydi, `services.py` dagi cheklov telefon bo'yicha va aniqroq. Javob **har doim bir xil**: `{"detail": "Kod yuborildi"}` — **raqam tizimda bor-yo'qligini oshkor qilmaymiz** (user enumeration himoyasi) |
| `VerifyOtpView` | `POST .../auth/otp/verify` | Kod tekshiradi, `InvalidCode` → 400, `AuthError` → 403, muvaffaqiyatda tokenlar + `is_new_user` + user ma'lumoti |

### `serializers.py`
`RequestOtpSerializer` (`phone`), `VerifyOtpSerializer` (`phone`, `code` — aynan 6 belgi).

### `utils.py`
`_client_ip(request)` — `X-Forwarded-For` ning birinchi elementini, bo'lmasa `REMOTE_ADDR` ni qaytaradi.
> ⚠️ Izohda ogohlantirish: `X-Forwarded-For` ni **faqat o'zingizning proxy ortida** ishonch bilan o'qing. Aks holda hujumchi uni o'zi yozib IP cheklovini butunlay chetlab o'tadi.

---

## 6. `api/patients/` — bemor va manzil CRUD

### `views.py`

#### `PatientViewSet(ModelViewSet)` — `/api/v1/patient/patients/`
```python
def get_queryset(self):
    return Patient.objects.filter(owner=self.request.user)
```
> ⚠️ **ENG MUHIM QATOR SHU FAYLDA.** Buni unutish — boshqa mijozning bemor ro'yxatini ID orqali ko'rsatib qo'yadigan klassik **IDOR** zaifligi (OWASP Top 10).

`perform_create` — `owner=request.user` **serverda** qo'yiladi.

#### `AddressViewSet(ModelViewSet)` — `/api/v1/patient/addresses/`
Xuddi shu himoya (`filter(user=request.user)`). `perform_create`/`perform_update` da `is_default=True` bo'lsa `_clear_other_defaults()` chaqiriladi.

### `serializers.py`
`PatientSerializer` — maydonlar ro'yxatida **`owner` YO'Q**.
> Nega: uni `request.user` dan olamiz, klient tanasidan emas. Aks holda mijoz boshqa odamning profiliga o'zini "egasi" qilib yozib qo'yishi mumkin edi.

### `utils.py`
`_clear_other_defaults(user, *, keep)` — bir foydalanuvchida faqat bitta standart manzil. Bitta `UPDATE` so'rovi. Signal yoki `save()` ichida emas, ataylab shu yerda — kod o'qigan odam qoidani darhol ko'radi.

---

## 7. `api/catalog/` — katalog moduli

### `services.py` — modul tashqi interfeysi

> **Nega DTO qaytaramiz, model obyektini emas:**
> Agar `Service` model obyektini qaytarsak, booking moduli bexosdan `service.clinic.name` deb yozib qo'yishi mumkin — va bu **ishlaydi**. Django ORM jimgina qo'shimcha so'rov qiladi, chegara buzilganini hech kim sezmaydi. Faza 4 da servisga ajratganda esa o'sha qator to'satdan yiqiladi va sababini topish qiyin bo'ladi.
>
> DTO buni oldini oladi: unda faqat kelishilgan maydonlar bor. Va aynan shu `dataclass` keyinchalik `.proto` message'ga aylanadi.

**DTO'lar** (`@dataclass(frozen=True)` — o'zgarmas): `ServiceDTO`, `DoctorDTO`, `ClinicDTO`.

| Funksiya | Nima qiladi | Nega shunday |
|---|---|---|
| `is_doctor_bookable(doctor_id) → bool` | `status=APPROVED AND user.is_active` | **Bitta joyda turgan qoida.** Bu shartni view'larga tarqatib yubormang — ertaga litsenziya muddati qo'shilsa, faqat shu funksiyani tuzatasiz |
| `get_services(ids) → list[ServiceDTO]` | Faol xizmatlarni oladi | **Topilmagan xizmatlar ro'yxatga tushmaydi.** Chaqiruvchi uzunlikni solishtirib tekshiradi. Ataylab shunday: har bir chaqiruvchi "topilmadi"ni boshqacha talqin qilishi mumkin |
| `get_doctor(id) → DoctorDTO \| None` | `select_related("user")` + `prefetch_related("specializations")` | N+1 ni oldini oladi |
| `list_doctors(spec, clinic, home_only, limit, offset)` | Filtrlangan ro'yxat, `-rating, -reviews_count` bo'yicha | `select_related`/`prefetch_related` **shart**: ularsiz 20 shifokor uchun 41 so'rov ketadi. `.distinct()` — M2M join dublikat bermasligi uchun. Faza 5 da bu funksiya Elasticsearch'ga ko'chadi |
| `list_clinics(city, limit, offset)` | Faqat `ACTIVE` klinikalar | — |
| `list_doctor_services(doctor_id, place)` | Narx bo'yicha saralangan | Bron oynasidagi "Xizmat tanlash" |
| `_to_service_dto` / `_to_doctor_dto` / `_to_clinic_dto` | ORM → DTO konvertorlari | Konvertatsiya bitta joyda |

### `views.py`
| View | Endpoint | Izoh |
|---|---|---|
| `SpecializationListView` | `GET /api/v1/catalog/specializations` | Kamdan-kam o'zgaradi → agressiv keshlash mumkin (Faza 6) |
| `ClinicListView` | `GET .../clinics?city=` | — |
| `DoctorListView` | `GET .../doctors?specialization=&clinic=&home_only=&limit=&offset=` | Dizayndagi "Gastroenterolog" ro'yxat ekrani |
| `DoctorDetailView` | `GET .../doctors/{id}` | Topilmasa 404 |
| `DoctorServicesView` | `GET .../doctors/{id}/services?place=home` | `place` faqat `clinic`/`home` bo'lishi mumkin, aks holda 400. **Klinika va uy xizmatlari bitta bronda aralashmasligi kerak** — frontend qaysi rejimda ekanini bilib mos ro'yxat so'rashi kerak |

### `serializers.py`
`DoctorListSerializer` va detail **ataylab ajratilgan**: ro'yxat 20+ shifokorni bitta so'rovda qaytaradi, shuning uchun har bir elementni yengil tutish kerak. `bio`, `license_number` kabi og'ir maydonlar faqat detail'da.
> ~~⚠️ `DoctorDetailSerializer` hali yozilmagan — `DoctorDetailView` `DoctorListSerializer` ishlatadi.~~ ✅ Yozildi (20-bo'lim, 22-band).

### `utils.py`
| Funksiya | Nima qiladi |
|---|---|
| `_int_param(request, name, default, max_value)` | Query paramni butun songa o'giradi. Xato/manfiy bo'lsa `default`. `max_value` bilan cheklaydi — **`limit=999999` bilan bazani cho'ktirishdan himoya** |
| `_bool_param(request, name)` | `"1"`, `"true"`, `"yes"` → `True` |

---

## 8. `api/schedule/` — jadval va bandlik (**loyiha yuragi**)

### Modul chegarasi qoidasi
> Bu fayl `catalog` yoki `booking` modellarini **import qilmaydi**. Barcha funksiyalar `doctor_id` (UUID) qabul qiladi, `Doctor` obyektini emas. Shu tufayli Faza 2–4 da bu modulni alohida servisga ko'chirish **mexanik ish** bo'ladi: faqat funksiya chaqiruvlari tarmoq chaqiruviga aylanadi, logika o'zgarmaydi.

### Konstantalar
| Konstanta | Qiymat | Nega |
|---|---|---|
| `LOCAL_TZ` | `Asia/Tashkent` | Qoidalar mahalliy vaqtda yozilgan, bazada UTC |
| `HOLD_TTL_MINUTES` | 10 | To'lov kutish muddati |
| `GENERATION_HORIZON_DAYS` | 30 | Necha kun oldinga slot yaratiladi |
| `HOME_VISIT_BUFFER_MINUTES` | 30 | Uy chaqiruvi uchun yo'l vaqti buferi. v1 qat'iy 30 daqiqa, v2 da masofaga qarab hisoblanadi |
| `MIN_LEAD_TIME_MINUTES` | 60 | Eng yaqin bron 1 soatdan keyin |

### 8.1 Slot generatsiyasi

#### `generate_slots(doctor_id, days=30) → int`
**IDEMPOTENT** — istalgan marta qayta chaqirsangiz dublikat yaratmaydi. Buni `bulk_create(ignore_conflicts=True)` + `uniq_doctor_slot_start` constraint ta'minlaydi.
> Nega muhim: bu funksiya har kecha cron orqali ishlaydi va ba'zan ikki marta ishga tushib ketadi.

Oqim:
1. Amaldagi `WorkingRule`larni oladi (`valid_from <= horizon`, `valid_to IS NULL OR >= today`)
2. `ScheduleException` sanalarini `set()` ga yig'adi — ta'til/bayram
3. Har qoida × har kun: hafta kuni mos kelmasa/istisno kunda bo'lsa/amal muddatidan tashqarida bo'lsa — o'tkazadi
4. `bulk_create(..., ignore_conflicts=True, batch_size=500)`

> ⚠️ Izohda: `ignore_conflicts` bilan `bulk_create` PostgreSQL'da haqiqatan yaratilganlar sonini ishonchli qaytarmaydi. Aniq son kerak bo'lsa oldin/keyin `count()` qiling.

#### `_iter_dates(start, end)` — generator
Sanalar bo'yicha yuradi. Generator — millionlab sanani ro'yxatga yig'ish shart emas.

#### `_build_slots_for_day(rule, day) → list[TimeSlot]`
**Vaqt mintaqasi bitta joyda hal qilinadi:** qoida mahalliy vaqtda ("09:00 da ishni boshlayman"), bazaga UTC'da yoziladi.
```python
cursor = datetime.combine(day, rule.start_time, tzinfo=LOCAL_TZ)
while cursor + step <= day_end:      # oxirgi to'liq bo'lmagan slot yaratilmaydi
    TimeSlot(start_at=cursor.astimezone(dt_timezone.utc), ...)   # datetime.timezone
```
> O'zbekistonda yozgi vaqtga o'tish yo'q, shuning uchun sodda. Boshqa mamlakatda DST kunlarida bir kunda 23 yoki 25 soat bo'lishi hisobga olinishi kerak — shuning uchun ham **naive datetime ishlatmaymiz**.

### 8.2 O'qish

#### `get_available_slots(doctor_id, clinic_id, date_from, date_to) → QuerySet`
`status=FREE` va `start_at >= max(now + 60min, date_from)`.
> Bu tizimdagi **eng ko'p chaqiriladigan so'rov** — dizayndagi har bir shifokor kartochkasi ostida bo'sh vaqtlar bor. Faza 6 da aynan shu funksiya keshlanadi va 10 000 rps ga optimallashtiriladi. "Hozircha oddiy qoldiring. Avval ishlasin, keyin o'lchaysiz."

QuerySet qaytaradi (list emas) — chaqiruvchi qo'shimcha filtr/slice qo'shishi mumkin, lazy baholanadi.

### 8.3 Bandlik — **eng muhim qism**

#### `hold_slot(slot_id, ttl_minutes=10) → TimeSlot`
> **BU FUNKSIYA LOYIHADAGI ENG MUHIM 10 QATOR.**

**NOTO'G'RI usul:**
```python
slot = TimeSlot.objects.get(id=slot_id)
if slot.status != FREE:      # ← shu yerda boshqa so'rov oraga tushib ketadi
    raise SlotNotAvailable
slot.status = HELD
slot.save()
```
Bu klassik **race condition**: ikki so'rov ham `if` dan o'tib ketadi. Lokal kompyuterda hech qachon ko'rinmaydi, prodda ko'rinadi.

**TO'G'RI usul — compare-and-set:** shartni `UPDATE` ning o'ziga qo'yish.
```python
updated = TimeSlot.objects.filter(
    id=slot_id, status=FREE          # ← shart UPDATE ichida
).update(status=HELD, hold_expires_at=expires_at)

if updated == 0:
    raise SlotNotAvailable
```
Baza `UPDATE ... WHERE status='free'` ni **atomik** bajaradi, shuning uchun 100 ta parallel so'rovdan aynan bittasi `1` qaytaradi.

Keyin: agar `clinic_id is None` (uy chaqiruvi) → `_block_travel_buffer(slot)`.

#### `_block_travel_buffer(slot) → int`
Shifokor 12:00 da bemor uyida bo'lsa, 12:30 da boshqa manzilda bo'la olmaydi — yo'lda vaqt ketadi. `±30 daqiqa` oralig'idagi `FREE` qo'shni slotlarni `BLOCKED` qiladi.

> Bu yerda compare-and-set yetmaydi, chunki **bir nechta qator** bilan ishlaymiz. `select_for_update()` bilan qulflaymiz. Qatorlarni **har doim bir xil tartibda** (`order_by("start_at")`) qulflang — aks holda ikki tranzaksiya bir-birini kutib **deadlock** bo'ladi.

#### `confirm_slot(slot_id)`
`HELD → BOOKED`, `hold_expires_at=None`. Yana compare-and-set (`filter(status=HELD)`). `updated == 0` → `SlotNotAvailable`.

#### `release_slot(slot_id)`
**Kompensatsiya amali:** `HELD`/`BOOKED` → `FREE`. Faza 4 da saga to'lov yiqilganda shuni chaqiradi, shuning uchun **IDEMPOTENT** bo'lishi shart:
```python
if slot is None or slot.status == FREE:
    return                    # allaqachon bo'sh — bu xato emas
```
Uy chaqiruvi bo'lsa, yo'l vaqti uchun `BLOCKED` qilingan qo'shnilarni ham `FREE` ga qaytaradi.

#### `release_expired_holds() → int`
Har daqiqada background job (Faza 3 da Celery, keyinroq Kubernetes CronJob).
> Nega kerak: mijoz to'lov oynasini ochib yopib qo'ysa, slot **abadiy HELD** holatida qolib ketadi va hech kim uni band qila olmaydi.

Bir marta maksimum 1000 slot (`[:1000]`) — bitta ish sikli cheksiz cho'zilmasligi uchun.

### 8.4 `cache.py` — keshlash (Faza 6)

| Element | Vazifasi |
|---|---|
| `CACHE_TTL_SECONDS = 30` | Kesh yashash muddati |
| `CACHE_VERSION = "v1"` | Sxema o'zgarganda oshirasiz — eski keshlar avtomatik "yo'qoladi" (kalit o'zgaradi) |
| `_cache_key(doctor_id, day)` | `slots:v1:{uuid}:{2026-07-30}` |
| `get_available_slots_cached(doctor_id, day)` | **Cache-aside pattern:** kesh o'zi bazaga bormaydi. Biz keshni tekshiramiz, bo'sh bo'lsa bazadan olamiz va keshga yozamiz. Eng keng tarqalgan va eng oson tushuniladigan naqsh |
| `invalidate_doctor_day(doctor_id, day)` | **Event-based invalidatsiya.** `booking/services.py:_invalidate_slot_cache` orqali `create_booking`, `confirm_booking` va `cancel_booking` da `transaction.on_commit` bilan chaqiriladi. ⚠️ Buni unutish — eng ko'p uchraydigan keshlash bug'i: band qilingan slot 30 soniyagacha "bo'sh" ko'rinib turadi. TTL ni ham qoldiramiz — u **xavfsizlik to'ri** |

Fayl oxirida **cache stampede** (thundering herd) muammosi va `cache.add()` (atomik) orqali lock yechimi izohda tayyor turadi.
> Tavsiya: buni Faza 6 da, k6 testida stampede'ni **o'z ko'zingiz bilan ko'rgandan keyin** qo'shing. Muammoni ko'rmasdan yechim yozish — erta optimizatsiya.

### 8.5 `views.py` / `urls.py` — slots endpoint

| Element | Izoh |
|---|---|
| `DoctorSlotsView` | `GET /api/v1/schedule/doctors/{id}/slots`. **Eng ko'p chaqiriladigan endpoint** — ro'yxat ochilganda 20 shifokor uchun 20 marta. Ochiq (`permission_classes = []`) |
| `SlotSerializer` | Faqat `id`, `start_at`, `end_at` — **bu javob eng ko'p uzatiladi, yengil tutish kerak** |
| `utils.parse_dt` / `parse_date` | `booking/utils.py:_parse_dt` ning ataylab qilingan nusxasi — `schedule` `booking` dan import qilmaydi (modul chegarasi) |

**Ikki rejim, va faqat bittasi keshlanadi:**

| So'rov | Yo'l | Keshlanadimi |
|---|---|---|
| `?date=2026-07-30` yoki **parametrsiz** (→ bugun) | `get_available_slots_cached` | ✅ ha, + `Cache-Control: public, max-age=30` |
| `?date_from=&date_to=` | `get_available_slots` (to'g'ridan-to'g'ri baza) | ❌ yo'q. `MAX_RANGE_DAYS = 30` → 400, natija `[:200]` |

**Nega oraliq keshlanmaydi:** kesh kaliti kun bo'yicha qurilgan (`slots:v1:{doctor}:{kun}`). Ixtiyoriy oraliqni keshlash uchun kalitga ikkala chegarani qo'shish kerak bo'lardi — u holda har bir noyob oraliq o'z yozuvini yaratadi, hit darajasi nolga intiladi va kesh faqat xotira yeydi. Kalit **kam xil** bo'lishi kerak. Oraliq so'rovi kamdan-kam chaqiriladi (kalendar ko'rinishi) — bu to'g'ri almashuv.

**Parametrsiz chaqiruv bugunni beradi** — ataylab: shifokor kartochkasi "eng yaqin bo'sh vaqt"ni so'raydi, ya'ni eng ko'p keladigan so'rov avtomatik keshlanadigan yo'lga tushadi. `slots.js` ham shuning uchun parametrsiz chaqiradi.

> Keshlangan yo'lda javob `SlotSerializer` dan **qayta o'tkazilmaydi** — kesh allaqachon aynan shu shakldagi dict saqlaydi. Keshning ma'nosi ishni o'tkazib yuborishda, uni takrorlashda emas.

> Kelajakda batch endpoint (`?doctor_ids=a,b,c`) kerak bo'ladi — lekin **yuk testidan keyin**, hozir erta optimallashtirish.

---

## 9. `api/booking/` — bron oqimi

### `services.py`

**Exception ierarxiyasi:**
```
BookingError            → HTTP 409 (so'rov to'g'ri, lekin hozir bajarib bo'lmaydi)
└── InvalidBookingRequest → HTTP 400 (so'rov noto'g'ri)
```

#### `BookingRequest` (`@dataclass(frozen=True)`)
`client_id`, `patient_id`, `doctor_id`, `slot_id`, `service_ids`, `address_id`, `comment`.
> Nega dataclass: Faza 4 da bu to'g'ridan-to'g'ri gRPC message'ga aylanadi. Argumentlarni tarqoq holda uzatsangiz, o'sha paytda hamma joyni qayta yozishga to'g'ri keladi.

#### `create_booking(request) → Booking`
Qadamlar (Faza 4 da har biri saga qadami bo'ladi):
1. **Tekshiruvlar** — lokal, tarmoqsiz (`_validate_and_load_services`)
2. **Narxlarni olish** → catalog
3. **Slotni HELD qilish** → schedule
4. **Booking + BookingItem yozish**
5. To'lovni boshlash → payments *(Faza 4 da qo'shiladi)*

```python
with transaction.atomic():        # ⬅ MONOLIT IMTIYOZI
    schedule_services.hold_slot(request.slot_id)
    ...Booking.objects.create(...)
    BookingItem.objects.bulk_create([...])
```
> Ichkarida biror narsa yiqilsa, slot ham avtomatik `FREE` ga qaytadi, chunki `hold_slot` ning `UPDATE`'i ham shu tranzaksiyada. **Faza 4 da bu kafolat yo'qoladi** va o'rniga kompensatsiya yozasiz.

`SlotNotAvailable` → `BookingError("Bu vaqt allaqachon band qilingan")` (409).
`BookingItem`larda narx/nom/davomiylik **snapshot** qilinadi.

#### `confirm_booking(booking_id) → Booking`
To'lov muvaffaqiyatli. **IDEMPOTENT:** allaqachon `CONFIRMED` bo'lsa shunchaki qaytaradi.
> Faza 3 da bu funksiyani Kafka consumer chaqiradi va Kafka xabarni takrorlab yuborishi **mutlaqo normal hol**.

`PENDING_PAYMENT` dan boshqa holatdan tasdiqlab bo'lmaydi → `BookingError`.

#### `cancel_booking(booking_id, reason) → Booking`
Bronni bekor qiladi va slotni bo'shatadi. Bu ayni paytda **SAGA KOMPENSATSIYA AMALI** ham — to'lov yiqilganda shu funksiya chaqiriladi. Shuning uchun idempotent: yakuniy holatlarda (`CANCELLED`/`EXPIRED`/`COMPLETED`) hech narsa qilmaydi.

#### `expire_pending_bookings() → int`
Har daqiqada ishlaydigan job. Muddati o'tgan `HELD` slotlarga bog'langan `PENDING_PAYMENT` bronlarni topadi va har biri uchun **`expire_booking`** chaqiradi (`cancel_booking` EMAS — 20-bo'lim, 14-band).

#### `expire_booking(booking_id) → Booking`
`cancel_booking` dan faqat ikki narsa bilan farq qiladi: yakuniy holat (`expired`) va hodisa turi (`BookingExpired`). Ikkalasi ham umumiy `_release_and_finalize()` ustida quriladi.
> **Nega alohida:** bekor qilish = "mijoz fikridan qaytdi", muddat o'tishi = "mijoz to'lay olmadi". Bitta holatga yig'ilsa, to'lov integratsiyasidagi muammo mijoz xatti-harakati bo'lib ko'rinadi va `cancellation_rate` ni sun'iy oshiradi.
> `schedule.release_expired_holds` bilan **birga** ishlaydi va bir-birini dublikat qilmaydi: bu yerda **BRON** holati, u yerda **SLOT** holati tozalanadi.

> ⚠️ `schedule_services.TimeSlot` deb murojaat qilinadi — bu ishlaydi (modul `TimeSlot`ni import qilgan), lekin modul chegarasi qoidasini buzadi.

#### `_validate_and_load_services(request)`
**Barcha biznes tekshiruvlari bitta joyda:**
1. Kamida bitta xizmat
2. `is_doctor_bookable(doctor_id)` — shifokor bron qabul qiladimi
3. Barcha xizmatlar topildimi (`len(services) != len(ids)` → xato)
4. **Bitta bronda `clinic` va `home` xizmatlarini aralashtirib bo'lmaydi** (`len(places) > 1`)
5. Uy chaqiruvi bo'lsa `address_id` majburiy

> Tekshiruvni view yoki serializer ichiga tarqatib yubormang — keyin servisga ajratganda ularni yig'ib olish og'ir bo'ladi.

#### `_generate_number(attempts=5) → str`
`MB-260729-K4T9`. Alifbo `"23456789ABCDEFGHJKLMNPQRSTUVWXYZ"` — **chalkashadigan belgilar (`0`/`O`, `1`/`I`) chiqarilgan**. 5 marta urinib bandlikni tekshiradi.
> UUID'ni mijozga ko'rsatmang — telefonda aytib bo'lmaydi.

#### `_get_booking(booking_id)` — topilmasa `BookingError`.

#### Fayl oxiridagi "FAZA 4 DA NIMA O'ZGARADI"
1. `transaction.atomic()` yo'qoladi — jadval boshqa bazada
2. `hold_slot` gRPC chaqiruviga aylanadi: timeout, retry, circuit breaker + `idempotency_key`
3. `create_booking` saga bo'ladi: `hold_slot → create_payment → [kutish] → confirm | cancel`, har qadam `SagaState` jadvaliga yoziladi
4. Kompensatsiya zanjiri: to'lov yiqildi → `release_slot` → booking `CANCELLED` → mijozga SMS
5. **Oraliq holat ko'rinadigan bo'ladi** — "pul yechilgan, lekin bron tasdiqlanmagan". Interfeys darajasida hal qilinadi ("to'lov tekshirilmoqda"). Bu — eventual consistency'ning biznes narxi

### `views.py`
| View | Endpoint | Izoh |
|---|---|---|
| ~~`DoctorSlotsView`~~ | → **`api/schedule/views.py`** ga ko'chirildi | Slot jadval modulining tushunchasi; bron uni faqat ishlatadi. Ilgari `booking/views.py` faqat shu view uchun `schedule_services` ni import qilardi — chegara teskari tomonga qaragan edi. Yangi manzil: `GET /api/v1/schedule/doctors/{id}/slots` (13-bo'limga qarang) |
| `MyBookingsView` | `GET .../bookings/mine` | `filter(client=request.user)` + `prefetch_related("items")` (N+1) + `[:50]` |
| `BookingDetailView` | `GET .../bookings/{id}` | `filter(id=..., client=request.user)` — **egalik tekshiruvi query'da** |
| `CreateBookingView` | `POST .../bookings` | Pastda batafsil |
| `CancelBookingView` | `POST .../bookings/{id}/cancel` | Avval egalik tekshiradi (404), keyin `cancel_booking` |

#### `CreateBookingView` — Idempotency-Key
> Mobil ilova tugmani ikki marta bosishi, tarmoq javobni yo'qotib so'rovni takrorlashi **mutlaqo normal hol**. Kalitsiz bunday holatda ikkita bron yaratiladi va mijozdan ikki marta pul yechiladi.

Klient har bron urinishi uchun bitta UUID generatsiya qiladi va `Idempotency-Key` headerida yuboradi. Takroriy so'rovda yangi bron yaratmaymiz — o'shanisini **HTTP 200** bilan qaytaramiz (yangi bron uchun 201).

Model'dagi maydon:
```python
idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True)
```
`null=True` — kalit **majburiy emas** (eski klientlar yubormaydi). SQL'da bir necha `NULL` unique constraint'ni buzmaydi, shuning uchun `unique` va `nullable` birga ishlaydi.

> ⚠️ **Qolgan nozik nuqta:** kalit `create_booking` dan **keyin**, alohida `update()` bilan yoziladi. Ya'ni ikki bir xil so'rov bir vaqtda kelsa, ikkalasi ham `filter(...).first()` tekshiruvidan o'tadi. Lekin ikkinchisi `hold_slot` da to'xtaydi (slot allaqachon `HELD`) va **409** oladi — ya'ni ikkilangan bron baribir yaratilmaydi. Bu yerda haqiqiy kafolatni **slot constraint'i** beradi, idempotency kaliti esa mijozga **to'g'ri javob** (409 emas, o'sha bronning o'zi) qaytarish uchun xizmat qiladi.

### `serializers.py`
| Serializer | Vazifasi |
|---|---|
| ~~`SlotSerializer`~~ | → `api/schedule/serializers.py` ga ko'chirildi (`DoctorSlotsView` bilan birga) |
| `BookingItemSerializer`, `BookingSerializer` | `status_display` — `get_status_display` orqali odam o'qiy oladigan matn |
| `CreateBookingSerializer` | **Faqat FORMAT tekshiriladi** (UUID'mi, ro'yxat bo'shmasmi, `max_length=10`). BIZNES tekshiruvlari `services.py` da. "Ularni bu yerga ko'chirmang, aks holda gRPC ga o'tganda hammasi yo'qoladi" |
| `CancelBookingSerializer` | Ixtiyoriy `reason` |

### `utils.py`

#### `custom_exception_handler(exc, context)`
Xato kodlarini **bitta joyda** belgilaydi:
- `InvalidBookingRequest` → **400** (so'rov noto'g'ri, qayta yuborishning ma'nosi yo'q)
- `BookingError` → **409** (so'rov to'g'ri, lekin hozir bajarib bo'lmaydi — slot band. Klient boshqa vaqt tanlashi kerak. **Bu 400 EMAS**)
- Qolganlari → DRF standarti

> Har bir view ichida `try/except` yozsangiz, ular vaqt o'tib bir-biridan farq qila boshlaydi va mobil ilova chalkashadi.
> ~~⚠️ `settings.py` da `REST_FRAMEWORK["EXCEPTION_HANDLER"]` **ulanmagan** — hozir bu handler ishlamaydi.~~ ✅ Ulandi (409 tekshirilgan).

#### `_parse_dt(raw)`
DRF `DateTimeField` orqali ISO string → `datetime`. `None` → `None`.

---

## 10. `api/payments/` — to'lov va saga (Faza 4)

### `webhook.py` — Payme protokoli

#### `handle_payme_webhook(method, params) → dict`
Metodni handler'ga yo'naltiradi: `CheckPerformTransaction`, `PerformTransaction`, `CancelTransaction`. Noma'lum metod → `PaymentError`.

> **Ikki bosqich bo'lishi sababi:** Payme avval "bu to'lovni qabul qila olasizmi" deb so'raydi (mijozdan pul yechilmasdan **oldin**), keyingina haqiqiy to'lovni amalga oshiradi. Bu ham bir turdagi **SAGA** — tashqi provayder o'zi bizga ikki bosqichli tranzaksiya taklif qilyapti.

#### `_check_perform(params)` — bosqich 1
- `amount = Decimal(params["amount"]) / 100` — **Payme tiyinda yuboradi**
- Payment topilmasa → `-31050`
- **Summa mos kelmasa → `-31001`** (xavfsizlik: hujumchi kam summa bilan bronni ochib olmasin)
- OK → `{"result": {"allow": True}}`

#### `_perform(params)` — bosqich 2, haqiqiy to'lov
> **BU FUNKSIYA IDEMPOTENT BO'LISHI SHART.** Payme tarmoq muammosi bo'lsa **aynan shu so'rovni qayta yuboradi**. Ikkinchi marta kelganda — yangi bron tasdiqlamaymiz, birinchi martadagi natijani qaytaramiz.

1. `select_for_update()` — qatorni qulflaydi (parallel webhook'lardan himoya)
2. Agar `external_id == transaction_id AND status == SUCCEEDED` → **avvalgi javobni qaytaradi, hech narsa qilmaydi**
3. `transaction.atomic()`: Payment `SUCCEEDED` + `booking_services.confirm_booking(booking_id)`

> **Saga'ning "muvaffaqiyat" tarmog'i:** bron o'z holatini o'zi biladi, to'lov uni faqat **chaqiradi**. `Booking` modeli `Payment` haqida bilmaydi — bir tomonlama bog'liqlik.

#### `_cancel(params)` — **saga kompensatsiyasi**
- `REFUNDED` bo'lsa → darhol `{"state": -2}` (idempotent)
- Aks holda: Payment `REFUNDED` + `cancel_booking(...)` → ichida `release_slot` chaqiriladi → **slot bo'shaydi**

### `auth_webhook.py`

#### `verify_payme_signature(request) → bool`
> ⚠️ **BUNI TEKSHIRMASLIK — ENG XAVFLI XATO shu fazada.** Tekshirilmasa, hujumchi shunchaki `PerformTransaction` so'rovini o'zi yuborib, **haqiqiy pul to'lamasdan** bronni tasdiqlatib olishi mumkin.

HTTP Basic Auth: `login=Paycom`, parol = maxfiy kalit.
```python
hmac.compare_digest(key, settings.PAYME_SECRET_KEY)
```
**`==` emas, `compare_digest`** — constant-time solishtirish, **timing attack**dan himoya.

### `bron.py`

#### `create_payment(booking_id, provider) → Payment`
```python
idem_key = f"booking-payment-{booking_id}"
```
> `idempotency_key` sifatida **booking_id** ishlatamiz, tasodifiy emas. Shu tufayli mijoz "to'lash" tugmasini ikki marta bossa ham, bitta bron uchun bitta `Payment` qatori bo'ladi.

**Ikki qatlam himoya:**
1. Avval `filter(idempotency_key=...)` bilan tekshiradi
2. Poyga bo'lsa (ikki so'rov bir vaqtda) — `IntegrityError` ni ushlab, mavjudini qaytaradi

Bu naqsh "check-then-act" race condition'ni to'g'ri hal qiladi: tekshiruv optimizatsiya, **baza constraint'i haqiqiy kafolat**.

#### `build_checkout_url(payment) → str`
Payme yoki Click uchun to'lov havolasi. Noma'lum provayder → `PaymentError`.

### `services.py`
```
PaymentError
└── AlreadyProcessed   # bu to'lov allaqachon yakuniy holatda
```

### `views.py`

#### `PaymeWebhookView`
- `permission_classes = []`, `authentication_classes = []` — **Payme JWT yubormaydi**, o'zining Basic Auth formatidan foydalanadi
- Imzo xato → `-32504`, lekin **HTTP 200**
> ⚠️ **Payme protokoli hatto xatoda ham HTTP 200 kutadi**, xato JSON-RPC `"error"` maydonida ifodalanadi. Bu Payme'ning o'z talabi — boshqa provayderda bunday bo'lmasligi mumkin.
- Javob: `{"jsonrpc": "2.0", "id": ..., **result}`

> ⚠️ Ilgari bu faylda `from requests import Response` turgandi — `requests` kutubxonasining `Response` klassi, DRF'ning javob obyekti emas. U hech qanday xato bermay import bo'lardi va faqat so'rov kelganda yiqilardi. Tuzatildi: `rest_framework.response.Response`.

### `urls.py`
`POST /payments/payme/webhook` → `PaymeWebhookView`. ~~*(hali `api/v1.py` ga ulanmagan)*~~ ✅ Ulandi — to'liq manzil `/api/v1/payment/payments/payme/webhook` (20-bo'lim, 17-band).

---

## 11. `api/events/` — Outbox pattern (Faza 3)

### `services.py`

#### `publish(topic, key, event_type, payload)`
> **MUHIM: bu funksiya Kafka'ga to'g'ridan-to'g'ri yozmaydi.** U faqat `OutboxEvent` qatorini yaratadi.
>
> Shuning uchun bu funksiya **chaqiruvchining tranzaksiyasi ichida** ishlaydi — alohida `transaction.atomic()` **ochmaydi**. Agar ochsa, butun maqsad yo'qoladi: domen yozuvi va hodisa yana ikki xil tranzaksiyaga bo'linib qoladi.

To'g'ri chaqirish:
```python
with transaction.atomic():
    Booking.objects.filter(id=id).update(status=CONFIRMED)
    events.publish(topic="booking.events", ...)
```

### `publisher.py`

#### `OutboxPublisher`
> **KAFOLAT DARAJASI: at-least-once, exactly-once EMAS.** Producer xabarni yubordi, lekin javob kelmasdan jarayon o'lib qolsa, keyingi ishga tushishda o'sha xabar **yana** yuboriladi. Shuning uchun **consumer tomonida idempotentlik majburiy**.

| Metod | Nima qiladi | Muhim tafsilot |
|---|---|---|
| `__init__` | Kafka `Producer` yaratadi | `acks="all"` — **barcha replika tasdiqlagunicha kutamiz** (durability). `retries=3`. `linger.ms=20` — kichik xabarlarni guruhlab yuboradi (throughput) |
| `_kafka_addr()` | `settings.KAFKA_BOOTSTRAP_SERVERS` | Lazy import — Django settings hali yuklanmagan bo'lishi mumkin |
| `run_forever()` | Cheksiz sikl | Har qanday `Exception` ni ushlaydi va **jarayonni yiqitmaydi**. Ish bo'lmasa 1 soniya kutadi (bazani bo'sh so'rov bilan zerikitirmaslik) |
| `_process_batch()` | 100 tagacha `PENDING` hodisani `created_at` bo'yicha oladi | Oxirida `flush(timeout=10)` — **blocking**. `flush()` chaqirmasangiz, jarayon producer navbatini bo'shatmasdan keyingi tsiklga o'tishi mumkin |
| `_publish_one(event)` | Kafka'ga envelope yuboradi | Envelope: `{event_id, event_type, occurred_at, data}`. `key` — partition kaliti (tartib kafolati). `produce()` **asinxron** — natija `_on_delivery` da. `BufferError` → navbat to'la, keyingi tsiklda qayta uriniladi |
| `_on_delivery(event, err, msg)` | Callback | Xatosiz → `PUBLISHED` + `published_at`. Xato → `_mark_failed` |
| `_mark_failed(event, error)` | `attempts + 1` | `attempts >= 10` → `FAILED` (qo'lda tekshirish kerak), aks holda `PENDING` (qayta uriniladi). ⚠️ `FAILED` ga Prometheus alert ulanishi kerak — **hodisa hech kimga yetmagan, jim qolib ketmasligi kerak** |

---

## 12. `api/notifications/` — bildirishnoma

### `circuit.py`

#### `State` (Enum)
`CLOSED` (normal) → `OPEN` (servis o'lgan, umuman urinmaymiz) → `HALF_OPEN` (tiklandimi, bitta so'rov bilan tekshiramiz)

#### `CircuitBreaker`
> **NEGA KERAK:** servis o'lganda har bir so'rov timeout'ni to'liq kutadi. Sekundiga 100 so'rov kelsa, 2 soniyalik timeout bilan 200 so'rov bir vaqtda osilib turadi — **thread'lar tugaydi va siz ham o'lasiz** (cascading failure).
>
> Circuit breaker buni oldini oladi: bir necha xatodan keyin u "ochiladi" va keyingi so'rovlar tarmoqqa umuman chiqmay **darhol** xato qaytaradi.

| Element | Vazifasi |
|---|---|
| `failure_threshold = 5` | 5 xatodan keyin ochiladi |
| `recovery_timeout = 30.0` | 30 soniyadan keyin `HALF_OPEN` ga o'tadi |
| `__post_init__` | `threading.Lock()` yaratadi — **holat bir necha thread'dan o'qiladi/yoziladi** |
| `state` (property) | `OPEN` da vaqt o'tganini tekshirib `HALF_OPEN` ga o'tkazadi. `time.monotonic()` — **tizim vaqti o'zgarsa ham buzilmaydi** |
| `allow()` | `state != OPEN` |
| `record_success()` | `CLOSED`, hisoblagich nolga |
| `record_failure()` | **`HALF_OPEN` da bitta xato yetarli** — darhol qayta yopamiz (sinov so'rovi yiqildi = servis hali tiklanmagan) |

`_breaker = CircuitBreaker()` — modul darajasidagi yagona nusxa.

### `klient.py` — gRPC klient (Faza 2)

#### `_get_stub()`
Kanalni **bir marta** ochamiz va qayta ishlatamiz. Har so'rovda yangi kanal — sekin va TCP ulanishlarini tugatadi. gRPC kanali thread-safe, bitta global nusxa yetarli.

**Double-checked locking:** `if _stub is None` → `with _init_lock` → yana `if _stub is None`. Ikki thread bir vaqtda kirsa ham faqat bittasi yaratadi, va lock har chaqiruvda olinmaydi.

Kanal opsiyalari: `keepalive_time_ms=30_000` (o'lik ulanishni aniqlash), `max_receive_message_length=4MB`.

#### `send(idempotency_key, channel, recipient, template, params, language) → str | None`
> **DIQQAT: BU FUNKSIYA XATO KO'TARMAYDI (fail-open).**
>
> Sabab: SMS yuborilmagani — bronni bekor qilish uchun asos **emas**. Mijoz shifokorga yozildi, puli yechildi, hammasi joyida. SMS ketmagani noqulaylik, falokat emas.
>
> Bu **ataylab** qilingan qaror va u **har bir bog'liqlik uchun alohida** qabul qilinadi. To'lov servisi uchun javob teskari bo'ladi — u yiqilsa, bronni davom ettirish mumkin emas.
>
> Har yangi servis qo'shganingizda o'zingizdan so'rang: *"bu yiqilsa, asosiy ish davom etadimi?"* Javob strategiyani belgilaydi.

Oqim:
1. `_breaker.allow()` `False` → darhol `None`
2. `MAX_ATTEMPTS = 3` marta urinadi, `timeout=TIMEOUT_SECONDS`
3. Muvaffaqiyat → `record_success()` + `message_id`
4. `code not in RETRYABLE` → qayta urinish foydasiz (so'rovning o'zi noto'g'ri). **Breaker'ni ham qo'zg'atmaymiz: aybdor biz, servis emas** — bu muhim nuqta
5. Retryable xato → `record_failure()` + **eksponensial backoff + jitter**:
```python
delay = BASE_BACKOFF * (2 ** (attempt - 1))
time.sleep(delay + random.uniform(0, delay))
```
> **Jitter shart:** usiz barcha klientlar bir vaqtda qayta uriniladi va endigina tiklangan servisni darhol qaytadan yiqitadi. Bu **thundering herd**.

> ~~⚠️ `_breaker` bu faylga import qilinmagan (`circuit.py` da), va `gen.notification.v1` proto fayllari generatsiya qilinmagan.~~ ✅ Ikkalasi ham tuzatildi.

### `services.py`

#### `send_sms(phone, template, params, language)`
> **ATAYLAB XATO KO'TARMAYDI (fail-safe):** agar SMS ketmasa, `SmsLog`ga yozamiz va monitoringga qoldiramiz, lekin **Kafka consumer'ni yiqitmaymiz**. Aks holda bitta yomon telefon raqami butun consumer'ni to'xtatib qo'yadi va undan keyingi barcha SMS'lar navbatda kutib qoladi — **head-of-line blocking**.

1. `render_template` xatosi → `SmsLog(FAILED, error="template_error")` + `return`
2. `SmsProvider().send()` → `SmsLog(SENT, provider_message_id)`
3. Har qanday `Exception` → `SmsLog(FAILED, error)`, log'da `_mask(phone)`

#### `SmsProvider`
> Tashqi SMS API bilan **yagona aloqa nuqtasi**. Provayderni almashtirish kerak bo'lsa (Eskiz → Play Mobile), faqat shu klass o'zgaradi — `send_sms` va undan yuqorisi tegilmaydi.

~~`DEBUG=True` → konsolga log + `"debug-fake-id"`. Aks holda `NotImplementedError`.~~ ✅ `settings.SMS_PROVIDER` bo'yicha `api/notifications/sms.py` dagi klientga yo'naltiradi (20-bo'lim, 13-band).

### `templates.py`
`TEMPLATES` — ikki tilli (uz/ru) shablonlar lug'ati: `booking_confirmed`, `booking_cancelled`, `otp`.

#### `render_template(template, language, params) → str`
- Noma'lum shablon → `ValueError`
- Til topilmasa → `uz` (fallback)
- `KeyError` (parametr kelmagan) → `ValueError`
> Shablonda kutilgan parametr kelmagan — bu **deploy vaqtidagi xato**, jim qolib ketmasligi kerak.

### `consumer.py` — Kafka consumer

#### `run_consumer()`
`booking.events` va `payment.events` ni tinglaydi.

**`enable.auto.commit = False`** — qo'lda commit qilamiz:
> Agar avtomatik commit ishlatsak, Kafka offset'ni xabar **qabul qilinganda** belgilaydi — SMS yuborishdan **oldin**. Ishlov berish davomida jarayon o'lib qolsa, xabar **yo'qoladi**.
>
> Qo'lda commit "kamida bir marta" kafolatini beradi: ishlov berilmaguncha offset siljimaydi, demak eng yomon holatda xabar **takrorlanadi**, lekin hech qachon yo'qolmaydi. Shu tufayli **idempotentlik majburiy**.

`try/finally` → `consumer.close()` — consumer group'dan toza chiqish (rebalance tez bo'ladi).

#### `_process_message(msg)`
Envelope'ni ochadi → `_mark_processing(event_id)` → handler'ga yo'naltiradi. Noma'lum event type → faqat log (yiqilmaydi — **forward compatibility**: yangi hodisa turlari qo'shilsa eski consumer buzilmaydi).

#### `_mark_processing(event_id) → bool`
```python
try:
    ProcessedEvent.objects.create(event_id=event_id)
    return True
except IntegrityError:
    return False
```
> `ProcessedEvent.event_id` **UNIQUE** (PK) — shuning uchun ikki consumer parallel ishga tushsa ham (yoki bir xabar ikki marta kelsa ham), faqat **bittasi** haqiqiy ishni bajaradi.

Bu "insert-first" naqsh: `if exists()` tekshiruvi race condition beradi, `INSERT` + `IntegrityError` bermaydi.

#### `_handle_booking_confirmed(data)` / `_handle_booking_cancelled(data)`
Mos shablon bilan `send_sms` chaqiradi.

#### `_handle_kafka_error(msg)`
`_PARTITION_EOF` → **xato emas** (partitsiya oxiriga yetdik), jim o'tadi. Qolganlari log'ga.

---

## 13. `api/search/` — CQRS + Elasticsearch (Faza 5)

Bu modul **CQRS** (Command Query Responsibility Segregation) ni amalga oshiradi: **yozish** PostgreSQL'da, **o'qish** Elasticsearch'da.

### `index.py`

`DOCTOR_MAPPING` — ES indeks sxemasi:
| Maydon turi | Nega shunday |
|---|---|
| `name_analyzer` (lowercase) | O'zbekcha/ruscha matn: "Юсуббаев" ni "юсуббаев" bilan topish |
| `specializations: keyword` | **Aniq moslik** uchun (filter) — tahlil qilinmaydi |
| `specialization_names: text` | **Matn qidiruvi** uchun — tahlil qilinadi |
| `location: geo_point` | Geo-qidiruvning kaliti. ES buni maxsus indekslaydi (masofa, radius) |
| `is_bookable: boolean` | O'chirish o'rniga flag |

`create_index(es)` — indeks yo'q bo'lsa yaratadi (idempotent).
`DOCTORS_INDEX = "doctors_v1"` — versiyalangan nom, mapping o'zgarganda `v2` yaratib alias almashtiriladi (zero-downtime reindex).

### `projector.py`

#### `run_projector()`
`catalog.events` ni o'qib ES hujjatlarini yangilaydi.
> Bu ham Faza 3 dagi consumer bilan **bir xil naqsh**: qo'lda commit, idempotentlik. ES hujjatini `id` bo'yicha `index` (upsert) qilamiz — shuning uchun bir xil hodisa ikki marta kelsa natija bir xil. **ES'da upsert tabiatan idempotent, `ProcessedEvent` jadvali ham shart emas.**

#### `_project(es, envelope)`
- `DoctorApproved` / `DoctorUpdated` → `es.index()` (upsert)
- `DoctorSuspended` → **o'chirmaymiz**, `is_bookable=False` qilamiz
> Shunda "nega yo'qoldi" degan savolga javob bor, va kerak bo'lsa qayta yoqish oson. `NotFoundError` — jim o'tiladi.

#### `_to_es_doc(data)`
Hodisa payload'ini ES hujjatiga o'giradi. `.get(..., default)` ishlatiladi — hodisa sxemasi evolyutsiyasiga bardoshli.

### `queries.py`

#### `search_doctors(...)`
> Bu funksiya `catalog_services.list_doctors` ni **almashtiradi** — o'qish yo'li endi ES orqali.

**`filter` va `must` ajratilgani muhim:**
| | Xatti-harakat |
|---|---|
| `filter` | **Keshlanadi, ball hisoblamaydi** → tez. Aniq moslik uchun |
| `must` | Relevantlikka **ta'sir qiladi** (`_score`) |

- `filter`: `is_bookable=True` (har doim), `specializations`, `accepts_home_visits`, `rating >= min_rating`, `geo_distance`
- `must`: `multi_match` bilan matn qidiruvi
  - `"fields": ["full_name^3", "specialization_names"]` — **`^3` = ismga 3 baravar ko'proq vazn**
  - `"fuzziness": "AUTO"` — **imlo xatosini kechiradi**: "гастролог" → "гастроэнтеролог"
- `must` bo'sh bo'lsa `match_all` — ES bo'sh `must` bilan kutilgandek ishlamaydi

#### `_build_sort(sort, lat, lon)`
| `sort` | Natija |
|---|---|
| `distance` (+koordinata) | `_geo_distance` asc |
| `rating` | `rating` desc, `reviews_count` desc |
| `price` | `min_price` asc |
| default (`relevance`) | `_score`, keyin `rating` desc — **teng ballda yuqori reytingli yuqorida** |

### `reindex.py`

#### `reindex_all() → int`
PostgreSQL'dan butun ES'ni qayta quradi.
> **Bu funksiya CQRS'ning eng katta afzalligini isbotlaydi: o'qish modeli to'liq QAYTA TIKLANUVCHI, chunki haqiqat manbai boshqa joyda.**

Qachon kerak: ES o'lganda, mapping o'zgarganda, yoki loyihaga qidiruv keyin qo'shilganda.

- `prefetch_related("specializations", "services", "affiliations__clinic")` — N+1 ni oldini oladi
- `.iterator(chunk_size=500)` — **millionlab yozuvni RAM'ga yig'maydi**
- `generate_actions()` generator + `bulk()` — ES helper partiyalab yuboradi

#### `_doctor_to_doc(doctor)`
ORM obyektini ES hujjatiga. `min_price` — faol xizmatlarning eng arzoni. `location` — birinchi faol klinika koordinatasi (soddalashtirilgan).

### `utils.py`
`_es_client()` — `Elasticsearch(settings.ELASTICSEARCH_URL)`. `_kafka_addr()`.

Import yo'llari `api.search.*` — modul to'liq import bo'ladi (Elasticsearch serveri ishlamasa ham, ulanish faqat funksiya chaqirilganda ochiladi).

---

## 14. `api/observability/` — kuzatuv (Faza 7)

### `health.py`

#### `liveness(request)` — `GET /healthz/live`
> **ATAYLAB HECH NARSANI TEKSHIRMAYDI.** Faqat "men javob bera olyapman" degani.
>
> Agar bu yerda bazani tekshirsak, baza bir soniya sekinlashganda Kubernetes **butun podni o'chirib qayta ishga tushiradi** — bu esa faqat ahvolni yomonlashtiradi (qayta ishga tushish sekin, va boshqa podlarga yuk oshadi).

#### `readiness(request)` — `GET /healthz/ready`
> **Tashqi bog'liqliklarni tekshiradi.** Bittasi ishlamasa **503** qaytaradi va Kubernetes bu podga trafik yuborishni to'xtatadi — lekin podni **o'chirmaydi**. Bog'liqlik tiklanganda keyingi probe muvaffaqiyatli bo'ladi va pod avtomatik trafikni qaytaradi.

Tekshiradi: baza (`SELECT 1`) va Kafka. Javob: `{"status": "ready"|"not_ready", "checks": {...}}`.

#### `_check_kafka()` — `producer.list_topics(timeout=2)`
Producer modul darajasida saqlanadi (probe har 10 soniyada ishlaydi). Bo'sh broker ro'yxati ham nosozlik.
> **Kafka nosozligi 503 qaytarmaydi** — javobda `"kafka": "degraded: ..."` bo'lib ko'rinadi, lekin `status` `ready` qoladi. Sabab: API Kafka'ga outbox orqali yozadi, ya'ni unga sinxron bog'liq emas. 503 qaytarilsa, Kafka nosozligi to'liq API uzilishiga aylanardi. Faqat **baza** kritik. (20-bo'lim, 16-band)

> **Liveness vs Readiness farqi** — bu faylning asosiy darsi: *liveness yiqilsa qayta ishga tushirish, readiness yiqilsa trafikni to'xtatish.*

### `metrics.py` — Prometheus

**RED metodikasi:** har bir servis uchun uchta narsani o'lchang — **R**ate (sekundiga necha so'rov), **E**rrors (nechtasi xato), **D**uration (qancha vaqt). Bu uchtasi tizim salomatligini 90% tasvirlaydi.

| Metrika | Turi | Yorliqlar | Nega |
|---|---|---|---|
| `medbron_bookings_created_total` | Counter | `doctor_specialization`, `place` | **Biznes metrikasi** — Grafana'da biznes dashboard quradi |
| `medbron_bookings_failed_total` | Counter | `reason` (`slot_taken`, `payment_failed`, `validation`) | Nima uchun yiqilyapti |
| `medbron_booking_duration_seconds` | Histogram | buckets `[0.05 … 5.0]` | **p95/p99 shu bucketlardan hisoblanadi** |
| `medbron_payment_webhook_total` | Counter | `method`, `result` | **Bu yerda PUL bor** — ayniqsa muhim |
| `medbron_saga_compensations_total` | Counter | — | ⚠️ **Bu metrika o'sib ketsa — ALERT.** Ko'p kompensatsiya = ko'p mijoz to'lay olmayapti = to'lov integratsiyasida muammo |

Fayl oxirida `@booking_duration.time()` dekoratori bilan ishlatish namunasi.

### `tracing.py` — OpenTelemetry

#### `setup_tracing(service_name)`
Django ishga tushganda **bir marta** chaqiriladi (`apps.py → ready()`).
1. `Resource({"service.name": service_name})` — Jaeger'da servis nomi shu
2. `BatchSpanProcessor` + `OTLPSpanExporter` → Jaeger. **Batch** — har spanni alohida yuborish sekin
3. `DjangoInstrumentor().instrument()` + `Psycopg2Instrumentor().instrument()`
> **Avtomatik instrumentatsiya:** har bir HTTP so'rov, DB so'rovi va servislararo chaqiruv **avtomatik** trace'ga tushadi, qo'lda kod yozmasdan.

Izohlarda ikkita muhim tavsiya:
- **Qo'lda span**: `with tracer.start_as_current_span("hold_slot")` — Jaeger'da biznes qadamlari alohida ustun bo'lib ko'rinadi, qaysi biri sekin ekani darrov ko'zga tashlanadi
- **Strukturali log**: loglar JSON bo'lishi va har qatorda `trace_id` bo'lishi kerak. Shunda Jaeger'da sekin so'rovni topib, uning `trace_id` si bo'yicha barcha loglarni bir joyga yig'a olasiz. **Matnli log bilan bu mumkin emas**

---

## 15. `analytics/` — ClickHouse OLAP (Faza 7)

### `schema.py`

#### `fact_booking` (fakt jadvali)
> **Bu jadval PostgreSQL'dagi `Booking`ning nusxasi EMAS.** U ataylab **denormalizatsiya** qilingan — analitikada join qimmat, shuning uchun tez-tez kerak bo'ladigan maydonlar shu yerga tekislab yoziladi.

| Element | Nega |
|---|---|
| `LowCardinality(String)` | Kam xil qiymat (`specialization`, `city`, `place`, `status`) — ClickHouse ularni lug'at sifatida saqlaydi, joy va vaqt tejaydi |
| `is_cancelled`, `is_home_visit` — `UInt8` (0/1) | `sum(is_cancelled)` bilan bekor qilish darajasini **bir o'qishda** hisoblash |
| `ENGINE = MergeTree()` | ClickHouse'ning asosiy dvigateli |
| `PARTITION BY toYYYYMM(created_date)` | Oylar bo'yicha bo'linadi — **eski oylarni arxivlash oson**, so'rov faqat kerakli partitsiyani o'qiydi |
| `ORDER BY (created_date, specialization, city)` | **ClickHouse'ning asosiy indeksi.** Eng ko'p filtrlaydigan maydonlarni shu yerga qo'ying — so'rov tezligini shu belgilaydi |

#### `dim_doctor` (o'lchov jadvali)
`ENGINE = ReplacingMergeTree() ORDER BY doctor_id` — **bir xil kalitli yangi qator eskisini almashtiradi**. Shifokor ma'lumoti yangilanganda ishlatiladi.

### `consumer.py`

#### `run_analytics_consumer()`
> **OLTP consumer'dan muhim farq:** bu yerda xabarlarni **bittalab emas, partiyalab** yozamiz. ClickHouse bitta `INSERT`ga 1 qator yozishni yomon ko'radi — u minglab qatorni birdan yozishga optimallashgan. Har xabarda bitta `INSERT` qilsangiz, **ClickHouse tiz cho'kadi**.

Naqsh: buferga yig'amiz, `BATCH_SIZE = 1000` to'lganda **yoki** `FLUSH_INTERVAL_SECONDS = 5` o'tganda birdan yozamiz.
> Vaqt sharti nima uchun: kam trafikda bufer 1000 ga hech qachon yetmasligi mumkin va ma'lumot abadiy kutib qolardi.

`_flush()` dan **keyin** `consumer.commit()` — offset faqat yozilgandan keyin siljiydi. `finally` da qolgan buferni yozadi.

#### `_to_fact_row(envelope) → dict | None`
Hodisani analitik qatorga o'giradi va **denormalizatsiya qiladi**.
> E'tibor bering: `specialization`, `city`, `patient_age_group` kabi maydonlarni **hodisa ichidan** olamiz — join qilmaymiz. Shuning uchun `booking.events` hodisasi bu maydonlarni **o'z ichiga olishi kerak** (`events.publish` payload'iga qo'shiladi).

`EVENT_STATUS` map'i orqali `BookingConfirmed` → `confirmed`, `BookingCancelled` → `cancelled`, `BookingExpired` → `expired`; noma'lum turga `None` (kelajakda yangi hodisa qo'shilsa consumer yiqilmaydi). `is_cancelled` **faqat** `cancelled` da 1 — `expired` bekor qilish darajasiga qo'shilmaydi.

#### `_flush(rows)`
`client.insert("fact_booking", rows)` — bitta `INSERT`.

### `queries.py`
> Bu so'rovlar millionlab qatorni skanerlaydi, lekin ClickHouse'da **millisekundlarda** ishlaydi — chunki ustunli saqlash va `LowCardinality`. **Xuddi shu so'rovlarni PostgreSQL'da ishlatsangiz, sekundlar ketardi va production bazasini bloklardi.**

| So'rov | Nima ko'rsatadi |
|---|---|
| `cancellation_rate_by_specialization` | Bekor qilish darajasi mutaxassislik kesimida. `saga_compensations` metrikasi "nechta" desa, bu **"qaysi sohada"** deydi |
| `daily_revenue` | Kunlik daromad (faqat `confirmed`) |
| `home_vs_clinic_by_city` | Uy chaqiruvi vs klinika, hudud kesimida |
| `conversion_funnel` | Haftalik `created`/`confirmed`/`cancelled`/`expired` + `conversion_pct` — **to'lov integratsiyasi qanchalik yaxshi ishlayotganini ko'rsatadi**. `created` (= `pending_payment` qatorlari) maxraj vazifasini bajaradi; `nullIf(..., 0)` nolga bo'linishdan himoya qiladi |

> ⚠️ **Bitta bron = bir necha qator** (`pending_payment` → `confirmed`/`cancelled`/`expired`). Filtrsiz `count()` bronlar sonini emas, **hodisalar** sonini beradi — shuning uchun `cancellation_rate_by_specialization` yakuniy holatlar bo'yicha, `home_vs_clinic_by_city` esa `confirmed` bo'yicha filtrlaydi.

### `utils.py`
`_clickhouse_client()`, `_kafka_addr()` — lazy import + settings.

> ℹ️ Bu fayl aslida Python (`SCHEMA = """..."""`) — avval `schema.sql` deb nomlangan edi, `schema.py` ga o'zgartirildi.

---

## 16. `apps/loadtest/slots.js` — k6 yuk testi (Faza 6)

`GET /api/v1/schedule/doctors/{id}/slots` — eng og'ir endpointni sinaydi. **Parametrsiz** chaqiradi, ya'ni keshlanadigan "bugun" yo'liga tushadi (8.5-bo'limga qarang) — `date_from`/`date_to` qo'shsangiz oraliq rejimiga o'tasiz va Faza 6 ning butun maqsadini o'tkazib yuborasiz.

#### `options.stages`
```
30s → 50 VU   (isinish)
1m  → 200 VU  (normal yuk)
1m  → 500 VU  (yuqori yuk)
2m  → 1000 VU (pik)
1m  → 0       (sovish)
```
> **Bosqichma-bosqich yuklama** — real trafik ham shunday o'sadi, birdan emas. Bir zumda 10000 VU tashlash noreal va faqat tarmoqni buzadi.

#### `options.thresholds` — **bu sizning SLA'ingiz**
```js
http_req_duration: ["p(95)<100", "p(99)<250"]
errors: ["rate<0.01"]
```
> **p95 tanlangani muhim:** o'rtacha (mean) **yolg'onchi ko'rsatkich** — u sekin so'rovlarni yashiradi. `p95 = "so'rovlarning 95% shu vaqtdan tez"`.

#### `DOCTOR_IDS`
> **Bitta ID'ni urg'ochilamang** — real hayotda so'rovlar turli shifokorlarga taqsimlanadi, va bu **keshlash xatti-harakatiga jiddiy ta'sir qiladi**. Bitta ID bilan 100% cache hit chiqadi va test yolg'on natija beradi.

#### `default function()`
Tasodifiy shifokor ID tanlaydi → `GET` → `check()` (status 200, javob bo'sh emas, `<250ms`) → custom metrikalarga yozadi → `sleep(Math.random() * 2)`.
> **Real foydalanuvchi so'rovlar orasida o'ylaydi.** `sleep` siz uzluksiz otsangiz, bu realdan ko'ra og'irroq sun'iy yuk bo'ladi.

---

## 17. `k8s/booking-service.yaml` — Kubernetes (Faza 7)

To'rtta obyekt: **Deployment**, **Service**, **HPA**, va **probe'lar**.

### 1. Deployment
| Sozlama | Qiymat | Nega |
|---|---|---|
| `replicas: 3` | 3 nusxa | Biri o'lsa qolgan 2 tasi trafikni ko'taradi |
| `strategy.rollingUpdate` | `maxUnavailable: 0`, `maxSurge: 1` | **ZERO-DOWNTIME DEPLOY.** `maxUnavailable: 0` → deploy paytida birorta ham eski pod o'chmaydi, yangi pod **tayyor** bo'lgunga qadar. `maxSurge: 1` → bir vaqtda faqat bitta qo'shimcha pod |
| `image: .../booking:${GIT_SHA}` | commit sha | **`latest` EMAS** — `latest` bilan qaysi kod ishlayotganini bilib bo'lmaydi va rollback imkonsiz |
| `resources.requests` | cpu 250m, mem 256Mi | Kafolatlangan minimum — scheduler shu asosda joylashtiradi. **HPA foizni aynan `requests`ga nisbatan hisoblaydi** |
| `resources.limits` | cpu 1000m, mem 512Mi | Qat'iy shift. **Bularsiz bitta buzuq pod butun node'ni yeb qo'yishi va qo'shni servislarni o'ldirishi mumkin** |
| `envFrom` | `secretRef` + `configMapRef` | **DB parol, JWT kalit — Secret'da, YAML'da EMAS** |

### Probe'lar
| Probe | Yo'l | Yiqilsa nima bo'ladi |
|---|---|---|
| `livenessProbe` | `/healthz/live` | Kubernetes podni **o'chirib qayta ishga tushiradi**. Shuning uchun tekshiruv **yengil**: tashqi bog'liqliklarni tekshirmaydi. Aks holda Kafka bir soniya uzilsa, Kubernetes sog'lom podlaringizni **qirib tashlaydi** |
| `readinessProbe` | `/healthz/ready` | Podni **o'chirmaydi**, faqat trafik yubormaydi. DB + Kafka tekshiradi. Ulanish tiklanganda pod avtomatik trafikni qaytaradi — **qayta ishga tushishsiz** |
| `startupProbe` | `/healthz/live`, `failureThreshold: 30 × 2s = 60s` | Sekin ishga tushadigan ilova uchun. **U tugamaguncha liveness ishlamaydi** — Django migratsiya 30 soniya olsa, liveness uni bexosdan o'ldirmasligi uchun |

### 2. Service
`type: ClusterIP`, `port 80 → targetPort 8000`, selector `app: booking-service`.
> Bu — Faza 2 dagi **"service discovery"** muammosining Kubernetes yechimi. Boshqa servislar podlarning o'zgaruvchan IP'sini bilishi shart emas — ular shunchaki `http://booking-service` deb murojaat qiladi, Kubernetes DNS uni tirik podlarga yo'naltiradi.

### 3. HPA (HorizontalPodAutoscaler)
`minReplicas: 3`, `maxReplicas: 20`, `cpu averageUtilization: 70`.

**`behavior` dagi assimetriya ataylab:**
| | Qiymat | Nega |
|---|---|---|
| `scaleUp.stabilizationWindowSeconds` | 30 | **Tez ko'payadi** — mijoz kutmasin |
| `scaleDown.stabilizationWindowSeconds` | 300 | **Sekin kamayadi** — endigina kamaytirib, yana ko'paytirishga tushib qolmaslik uchun (**flapping**) |

> ⚠️ HPA ishlashi uchun klasterda `metrics-server` o'rnatilgan va `resources.requests` belgilangan bo'lishi **shart**.

---

## 18. Testlar

### `apps/booking/tests.py` — concurrency testlari
> Bu shunchaki test emas — **loyihaning eng qimmatli hujjati**. Intervyuda "double booking'ni qanday oldini oldingiz?" degan savolga siz gap bilan emas, **shu fayl bilan** javob berasiz.

| Test | Nimani isbotlaydi |
|---|---|
| `make_slot()` (helper) | Slot yaratadi. **Soniya/mikrosoniyani tozalaydi** — aks holda testlar beqaror bo'ladi |
| `test_bir_shifokorga_bir_vaqtda_ikki_slot_yaratib_bolmaydi` | `UNIQUE constraint` ishlayotganini. **Ikkinchi slot boshqa klinikaga tegishli, lekin baribir yaratilmaydi** — aynan shu shifokor bir vaqtda ikki joyda bo'lolmasligining kafolati |
| `test_tugash_vaqti_boshlanishdan_keyin_bolishi_shart` | `CheckConstraint(end_at > start_at)` |
| **`test_yuz_parallel_sorovdan_faqat_bittasi_band_qiladi`** | **ENG MUHIM TEST.** 100 thread bir vaqtda bitta slotni band qilmoqchi → aynan **bittasi** muvaffaqiyatli |
| `test_muddati_otgan_hold_avtomatik_boshaydi` | Mijoz to'lov oynasini yopib ketsa slot abadiy band qolmasligini |
| `test_hold_dan_keyin_tasdiqlash` | `HELD → BOOKED` |
| `test_boshashtirish_idempotent` | `release_slot` ikki marta chaqirilsa ham xato bermasligini. **Faza 4 da hayotiy: Kafka xabari takrorlanishi normal hol** |
| `test_bosh_bolmagan_slotni_band_qilib_bolmaydi` | `BLOCKED` slotni band qilib bo'lmasligini |
| `test_uy_chaqiruvi_qoshni_slotlarni_yopadi` | ±30 daqiqadagi qo'shnilar `BLOCKED`, 3 soatdagi `FREE` qoladi |
| `test_uy_chaqiruvi_bekor_qilinsa_qoshnilar_boshaydi` | Kompensatsiya qo'shnilarni ham qaytarishini |
| `test_klinikadagi_qabul_bufer_talab_qilmaydi` | **Klinikada shifokor joyidan qimirlamaydi** — yo'l vaqti kerak emas |

**Asosiy testdagi ikki nozik nuqta:**
1. `@pytest.mark.django_db(transaction=True)` **majburiy** — oddiy `django_db` har testni bitta tranzaksiyaga o'raydi va thread'lar bir-birining ma'lumotini ko'rmaydi, ya'ni **test yolg'on yashil bo'ladi**
2. `threading.Barrier(100)` — barcha thread'lar bir vaqtda start olishi uchun. Usiz thread'lar ketma-ket ishlab ketadi va **race condition umuman yuzaga kelmaydi**
3. `finally: connections.close_all()` — har thread o'z DB ulanishini yopishi shart

### `apps/payment/tests.py` — saga testlari
| Test | Nimani isbotlaydi |
|---|---|
| `test_tolov_muvaffaqiyatli_bolsa_hammasi_tasdiqlanadi` | Baxtli yo'l: `PerformTransaction` → Booking `CONFIRMED`, Slot `BOOKED` |
| `test_tolov_bekor_bolsa_kompensatsiya_ishlaydi` | **Asosiy SAGA testi:** `CancelTransaction` → Booking `CANCELLED`, Slot `FREE`. "Slot abadiy band bo'lib qolmasligi kerak" |
| `test_perform_ikki_marta_kelsa_...` | **IDEMPOTENTLIK:** Payme `PerformTransaction`ni qayta yuborsa (rasmiy protokolda kutiladigan holat), bron ikkinchi marta confirm qilinmaydi |
| `test_summa_mos_kelmasa_perform_rad_etiladi` | **Xavfsizlik:** hujumchi kam summa bilan yuborsa qabul qilinmaydi |
| `test_bekor_qilingan_tolovni_ikkinchi_marta_...` | `CancelTransaction` ham idempotent |

**Fixture'lar** `conftest.py` da (loyiha ildizida): `clinic`, `specialization`, `doctor` (`APPROVED` holatda), `service`, `client_user`, `patient`, `pending_booking`.

`pending_booking` — `Booking=PENDING_PAYMENT` + `Slot=HELD` + `Payment=CREATED`. Slot **HELD bo'lishi shart**: `confirm_slot` compare-and-set bilan `HELD → BOOKED` qiladi, `FREE` slotda `SlotNotAvailable` ko'taradi. Ya'ni fixture'dagi "HELD" dekoratsiya emas, testning ishlashi uchun zarur shart.

**Holat:** PostgreSQL'da **20/20 o'tdi**, SQLite'da 19/20 (20-bo'lim, 1-band).

```bash
POSTGRES_DB=medbron POSTGRES_USER=postgres POSTGRES_PASSWORD=... pytest   # 20/20
pytest                                                                    # 19/20
```

---

## 19. Loyihaning asosiy naqshlari (xulosa)

| Naqsh | Qayerda | Muammoni hal qiladi |
|---|---|---|
| **Compare-and-set** | `schedule/services.hold_slot` | Race condition — 100 parallel so'rovdan bittasi o'tadi |
| **DB constraint = invariant** | `TimeSlot.uniq_doctor_slot_start` | Kod xato qilishi mumkin, baza yo'q |
| **Idempotentlik** | `hold_slot`, `release_slot`, `confirm_booking`, `_perform`, `create_payment`, `generate_slots`, `ProcessedEvent` | Takroriy so'rov/xabar zarar yetkazmaydi |
| **Transactional Outbox** | `OutboxEvent` + `OutboxPublisher` | Baza yozildi lekin Kafka yiqildi → hodisa yo'qolmaydi |
| **Saga + kompensatsiya** | `create_booking` → `cancel_booking` → `release_slot` | Taqsimlangan tranzaksiya (ACID yo'q joyda) |
| **Circuit breaker** | `notifications/circuit.py` | Cascading failure — o'lgan servis sizni ham o'ldirmasin |
| **Retry + jitter** | `notifications/klient.py` | Thundering herd |
| **Fail-open vs fail-closed** | SMS → fail-open; to'lov → fail-closed | "Bu yiqilsa, asosiy ish davom etadimi?" |
| **DTO (modul chegarasi)** | `catalog/services.py` | Yashirin bog'liqlik — servisga ajratganda to'satdan yiqilmaslik |
| **Snapshot** | `BookingItem.price/name/duration` | Narx o'zgarsa eski bronlar buzilmaydi |
| **Cache-aside + event invalidation** | `schedule/cache.py` | Kesh eskirishi |
| **CQRS** | `search/*` (ES) vs PostgreSQL | Murakkab qidiruv + qayta tiklanuvchi o'qish modeli |
| **Denormalizatsiya (OLAP)** | `analytics/schema.py` | Analitik so'rov prod bazasini bloklamasin |
| **Liveness ≠ Readiness** | `observability/health.py` | Sekin baza tufayli sog'lom podlar o'lmasin |
| **IDOR himoyasi** | `get_queryset` da `filter(owner=request.user)` | Boshqa mijozning ma'lumoti ko'rinmasin |
| **Ma'lumotni minimallashtirish** | `_mask(phone)`, `SmsLog` kontentni saqlamaydi, OTP xeshlanadi | Log/baza sizib chiqsa zarar kam |

---

## 20. Ma'lum kamchiliklar ro'yxati (tuzatish uchun)

### ✅ Tuzatilgan
- **`settings.py`** — `AUTH_USER_MODEL`, `REST_FRAMEWORK` (`EXCEPTION_HANDLER`, `DEFAULT_THROTTLE_RATES`, `DEFAULT_AUTHENTICATION_CLASSES`, `DEFAULT_PERMISSION_CLASSES`, `DEFAULT_THROTTLE_CLASSES`), `SIMPLE_JWT`, `CACHES`, `KAFKA_BOOTSTRAP_SERVERS`, `NOTIFICATION_GRPC_ADDR`, `ELASTICSEARCH_URL`, `CLICKHOUSE_HOST`, `PAYME_SECRET_KEY`, `PAYME_MERCHANT_ID`, `CLICK_MERCHANT_ID` qo'shildi; `rest_framework` `INSTALLED_APPS` da; `env()` yordamchisi bilan hammasi muhit o'zgaruvchisidan
- **`apps/booking/apps.py`** — `observability.tracing` → `api.observability.tracing`
- **`api/observability/tracing.py`** — `psycopg2` instrumentatsiyasi `try/except ImportError` ichiga olindi (SQLite'da paket yo'q → `ready()` yiqilib butun Django ishga tushmayotgan edi)
- **`apps/account/models.py`** — takroriy `User` klassi olib tashlandi; tartib `UserManager → User → Patient → Address → OtpCode` ga keltirildi, ya'ni `Patient.owner` va `Address.user` FK'lari endi **aniq** to'g'ri modelga ishora qiladi (ilgari birinchi, keyin bekor qilingan klassga ishora qilardi va tasodifan ishlab turgandi)
- **Migratsiyalar** generatsiya qilindi va qo'llandi:
  - `account/0002_...` — `User`ga `password`, `last_login`, `is_staff`, `is_superuser`, `groups`, `user_permissions`; `full_name`/`phone` yangilandi; `OtpCode` yaratildi
  - `notifications/0001_initial` — `SmsLog`, `ProcessedEvent`
  - `utils/0001_initial` — `OutboxEvent`

  `password` uchun bir martalik `default=''` berildi (`preserve_default=False` — modelga singib qolmaydi). Domen jadvallari bo'sh edi, ma'lumot yo'qolmadi; `db.sqlite3.backup-*` nusxasi saqlangan.

- **JWT ishga tushirildi** — `djangorestframework-simplejwt` 5.5.1 o'rnatilgan, `api/account/services.py` ga `from rest_framework_simplejwt.tokens import RefreshToken` qo'shildi, `token_blacklist` ning 12 migratsiyasi qo'llandi (`BLACKLIST_AFTER_ROTATION=True` shu jadvallarni talab qiladi)
- **`Booking.idempotency_key`** maydoni qo'shildi (`unique=True, null=True`) + `booking/0002_booking_idempotency_key` migratsiyasi. Bu bron oqimining asosiy yo'lini blokdan chiqardi
- **Import yo'llari** — 14 ta noto'g'ri import tuzatildi: `api/notifications/services.py`, `api/payments/views.py` (+`from requests import Response` → `rest_framework.response.Response`), `api/payments/webhook.py`, `api/search/{index,queries,projector,reindex}.py`, `apps/booking/tests.py`, `api/schedule/cache.py` izohidagi namuna
- **`api/notifications/klient.py`** — `_breaker` `circuit.py` dan import qilindi; `from conf.settings import ...` → `django.conf.settings` (sozlama modulini to'g'ridan-to'g'ri import qilish `override_settings` va env override'ni chetlab o'tadi)
- **gRPC stub'lar generatsiya qilindi** — `proto/gen/notification/v1/notification.proto` yozildi (kontrakt `klient.py` dagi ishlatilishdan aniqlandi) va `gen/notification/v1/` ga kompilyatsiya qilindi. `Channel` enum'da `CHANNEL_UNSPECIFIED = 0` — proto3 da 0 default qiymat, agar `CHANNEL_SMS = 0` bo'lsa maydonni to'ldirmagan klient bexosdan SMS yuborib qo'yardi
- **`conftest.py` + `pytest.ini`** yozildi — `clinic`, `specialization`, `doctor`, `service`, `client_user`, `patient`, `pending_booking` fixture'lari. `pytest-django` o'rnatildi
- **`DATABASES` env orqali almashtiriladigan qilindi** (`POSTGRES_DB` berilsa PostgreSQL) + `psycopg2-binary` o'rnatildi. Shu bilan **100-thread concurrency testi ishga tushdi va o'tdi** — loyihaning asosiy invarianti endi isbotlangan
- **`OTEL_EXPORTER_OTLP_ENDPOINT`** sozlamasi qo'shildi; manzil berilmasa tracing yoqilmaydi. Ilgari `jaeger-collector:4317` kodda qattiq yozilgan bo'lib, testlarda stderr'ni uzluksiz ogohlantirish bilan to'ldirardi
- **`api/observability/urls.py`** yaratildi va `conf/urls.py` da `healthz/` prefiksi bilan ulandi — `k8s/booking-service.yaml` dagi probe'lar aynan `/healthz/live` va `/healthz/ready` ga murojaat qiladi. Tekshirildi: ikkalasi ham **200**
- **`events.publish` ULANDI** — `confirm_booking` va `cancel_booking` ichida, **o'z tranzaksiyalari ichida** (Outbox pattern buni talab qiladi). Partition kaliti = `booking_id`, ya'ni bitta bronning hodisalari tartibda o'qiladi. Payload **denormalizatsiya qilingan**: `client_phone`, `booking_number` (bildirishnoma uchun) + `specialization`, `place`, `city`, `total_price`, `patient_age_group`, `patient_gender` (analitika uchun)
- **Yordamchi funksiyalar** modul chegarasini buzmasdan qo'shildi: `catalog_services.get_service_context()`, `catalog_services.get_clinic_city()`, `schedule_services.get_slot_clinic_id()` — booking moduli `catalog`/`schedule` modellarini baribir import qilmaydi
- **`apps/booking/test_events.py`** — 5 ta yangi test: hodisa outbox'ga tushishi, partition kaliti, hodisalar tartibi, idempotentlik (qayta tasdiqlash ikkinchi hodisa yozmaydi), to'lov kompensatsiyasi, va payload'ni `analytics._to_fact_row` haqiqatan qabul qilishi

**Import tekshiruvi:** ilgari umuman import bo'lmaydigan 21 modul — `api.payments.*`, `api.search.*`, `api.notifications.*`, `analytics.*`, `api.events.*`, `api.observability.*` — endi **hammasi import bo'ladi**.

Natija: `manage.py check` → **0 issues**, `makemigrations --check` → **No changes detected**.

**Uchidan-uchiga tekshirildi:**

| Sinov | Natija |
|---|---|
| `create_user` / `create_superuser` | Parolsiz / parolli, `platform_admin`, `check_password` o'tdi |
| `request_otp` → `verify_otp` → `issue_tokens` | Kod xeshlangan holda saqlanadi; `is_new=True`, `is_phone_verified=True`; access+refresh chiqdi |
| Token tarkibi | `access.user_id` to'g'ri UUID, `exp` bor; `refresh["role"] == "client"` (gateway shu claim'ga qaraydi) |
| Noto'g'ri kod | `InvalidCode`, `attempts` oshadi |
| `GET /api/v1/patient/patients/` tokensiz | **401** |
| Bearer token bilan | **200**, `POST` → **201**, keyingi `GET` → 1 bemor (`owner` serverda qo'yiladi) |
| `GET /api/v1/catalog/specializations` | **200** — ochiq endpoint `permission_classes = []` bilan ishlaydi |
| Buzuq token | **401** |
| `POST /bookings` + `Idempotency-Key` | **201** `MB-260730-A9L6` |
| Aynan shu kalit bilan takror | **200**, **bir xil bron**, bazada 1 qator, slot `held` |
| Band slotga boshqa kalit bilan | **409** `"Bu vaqt allaqachon band qilingan"` — `EXCEPTION_HANDLER` ishlayapti |
| Kalitsiz so'rov | **201**, `idempotency_key=None` (bir necha `NULL` unique'ni buzmaydi) |
| Kalitsiz takror (o'sha slot) | **409** — slot hold'i kalitsiz ham himoya qiladi |

**Test paketi:** PostgreSQL'da **20/20 o'tdi** (100-thread concurrency testi ham). SQLite'da 19/20 — sabab SQLite'ning bitta yozuvchi cheklovi, kod emas (20-bo'lim, 1-band).

### Test bazasi
1. **`test_yuz_parallel_sorovdan_faqat_bittasi_band_qiladi` faqat PostgreSQL'da o'tadi.**

   | Baza | Natija |
   |---|---|
   | PostgreSQL 17 | **20/20 o'tdi** — concurrency testida 1 muvaffaqiyat, 99 "band", 0 xato |
   | SQLite | **19/20** — ~9/100 thread `OperationalError: database table is locked` |

   SQLite bitta yozuvchiga mo'ljallangan, 100 tomonli parallel yozishni ko'tarmaydi. **Kod xatosi emas:** SQLite'da ham `ok=1, band=90, lock=9`, slot `held` — ya'ni invariant saqlanadi, compare-and-set to'g'ri ishlaydi. Faqat testning `assert errors == []` sharti bajarilmaydi.

   Testning o'z docstring'ida "lokal kompyuterda hech qachon ko'rinmaydi, prodda ko'rinadi" deb yozilgan — aynan shu holat.

   **Test zaiflashtirilmadi.** `except OperationalError: pass` qo'shish uni ma'nosiz qilardi. SQLite'dagi yiqilish ataylab **ko'rinadigan** qoldirildi: `skipif` qo'yilsa, default `pytest` yugurishi "yashil" ko'rinardi, lekin loyihaning eng muhim testi hech qachon ishlamagan bo'lardi — bu yolg'on ishonch. Yiqilish esa "siz noto'g'ri bazada test qilyapsiz" deb aytib turadi.



### Xavfsizlik (prodga chiqishdan oldin)
10. ~~`DEBUG = True`, `SECRET_KEY` kodda ochiq, `ALLOWED_HOSTS = []`~~ ✅ Endi `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` muhitdan. Lokalda hech narsa berish shart emas (default `DJANGO_DEBUG=1`). `DJANGO_DEBUG=0` da `SECURE_PROXY_SSL_HEADER` va secure cookie'lar yoqiladi
11. ~~Prodda `POSTGRES_DB` + `POSTGRES_PASSWORD` env berilishi shart — berilmasa jimgina SQLite'ga tushib qoladi~~ ✅ `_require_in_prod()`: `DJANGO_DEBUG=0` bo'lsa `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `POSTGRES_DB`, `REDIS_URL`, `PAYME_SECRET_KEY` dan birortasi yo'q bo'lsa Django **ishga tushmaydi** (`ImproperlyConfigured`, yetishmaganlar ro'yxati bilan). Test: `apps/catalog/tests.py`
12. ~~`LocMemCache` → Redis (`REDIS_URL` bering) — aks holda ko'p podda kesh invalidatsiyasi ishlamaydi~~ ✅ Prodda `REDIS_URL` majburiy (11-band)
13. ~~`_send_sms` va `SmsProvider.send` — haqiqiy provayder ulanmagan~~ ✅ **Eskiz.uz ulandi** (`api/notifications/sms.py`):
   - `SMS_PROVIDER`: `console` (faqat log, DEBUG da default) yoki `eskiz` (prodda default). Prodda `console` bo'lsa Django ishga tushmaydi
   - Env: `ESKIZ_EMAIL`, `ESKIZ_PASSWORD` (majburiy), `ESKIZ_FROM` (default `4546`), `ESKIZ_BASE_URL`
   - Token (~30 kun) Django keshida — prodda Redis, barcha podlar bitta tokenni ishlatadi. 401 kelsa bir marta qayta login
   - Bildirishnoma SMS (`send_sms`) — avvalgidek **fail-open**: xato `SmsLog(FAILED)` ga yoziladi, consumer yiqilmaydi
   - OTP SMS — **fail-closed**: yuborilmasa **HTTP 503** (`SmsUnavailable`), "Kod yuborildi" deb yolg'on aytilmaydi; `OtpCode` yozuvi o'chiriladi, ya'ni yetmagan SMS cooldown/soatlik limitni sarflamaydi. Matn `templates.py` dagi `otp` shablonidan
   - Yangi provayder (Play Mobile) = shu faylda `send(phone, text) -> str` klassi + `get_sms_client()` ga bitta qator
   - ⚠️ **Deploydan oldin:** Eskiz kabinetida `templates.py` dagi barcha matnlarni moderatsiyadan o'tkazish kerak — tasdiqlanmagan matn rad etiladi
   - Testlar: `apps/notifications/tests.py` (5 ta, tarmoq mock). Jami PostgreSQL'da **44 passed**

### Nozik mantiqiy nuqtalar
15. ~~**`create_booking` hodisa chiqarmaydi**~~ — **✅ tuzatildi.** Endi `BookingCreated` chiqadi (payload `status="pending_payment"`), outbox'ga xuddi shu tranzaksiyada, `bulk_create` dan **keyin** (payload item'lardan xizmat kontekstini oladi).

   **Bu voronkaning MAXRAJI.** Usiz faqat tugagan bronlar sanalardi va "boshlandi, lekin to'lanmadi" statistikada umuman yo'q edi — ya'ni konversiyani hisoblab bo'lmasdi. `conversion_funnel` ga `created` ustuni va `conversion_pct` qo'shildi.

   > ⚠️ **Yon ta'sir — e'tibordan qochmasin.** Endi bitta bron `fact_booking` ga **bir necha qator** yozadi (`pending_payment`, keyin `confirmed`/`cancelled`/`expired`). Ya'ni filtrsiz `count()` bronlar sonini emas, **hodisalar** sonini beradi. Shu sababli ikki so'rov tuzatildi:
   > * `cancellation_rate_by_specialization` — `status IN ('confirmed','cancelled','expired')` qo'shildi; usiz maxraj ikki barobar oshib, bekor qilish darajasi **ikki barobar past** ko'rinardi
   > * `home_vs_clinic_by_city` — `status = 'confirmed'` qo'shildi; usiz bitta bron uch marta sanalardi
   >
   > `daily_revenue` allaqachon `status = 'confirmed'` bo'yicha filtrlaydi — tegilmadi.

   SMS chiqmaydi: `notifications/consumer.py` da `IGNORED_EVENTS` ro'yxati bor. Bu `handlers.get() → None` ga tashlab qo'yilmadi — aks holda har bron uchun "Noma'lum hodisa turi" ogohlantirishi yozilib, haqiqiy noma'lum hodisa shovqin ichida ko'rinmay qolardi. Filtr `_mark_processing` dan **oldin** turadi, ya'ni `ProcessedEvent` jadvali bekorga o'smaydi.

16. ~~**`readiness` Kafka haqida yolg'on gapiradi**~~ — **✅ tuzatildi.** `_check_kafka()` endi haqiqatan `producer.list_topics(timeout=2)` chaqiradi. Producer modul darajasida saqlanadi (probe har 10 soniyada ishlaydi — har safar yangi TCP ulanish qimmat), va bo'sh broker ro'yxati ham nosozlik hisoblanadi (DNS ishlaydi, broker yo'q holati).

   **Lekin Kafka nosozligi 503 QAYTARMAYDI — bu ataylab.** API Kafka'ga to'g'ridan-to'g'ri yozmaydi: u `OutboxEvent` qatorini yozadi, Kafka'ga esa alohida `run_outbox_publisher` yetkazadi. Kafka o'lganda bron, to'lov va qidiruv bemalol ishlayveradi; hodisalar outbox'da navbatda turadi. Agar readiness 503 qaytarsa, Kubernetes **barcha** podlarni trafikdan chiqarardi va Kafka nosozligi to'liq API uzilishiga aylanardi — tekshiruv o'zi avariyani kengaytirardi. Shuning uchun javobda `"kafka": "degraded: ..."` ko'rinadi (monitoring ushlaydi), `status` esa `ready` qoladi. Faqat **baza** kritik.

   > Yo'l-yo'lakay `k8s/booking-service.yaml` da `readinessProbe.timeoutSeconds: 5` qo'shildi. Bu **majburiy** edi: Kubernetes default'i 1 soniya, Kafka metadata so'rovi esa 2 soniyagacha kutishi mumkin — ya'ni hammasi joyida bo'lsa ham probe timeout bo'yicha yiqilib, pod trafikdan chiqib ketardi.
17. ~~**`/api/v1/payment/payments/payme/webhook`** — manzilda takror bor~~ ✅ Yangi manzil: **`POST /api/v1/payment/payme/webhook`** (Payme kabinetiga shu kiritiladi)
18. ~~**`/api/v1/observability/live`** ham ochiq qolgan~~ ✅ `api/v1.py` dan olib tashlandi — probe'lar faqat `/healthz/live`, `/healthz/ready`
20. ~~`booking/services.py` `schedule_services.TimeSlot` ga to'g'ridan-to'g'ri murojaat qiladi~~ ✅ `schedule_services.expired_hold_slot_ids()` qo'shildi; `expire_pending_bookings` va `release_expired_holds` ikkalasi shundan foydalanadi
21. ~~`_block_travel_buffer` alohida `transaction.atomic()` ochadi~~ ✅ Haqiqiy muammo shu edi: tranzaksiyadan tashqarida chaqirilganda slot HELD bo'lib, bufer yopilishi alohida tranzaksiyada ketardi — bufer yiqilsa qo'shnilar ochiq qolardi. Endi `hold_slot` UPDATE + bufer **bitta** `atomic()` ichida, `_block_travel_buffer` o'zi tranzaksiya ochmaydi
   > ℹ️ Yon ta'sir: SQLite'da 100-thread testi endi ko'proq `database table is locked` beradi (tranzaksiya uzunroq). PostgreSQL'da **39/39 o'tadi** — bu yana SQLite cheklovi (1-band)
22. ~~`DoctorDetailView` `DoctorListSerializer` ishlatadi~~ ✅ `DoctorDetailSerializer` + `catalog_services.get_doctor_detail()` → `DoctorDetailDTO`: ro'yxat maydonlari + `bio`, `license_number`, `home_visit_radius_km`, `clinics` (faol affiliatsiya va faol klinikalar). Ro'yxat javobi yengilligicha qoldi (testlangan)

### Qo'shimcha qolgan ishlar (2026-09-14 tekshiruvi)
23. ~~**Davriy ishlar ishga tushirilmaydi** — `generate_slots`, `release_expired_holds`, `expire_pending_bookings` uchun management command ham, Celery/CronJob ham yo'q. Usiz slotlar yaratilmaydi va HELD holatida qotib qoladi.~~ **✅ tuzatildi:**
   - `python manage.py generate_slots [--days N] [--doctor UUID]` — har kecha. Shifokorlar ro'yxati `WorkingRule` dan olinadi (`schedule_services.generate_slots_for_all_doctors`), modul chegarasi buzilmaydi
   - `python manage.py expire_bookings` — har daqiqa. **Tartib muhim:** avval `expire_pending_bookings`, keyin `release_expired_holds`. Teskarisida slot FREE bo'lib, bron abadiy `PENDING_PAYMENT` da qotib qolardi
   - `python manage.py run_scheduler` — Kubernetes'siz muhit uchun ikkalasini bitta jarayonda bajaradi (bitta nusxada ishga tushiring)
   - `k8s/booking-cronjobs.yaml` — prod uchun ikkita CronJob (`* * * * *` va `0 2 * * *` Asia/Tashkent, `concurrencyPolicy: Forbid`)
   - **Topilgan bug:** `_build_slots_for_day` Django 5 da olib tashlangan `django.utils.timezone.utc` ni ishlatardi — ya'ni `generate_slots` **hech qachon ishlamagan**. `datetime.timezone.utc` ga almashtirildi
   - `apps/booking/test_periodic_jobs.py` — 5 ta test (generatsiya, idempotentlik, eskirgan qoida, expire tartibi, muddati o'tmagan bronga tegmaslik). Jami **36 passed**
24. ~~Kafka consumer'lar uchun ishga tushirish command'i yo'q~~ **✅ tuzatildi:**
   - `python manage.py run_notification_consumer` — SMS (`apps/notifications`)
   - `python manage.py run_search_projector` — Elasticsearch (`apps/search`)
   - `python manage.py run_analytics_consumer [--init-schema]` — ClickHouse (`apps/utils`, chunki `analytics` Django app emas). `--init-schema` jadvallarni `IF NOT EXISTS` bilan yaratadi (`analytics.consumer.ensure_schema`)
   - `k8s/booking-workers.yaml` — 4 ta Deployment (outbox publisher 1 nusxa + `Recreate`, notification 2, projector 1, analytics 1)
   - **`api/events/runner.py`** — `GracefulStop`: SIGTERM/SIGINT ushlanadi, sikl toza tugaydi. Ilgari Python SIGTERM'da darhol o'lardi va `finally` ishlamasdi (consumer group'dan chiqilmasdi, analytics buferi yo'qolardi). Publisher ham to'xtashda `producer.flush()` qiladi
   - **Zaharli xabar**: `decode_envelope()` — JSON emas yoki `event_id`/`event_type`/`data` yo'q xabar logga yoziladi va commit qilinadi. Ilgari bunday xabar consumer'ni abadiy yiqitib, butun partitsiyani tiqib qo'yardi. Vaqtinchalik xatolar (baza, ES yo'q) esa ataylab ko'tariladi — commit yo'q, pod qayta ishga tushib xabarni qayta o'qiydi
   - Har xabar oldidan `close_old_connections()` — uzilgan DB ulanishi worker'ni o'ldirmaydi
   - **Topilgan buglar (analytics):**
     * `client.insert("fact_booking", rows)` dict'lar ro'yxatini berardi — `clickhouse_connect` qatorlar + `column_names` kutadi, ya'ni **birinchi haqiqiy yozishda yiqilardi**
     * `created_date` ISO satr bo'lib ketardi — `Date` ustuni uchun `date` ga o'giriladi
     * To'xtashda bufer yozilardi, lekin **commit qilinmasdi** → qayta ishga tushganda o'sha qatorlar ClickHouse'ga ikkinchi marta tushardi. Endi `flush` + `commit`. Buferga tushmagan (noma'lum turdagi) xabarlar offset'i ham siljiydi
   - `apps/notifications/test_consumers.py` — 5 ta test (soxta consumer, Kafka kerak emas). Jami PostgreSQL'da **49 passed**
25. Click to'lovi webhook'i yo'q — faqat `build_checkout_url` havola yasaydi
26. `search_doctors` (Elasticsearch) uchun HTTP endpoint yo'q — API hali `catalog_services.list_doctors` dan o'qiydi
27. ~~`requirements.txt` va `Dockerfile` yo'q~~ **✅ tuzatildi:**
   - `requirements.txt` (prod) + `requirements-dev.txt` (pytest, pytest-django, grpcio-tools) — versiyalar venv'dagi testlar o'tgan holatdan, `pip check` → konflikt yo'q
   - **Yetishmagan paketlar topildi va qo'shildi:** `redis` (prodda `REDIS_URL` majburiy, lekin Python klienti o'rnatilmagan edi — Django keshga birinchi murojaatda yiqilardi), `gunicorn`, `whitenoise`, `tzdata` (`python:3.12-slim` da tizim tzdata yo'q → `ZoneInfo("Asia/Tashkent")` yiqilardi)
   - ℹ️ O'rnatilgan Django — **6.0.7** (settings sarlavhasidagi "5.0.6" eskirgan)
   - `Dockerfile` — 2 bosqichli (kompilyator faqat build'da), root'siz foydalanuvchi (`uid 10001`), default `CMD` = gunicorn (`--graceful-timeout 25` < k8s grace 30s). API, worker'lar va CronJob'lar **bitta image**, faqat `command` farq qiladi
   - `collectstatic` build'da **prod rejimida** (`DJANGO_DEBUG=0` + soxta qiymatlar faqat shu `RUN` uchun) — aks holda WhiteNoise manifesti yaratilmay, prodda admin sahifalari 500 berardi. Tekshirildi: `staticfiles.json` yaratiladi, 707 fayl post-processed
   - `settings.py`: `STATIC_ROOT`, `WhiteNoiseMiddleware`, `STORAGES` (manifest faqat prodda — lokal/testlar collectstatic'siz ishlayveradi)
   - `.dockerignore` — venv, sqlite, `.claude`, k8s image'ga tushmaydi
   - `k8s/booking-migrate-job.yaml` — migratsiya har deploy'dan oldin **bitta Job** (initContainer emas: 3 pod bir vaqtda `migrate` qilardi)
   - ⚠️ **Docker build o'zi sinalmagan** — mashinada Docker Desktop daemon ishlamayapti. Birinchi build: `docker build -t medbron/booking:test .`
   - Jami PostgreSQL'da **49 passed**
28. `apps/*/admin.py` bo'sh — admin panelda modellar ro'yxatdan o'tmagan
29. Faza 2–7 real infratuzilmada (Kafka, gRPC notification servisi, ES, ClickHouse, k6) sinab ko'rilmagan

### Umumiy holat
| Faza | Holati |
|---|---|
| 1. Monolit, bron, slot, OTP, JWT | ✅ Tayyor, uchidan-uchiga tekshirilgan |
| 2. gRPC | 🟡 Stub'lar bor, notification servisi yo'q |
| 3. Kafka, Outbox | 🟡 Outbox yoziladi va testlangan, Kafka bilan yurgizilmagan |
| 4. To'lov, saga | 🟡 Payme + testlar bor, Click yo'q |
| 5. Elasticsearch | 🟡 Kod bor, API'ga ulanmagan |
| 6. Kesh, yuk testi | 🟡 Kesh bor, k6 yurgizilmagan |
| 7. K8s, metrika, tracing, OLAP | 🟡 Health probe'lar ishlaydi, qolgani sinalmagan |
