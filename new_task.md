# MedBron — Mukammallik rejasi

> **Umumiy baho: 6/10.** Besh soha bo'yicha kesim:
>
> | Soha | Hozir | Bo'lim |
> |---|:---:|---|
> | Xavfsizlik | 5/10 | [A](#a-xavfsizlik--510) |
> | To'lov | 3/10 | [B](#b-tolov--310) |
> | Biznes logika | 5/10 | [C](#c-biznes-logika--510) |
> | Shifokor va klinika tomoni | 1/10 | [D](#d-shifokor-va-klinika-tomoni--110) |
> | Arxitektura | 8/10 | [E](#e-arxitektura--810) |
>
> Har bir kamchilik uchun: **muammo → real ssenariy → yechim**. Har sohaning oxirida "mukammal" ta'rifi (checklist), hujjat oxirida **bosqichma-bosqich yo'l xaritasi**.
>
> Asos: `API_HUJJATI.md` (2026-09-14).
>
> ## 📍 Joriy holat (2026-09-18)
>
> **Bosqich 0, 1 va 2 — kod qismi to'liq. Sprint 3.1 (Konversiya), 3.2 (Ishonch), 3.3 (Xavfsizlikni yopish) va 3.4 (Kichik, lekin ko'rinadigan) tugadi — ya'ni Bosqich 3 ning kod qismi yopildi** — C9, C3, C10, B10; C11, C4, C6, C7; A7 throttle, A10 PII/retention/akkaunt o'chirish, A11 audit log, A12 admin 2FA + IP, A13 xavfsizlik sozlamalari + CI SAST, A14 OTP. 3.4: C15.1 cursor paginatsiya + filtrlar, C15.2 bemor/manzil soft-delete, C15.5 "yaqinimdagi", C15.6 sevimlilar, C15.7 "yana bron qilish". **Bosqich 4 boshlandi — To'lov bloki kod qismi tugadi:** B6 Click (Prepare/Complete + reversal), B12 fiskal cheklar (Payme `detail`, Click OFD), C12 promo / birinchi bron / takroriy qabul (paket va dinamik narx qoldi). **B2B bloki ham tugadi:** D7 klinika admini paneli (ikki tomonlama taklif, umumiy jadval, dam olish kunlari, reception qidiruvi, hisobot), D8 xonalar. **Bozor bloki ham tugadi:** C13 push + Telegram + SMS (kanal ustuvorligi, sozlamalar, shablonlar bazada), C14 `uz`/`ru`. Keyingisi: **Bosqich 4 — Ma'lumot bloki** (E14 partitioning va arxivlash, agregat jadvallar, C15.4 qidiruv). Qo'lda bajariladigan tekshiruvlar [`QOLDA_BAJARILADIGAN.md`](QOLDA_BAJARILADIGAN.md) da, loyiha oxirida bajariladi.
>
> Belgilar: ~~ustiga chizilgan~~ = bajarildi · ⚠️ QISMAN = bir qismi qilingan (izohda nima qolgani yozilgan).
>
> Bosqich 0 dan qolganlari (real muhit kerak): Payme sandbox CI'da, real telefon bilan to'liq oqim, `hold_slot` parallel testi PostgreSQL'da.

---

# A. XAVFSIZLIK — 5/10

**Nega 5:** Poydevor to'g'ri qo'yilgan — OTP xeshlangan va uch qatlamli limitlangan, `secrets` ishlatilgan, user enumeration yopilgan, telefon logda maskalangan, Payme Basic Auth `compare_digest` bilan, `get_queryset` filtrlari 404 qaytaradi. Bu tasodifiy emas, ataylab qilingan ish.

**Nega 10 emas:** Eng muhim yozuv operatsiyasi — `create_booking` — obyekt darajasidagi avtorizatsiyani **umuman qilmaydi**. Ya'ni himoya o'qish tomonida bor, yozish tomonida yo'q. Bunga qo'shimcha sessiya boshqaruvi yo'q (chiqish qilib bo'lmaydi), audit yo'q, va PII nazoratsiz tarqaydi.

---

## ~~A1. 🔴 IDOR — begona bemor va manzil bilan bron~~ ✅

**Muammo.** `create_booking` `patient_id` va `address_id` so'rovchiga tegishli ekanini tekshirmaydi.

**Ssenariy.** Hujumchi UUID'larni brute-force qilmaydi — unga kerak emas. U o'z akkauntidan boshqa odamning `patient_id` si bilan bron yaratadi: SMS begona odamning ismi bilan ketadi, analitikaga begona yosh/jins tushadi, va javob kodidan (201 vs 400) bemor mavjudligini bilib oladi. Tibbiy kontekstda bu "falon odam falon shifokorga yozildi" degan ma'lumot oqishi.

**Yechim.**
```python
# api/patients/services.py
def get_owned_patient(client_id: UUID, patient_id: UUID) -> PatientDTO:
    """Topilmasa yoki begona bo'lsa — bir xil xato (mavjudlik oshkor bo'lmaydi)."""
    try:
        p = Patient.objects.get(id=patient_id, owner_id=client_id)
    except Patient.DoesNotExist:
        raise InvalidBookingRequest("patient_id noto'g'ri")
    return PatientDTO.from_model(p)

def get_owned_address(client_id: UUID, address_id: UUID) -> AddressDTO: ...
```
`_validate_and_load_services` ichida shu funksiyalarni chaqirish. **Muhim:** "topilmadi" va "begona" uchun xato matni **bir xil** bo'lsin.

---

## ~~A2. 🔴 Slot boshqa shifokorniki bo'lishi mumkin~~ ✅

**Muammo.** `slot_id` ↔ `doctor_id` bog'liqligi tekshirilmaydi.

**Ssenariy.** Mijoz arzon shifokor A ga bron qiladi, lekin qimmat shifokor B ning slot ID'sini yuboradi. Natija: B ning vaqti band bo'ladi, A ning narxi to'lanadi. B ning kuni buziladi va buning izi qolmaydi.

**Yechim.** `hold_slot` chaqiruvidan **oldin**:
```python
slot = schedule_services.get_slot_for_booking(slot_id)   # DTO
if slot.doctor_id != doctor_id:
    raise InvalidBookingRequest("slot_id noto'g'ri")
```
Yanada ishonchli: `hold_slot(slot_id, doctor_id)` — compare-and-set `WHERE` shartiga `doctor_id` ni ham qo'shing. Shunda poyga holatida ham buzilmaydi.

---

## ~~A3. 🔴 Xizmat, joy va davomiylik mosligi tekshirilmaydi~~ ✅

**Muammo.** Uchta alohida tekshiruv yo'q:
- `service.doctor_id == doctor_id` (yoki shifokor ishlaydigan klinikaning xizmati)
- `sum(service.duration) <= slot.duration`
- `place == home` ⇔ `slot.clinic is None`

**Ssenariy.** 60 daqiqalik xizmat 30 daqiqalik slotga tushadi → shifokorning keyingi qabuli kechikadi. Yoki uy xizmati klinika slotiga tushadi → ±30 daqiqalik yo'l buferi qo'yilmaydi → shifokor bir vaqtda ikki joyda bo'lishi kerak. Yoki begona shifokorning arzon xizmati bilan qimmat shifokorga yoziladi.

**Yechim.** `_validate_and_load_services` da uchalasi. Davomiylik slotdan katta bo'lsa — yoki xato, yoki **ketma-ket slotlarni** birga band qilish (10-band, "multi-slot booking").

---

## ~~A4. 🔴 Obyekt darajasidagi avtorizatsiya markazlashmagan~~ ✅
> Loader'lar + CI to'sig'i (`apps/utils/test_guards.py`). `get_owned_booking` Bosqich 1 da (hozircha view'larda `client=request.user` filtri).

**Muammo.** A1–A3 — bitta kasallikning alomatlari. Hozir har bir view o'zi eslab qolishi kerak. Ertaga yangi endpoint yoziladi va yana unutiladi.

**Yechim — arxitektura qoidasi.** So'rovdan kelgan **har qanday ID** faqat "egalik tekshiruvchi loader" orqali obyektga aylansin:

| Nima | Loader | Qoida |
|---|---|---|
| `patient_id` | `get_owned_patient(client_id, ...)` | `owner == client` |
| `address_id` | `get_owned_address(client_id, ...)` | `user == client` |
| `slot_id` | `get_slot_for_booking(slot_id, doctor_id)` | `doctor` mos, `free` |
| `service_id` | `get_bookable_services(doctor_id, ids, place)` | shifokor/klinika va `place` mos |
| `doctor_id` | `get_bookable_doctor(doctor_id)` | `approved` + `is_active` |
| `booking_id` | `get_owned_booking(client_id, ...)` | `client == request.user` |

Serializerda `IntegerField`/`UUIDField` ishlatilsin, `PrimaryKeyRelatedField(queryset=Model.objects.all())` **hech qachon** — u global queryset bilan begona obyektni ham qabul qiladi.

CI'da tekshiruv: `grep -r "PrimaryKeyRelatedField" api/` → `queryset=.*objects.all()` topilsa build yiqilsin.

---

## ~~A5. 🟠 Idempotency-Key ni o'g'irlash / to'qnashuvi~~ ✅
> TTL 24 soat tozalash hali yo'q (E14 bilan birga).

**Muammo.** Hujjatda kalit foydalanuvchiga bog'langani aytilmagan.

**Ssenariy.** Agar kalit global bo'lsa: hujumchi `Idempotency-Key: 1` yuboradi va boshqa odamning broni javobini oladi (ma'lumot oqishi). Yoki ikki mijoz `"retry-1"` kalitini ishlatsa, ikkinchisi birinchisining bronini ko'radi.

**Yechim.**
```
UNIQUE(client_id, idempotency_key)   -- kalit foydalanuvchiga bog'lansin
```
Qo'shimcha: so'rov tanasining xeshini ham saqlang. Bir xil kalit + **boshqa tana** → `422 Idempotency key reused with different payload`. Kalit TTL: 24 soat. Kalit formati: UUID, uzunligi cheklangan (max 64), aks holda jadval axlat bilan to'ladi.

---

## A6. 🟠 Sessiya boshqaruvi yo'q — chiqish (logout) qilib bo'lmaydi — ⚠️ QISMAN
> ~~refresh~~, ~~logout~~, ~~logout/all~~ qilindi. Qoldi: `GET/DELETE /accounts/sessions`, `device_id` claim, "yangi qurilmadan kirildi" SMS.

**Muammo.** `TokenRefreshView` ulanmagan (15-bo'lim, 12-band), va **logout endpointi ham yo'q**. Blacklist sozlangan, lekin unga hech narsa tushmaydi.

**Ssenariy.** Mijoz telefonini yo'qotdi. 30 kun davomida topgan odam uning tibbiy bronlarini ko'radi, bemor ma'lumotlarini o'qiydi, uning nomidan bron qiladi. Hech qanday chora yo'q.

**Yechim — to'liq sessiya to'plami:**

| Endpoint | Vazifa |
|---|---|
| `POST /accounts/auth/token/refresh` | Rotation bilan (allaqachon sozlangan) |
| `POST /accounts/auth/logout` | Refresh → blacklist |
| `POST /accounts/auth/logout/all` | Barcha qurilmalar (raqam o'g'irlanganda) |
| `GET /accounts/sessions` | Faol qurilmalar ro'yxati: qurilma nomi, oxirgi kirish, IP shahri |
| `DELETE /accounts/sessions/{id}` | Bitta qurilmani chiqarish |

`refresh` ga `device_id` va `device_name` claim'larini qo'shing (OTP verify'da klientdan olinadi). Shunda sessiya ro'yxati mumkin bo'ladi.

**Qo'shimcha qoida:** raqam bo'yicha yangi OTP muvaffaqiyatli bo'lsa — eski sessiyalar qolsinmi? Bank ilovalari qoldirmaydi. Tibbiy ilova uchun: qoldirish mumkin, lekin foydalanuvchiga "yangi qurilmadan kirildi" SMS'i yuborilsin.

---

## ~~A7. 🟠 Yozuv endpointlarida throttle yo'q~~ ✅
> ~~Max 3 ta `pending_payment` bron~~ (3.2) + `api/throttling.py`: `WriteRateThrottle` (barcha POST/PUT/PATCH/DELETE, foydalanuvchi bo'yicha; `booking_create` 20/soat, `booking_cancel` 30/soat, `sensitive` 5/soat, qolgani `write` 60/soat) va `AnonReadThrottle` (anonim GET, IP bo'yicha 300/soat). `DEFAULT_THROTTLE_CLASSES` da — yangi endpoint avtomatik himoyalangan. Payme va Telegram webhook'lari throttle'siz (IP allowlist + imzo). Qiymatlar muhitdan (`THROTTLE_*`).

**Muammo.** Throttle faqat OTP'da. `POST /bookings` cheksiz.

**Ssenariy 1 (resurs bloklash).** Raqobatchi 200 ta akkaunt bilan shifokorning barcha slotlarini 10 daqiqadan band qiladi, hech narsa to'lamaydi, va buni takrorlaydi. Shifokor bir kun davomida hech kimga ko'rinmaydi. Narxi: 200 ta SMS.

**Ssenariy 2 (baza yuki).** `POST /patients/` sikl bilan — 100K qator.

**Yechim.**
```python
THROTTLE_RATES = {
    "otp": "10/hour",
    "booking_create": "20/hour",     # user bo'yicha
    "booking_cancel": "30/hour",
    "write": "60/hour",              # boshqa POST/PUT/PATCH/DELETE
    "anon_read": "300/hour",         # katalog, IP bo'yicha
}
```
**Va biznes cheklovi (throttle'dan muhimroq):** bitta foydalanuvchida bir vaqtda **max 3 ta `pending_payment`** bron. To'rtinchisiga `409 Avval oldingi bronlarni to'lang yoki bekor qiling`. Bu slot-hold abuse'ni throttle'dan yaxshiroq to'xtatadi, chunki akkaunt ko'paytirishga ham qarshi ishlaydi (raqam pullik).

---

## ~~A8. 🟠 Payme webhook'ida qatlamlar yetishmaydi~~ ✅

**Hozir bor:** Basic Auth + `compare_digest` + fail-closed. Bu yaxshi, lekin yagona qatlam.

**Yetishmaydi:**

1. **IP allowlist.** Payme serverlarining IP diapazoni ma'lum — `PAYME_ALLOWED_IPS` sozlamasi, middleware darajasida. Kalit oqib ketsa ham hujumchi boshqa IP'dan kira olmaydi.
2. **So'rov jurnali.** Har bir webhook chaqiruvi `PaymeCallbackLog` ga yozilsin: `method`, `params` (summa bilan), `response`, `ip`, `duration_ms`. Nizo chiqqanda ("pul yechildi, bron yo'q") bu yagona dalil. Retention: 1 yil.
3. **Tana hajmi cheklovi.** `DATA_UPLOAD_MAX_MEMORY_SIZE` webhook uchun alohida kichik qiymat.
4. **Noma'lum `account` maydonlari.** `account` da `booking_id` dan boshqa narsa kelsa — `-31050`, jim qabul qilmaslik.

---

## ~~A9. 🟡 Moderatsiyadan o'tmagan shifokor profili ochiq~~ ✅

**Muammo** (15-bo'lim, 9-band). `get_doctor_detail` `is_bookable` filtrini qo'llamaydi.

**Ssenariy.** Litsenziyasi to'xtatilgan yoki platformadan chiqarilgan shifokor profili to'g'ridan-to'g'ri havola orqali ochiladi va ishlayotgandek ko'rinadi. Huquqiy javobgarlik.

**Yechim.** Ikki xil qaror, biznes tanlaydi:
- **Qattiq:** `approved` bo'lmasa — 404.
- **Yumshoq:** profil ko'rinadi, lekin javobda `is_bookable: false` va `unavailable_reason: "moderation" | "suspended" | "inactive"`, bron tugmasi o'chiq. SEO uchun yaxshiroq, lekin `suspended` shifokorni ko'rsatmaslik kerak — u ham 404.

Tavsiya: `moderation` → 404 (hali mavjud emas), `suspended`/`inactive` → 404, `approved` → 200. Sodda va xavfsiz.

---

## ~~A10. 🟡 PII nazoratsiz tarqaydi~~ ✅
> Telefon `booking.events` / `payment.events` payload'idan olib tashlandi, o'rniga `client_hash = HMAC-SHA256(phone, ANALYTICS_SALT)` (`api/pii.py`); ClickHouse `fact_booking.client_hash` ustuni. SMS consumer raqamni `booking_id` bo'yicha bazadan oladi (eski hodisalardagi `client_phone` ham ishlaydi). `DATA_RETENTION` + `manage.py purge_expired_data` (kunlik CronJob): OTP 7 kun, SMS log 90, outbox 7, ProcessedEvent 30, Payme log 365, audit 5 yil. `POST /accounts/me/delete` — anonimlashtirish (faol bron bo'lsa 409; shifokor — qo'llab-quvvatlash orqali). Qoldi (infra): Kafka topic retention 7 kun — [`QOLDA_BAJARILADIGAN.md`](QOLDA_BAJARILADIGAN.md) 21-band.

**Muammo.** Kafka payload'ida `client_phone`, `patient_age_group`, `patient_gender`, `specialization`. ClickHouse'da bular abadiy. "Falon telefon egasi ginekologga yozilgan" — bu **maxsus toifadagi shaxsiy ma'lumot** (sog'liq).

**Yechim.**

| Qayerda | Nima o'zgaradi |
|---|---|
| Analitika payload | `client_phone` o'rniga `client_hash = HMAC-SHA256(phone, ANALYTICS_SALT)` — kohorta tahlili ishlaydi, raqam yo'q |
| SMS payload | Telefon kerak, lekin alohida topikda (`booking.notifications`, retention 24 soat) yoki `booking_id` bo'yicha Postgres'dan olinadi |
| ClickHouse | `specialization` o'rniga `specialization_group` (Terapevt/Tor mutaxassis) — agar tor tahlil kerak bo'lmasa |
| Loglar | Telefon maskalangan (bor), bemor ismi **hech qachon** logga tushmasin |
| Kafka retention | `booking.events` uchun 7 kun, undan ortiq emas |

Qo'shimcha: ma'lumotlarni saqlash muddati siyosati (`DATA_RETENTION`) va foydalanuvchi so'rovi bo'yicha o'chirish oqimi (`POST /accounts/me/delete`) — bron tarixi anonimlashtiriladi, buxgalteriya yozuvlari qonuniy muddatgacha qoladi.

---

## ~~A11. 🟡 Audit log yo'q~~ ✅
> `AuditLog` (`apps/utils`) + `api/audit.py`. Actor va IP `AuditContextMiddleware` orqali avtomatik. Yoziladi: Django admin'dagi har bir amal (LogEntry signal), bronni qo'lda yakunlash / no-show, shifokor holati (approve/reject/suspend/reinstate), narx/xizmat o'zgarishi, refund, nizo hal qilish, payout, sharh moderatsiyasi, rol o'zgarishi, akkaunt o'chirish, 2FA sozlash. O'zgarmas: ORM'da UPDATE/DELETE taqiqlangan, PostgreSQL'da trigger (faqat retention tozalovi o'chira oladi). Admin'da faqat o'qish.

**Muammo.** "Kim bu bronni qo'lda `confirmed` qildi?", "Kim narxni 3 barobar oshirdi?" — javob yo'q.

**Yechim.** `AuditLog` jadvali: `actor_id`, `actor_role`, `action`, `object_type`, `object_id`, `before`, `after`, `ip`, `correlation_id`, `created_at`. Yozilishi shart bo'lgan amallar:
- Har qanday admin yozuv operatsiyasi (Django admin signal orqali)
- Bron holatini qo'lda o'zgartirish
- Narx/xizmat o'zgarishi
- Shifokor statusini o'zgartirish (approve/suspend)
- Refund
- Rolni o'zgartirish

Audit log **o'zgartirilmasin** (`UPDATE`/`DELETE` huquqi yo'q, faqat `INSERT`).

---

## ~~A12. 🟡 Django admin — eng katta himoyalanmagan yuza~~ ✅
> `api/admin_security.py`: `ADMIN_URL` (prodda tasodifiy prefiks majburiy), `ADMIN_ALLOWED_IPS` (CIDR; boshqa IP'ga 404, prodda majburiy), majburiy TOTP 2FA (`api/totp.py`, RFC 6238, replay himoyasi; `manage.py admin_2fa_setup <telefon>`). Sessiya 30 daqiqa faolsizlikdan keyin tugaydi, cookie `Secure` + `HttpOnly` + `SameSite=Strict`. Admin amallari audit log'da. Rol matritsasi: admin panelga faqat `is_staff` kiradi; `clinic_admin` o'z API'si orqali ishlaydi (panelga kirmaydi) — ular uchun alohida panel D7 bilan.

**Muammo.** `platform_admin` bazaga to'liq kirish huquqiga ega, sessiya orqali, parol bilan.

**Yechim.**
- Admin panel alohida domenda yoki VPN ortida (`ADMIN_URL` tasodifiy prefiks + IP allowlist)
- **Majburiy 2FA** (`django-otp`) — SMS emas, TOTP
- Sessiya: 30 daqiqa, `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE=Strict`
- Admin harakatlari audit logda (A11)
- Ro'l-huquq matritsasi: `clinic_admin` faqat o'z klinikasi obyektlarini ko'rsin (hozir admin panel bo'sh — buni **boshidan** to'g'ri qiling)

---

## A13. 🟡 Infratuzilma darajasidagi standart to'plam — ⚠️ QISMAN
> Kod qismi qilindi: HSTS (1 yil + preload), SSL redirect (`healthz/` bundan mustasno), Secure cookie'lar, `nosniff`, `X-Frame-Options: DENY`, Referrer/COOP siyosati, prodda `ALLOWED_HOSTS='*'` taqiqlangan, Swagger prodda o'chiq (bor edi), CORS — `django-cors-headers` ataylab yo'q (API mobil uchun). CI (`security` job): `bandit` (SAST), `pip-audit`, `gitleaks`, `check --deploy --tag security`. Qoldi (infra, qo'lda): Vault/K8s Secret rotation, bazaga TLS + alohida foydalanuvchi, PITR zaxira va **tiklash sinovi** — [`QOLDA_BAJARILADIGAN.md`](QOLDA_BAJARILADIGAN.md) 21-band.

Hujjatda aytilmagan, tekshirish kerak:

| Nima | Qiymat |
|---|---|
| `DEBUG` | prodda `False`, ishga tushishda tekshiriladi |
| `ALLOWED_HOSTS` | aniq ro'yxat, `*` emas |
| `SECURE_HSTS_SECONDS` | 31536000 + preload |
| `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `True` |
| CORS | aniq origin ro'yxati, `CORS_ALLOW_ALL_ORIGINS=False` |
| `SWAGGER_ENABLED` | prodda `False` yoki auth ortida |
| Secrets | K8s Secret / Vault, `.env` repo'da emas, rotation rejasi |
| CI | `pip-audit` / `safety` (bog'liqliklar), `bandit` (SAST), secret scanning |
| Baza | TLS, alohida foydalanuvchi, `SUPERUSER` emas |
| Zaxira | PITR yoqilgan, **tiklanish sinovdan o'tkazilgan** (o'tkazilmagan zaxira = zaxira yo'q) |

---

## ~~A14. 🟢 OTP — nozik yaxshilanishlar~~ ✅
> 1) Raqam bo'yicha soatiga 10 ta xato verify → 1 soat blok (429), hisoblagich keshda (Redis). 2) Faqat `+998XXXXXXXXX` (boshqasi 400, SMS yuborilmaydi); `medbron_otp_requests_total{result}` va `medbron_otp_verify_failed_total{reason}` metrikalari + `MedbronOtpSpike` / `MedbronOtpVerifyBruteforce` alertlari. 3) SMS matnida brend va ogohlantirish — bor edi. 4) Qurilma bog'lash — A6 bilan (sessiyalar ro'yxati), hali ochiq.

Hozirgi holat kuchli. Qo'shimchalar:

1. **Verify uchun ham raqam bo'yicha limit.** Hozir 5 urinish bitta kodga. Hujumchi 5 marta xato qiladi → yangi kod so'raydi (soatiga 5 ta) → 25 urinish/soat. Qo'shing: bitta raqamga soatiga max 10 ta **muvaffaqiyatsiz** verify, keyin 1 soat blok.
2. **SMS pumping.** Hujumchi o'z premium raqamiga minglab OTP so'ratadi (provayder puli sizdan ketadi). Himoya: xalqaro raqamlarni bloklash (faqat `+998`), yangi raqamlar uchun qattiqroq limit, `medbron_otp_sent_total{country}` metrikasi + anomaliya alerti.
3. **Kod matnida brend va ogohlantirish:** "MedBron kodi: 123456. Bu kodni hech kimga aytmang." — ijtimoiy muhandislikka qarshi.
4. **Qurilma bog'lash:** verify'da `device_id` olinsin (A6).

---

## ✅ Xavfsizlik "10/10" ta'rifi

- [x] ~~Har bir tashqi ID egalik tekshiruvchi loader orqali (A1–A4), CI qoidasi bilan majburlangan~~
- [x] ~~Idempotency-Key foydalanuvchiga bog'langan + tana xeshi (A5)~~
- [ ] ~~Refresh + logout + logout/all~~ + sessiyalar ro'yxati (A6) — ⚠️ QISMAN
- [x] ~~Barcha yozuv endpointlarida throttle + max 3 ta faol hold (A7)~~
- [x] ~~Payme: IP allowlist + to'liq callback jurnali (A8)~~
- [x] ~~`approved` bo'lmagan shifokor 404 (A9)~~
- [x] ~~Analitikada telefon yo'q, PII retention siyosati bor (A10)~~
- [x] ~~Audit log — o'zgarmas, barcha admin va holat amallari (A11)~~
- [x] ~~Admin: 2FA + IP cheklov + rol matritsasi (A12)~~
- [ ] Infratuzilma checklisti to'liq, ~~CI'da SAST va dependency scan~~ (A13) — ⚠️ QISMAN (zaxira/TLS/Vault — qo'lda)
- [x] ~~OTP: verify limiti, faqat `+998`, anomaliya alerti (A14)~~
- [ ] Yiliga bir marta tashqi penetration test

---

# B. TO'LOV — 3/10

**Nega 3:** `Payment` yozuvi hech qachon yaratilmaydi → `CheckPerformTransaction` har doim `-31050` → **hech bir bron to'lana olmaydi**. Ya'ni to'lov moduli mavjud, lekin ishlamaydi. Bor kod (webhook skeleti, saga kompensatsiyasi, `select_for_update`, idempotentlik) sifatli — shuning uchun 0 emas, 3.

**Nega bu eng past baho:** to'lov — mahsulotning yagona daromad nuqtasi va xatosi eng qimmat joyi. Bu yerda "keyinroq tuzatamiz" degan narsa yo'q: har bir xato — real pul va real nizo.

---

## ~~B1. 🔴 Checkout oqimi yo'q — `Payment` yaratilmaydi~~ ✅

**Muammo** (15-bo'lim, 4-band). `create_payment` va `build_checkout_url` yozilgan, hech kim chaqirmaydi.

**Yechim.**
```
POST /api/v1/payment/bookings/{booking_id}/checkout
Body:  {"provider": "payme" | "click"}
200:   {"payment_id": "uuid", "checkout_url": "https://checkout.paycom.uz/...",
        "amount": 150000, "expires_at": "2026-09-14T10:10:00Z"}
409:   bron `pending_payment` holatida emas
```
Qoidalar:
- Bitta bronga bir nechta `Payment` bo'lishi mumkin (birinchi urinish muvaffaqiyatsiz → ikkinchi provayder). Lekin **faqat bittasi** `succeeded` bo'la oladi — `UNIQUE(booking_id) WHERE status IN ('succeeded')` qisman indeks.
- `checkout` idempotent: mavjud `created` holatdagi `Payment` bo'lsa — o'sha qaytariladi, yangi yaratilmaydi.
- `expires_at` = bron hold muddati (B3 ga qarang).

**Alternativ:** `create_booking` javobida darhol `checkout_url` qaytarish (bitta so'rov kam). Lekin alohida endpoint yaxshiroq — provayderni almashtirish va qayta urinish mumkin.

---

## B2. 🔴 Payme protokoli to'liq emas — ⚠️ QISMAN
> ~~7 ta metod + kontrakt testlari~~ qilindi. Qoldi: Payme sandbox to'plami CI'da (merchant kalitlari kerak), kodlarni rasmiy hujjat bilan solishtirish.

**Yetishmaydigan metodlar:** `CreateTransaction`, `CheckTransaction`, `GetStatement`. Bularsiz Payme sandbox testidan o'tmaysiz va shartnoma imzolanmaydi.

**To'liq metodlar to'plami:**

| Metod | Vazifa | Kritik qoidalar |
|---|---|---|
| `CheckPerformTransaction` | To'lovdan oldingi tekshiruv | `Payment` bor; summa **aniq** teng; **bron `pending_payment` holatida**; hold muddati o'tmagan |
| `CreateTransaction` | Tranzaksiya ochish | Yangi `id` → `Payment.status=processing`, `external_id` saqlanadi, **hold uzaytiriladi**. Mavjud `id` → o'sha holat qaytariladi (idempotent). Boshqa `id` bilan o'sha bronga → xato |
| `PerformTransaction` | Pulni yechish | `select_for_update`; takroriy → birinchi javob; `perform_time` saqlanadi; bron holatini **qayta tekshirish** |
| `CancelTransaction` | Bekor / refund | `reason` saqlanadi; `state` 1 dan bekor → `-1`, 2 dan bekor → `-2` |
| `CheckTransaction` | Holat so'rash | `create_time`, `perform_time`, `cancel_time`, `transaction`, `state`, `reason` |
| `GetStatement` | Davr bo'yicha tranzaksiyalar | **Reconciliation uchun asosiy metod** — B7 |
| `SetFiscalData` | Fiskal chek ma'lumoti | O'zbekistonda onlayn to'lov uchun talab — B12 |

**Tranzaksiya holatlari:** `1` yaratilgan → `2` bajarilgan; `-1` yaratilgandan bekor; `-2` bajarilgandan bekor (refund).

**Xato kodlari:** `-32700` (parse), `-32600` (noto'g'ri so'rov), `-32601` (metod yo'q), `-32504` (auth), `-31001` (summa), `-31003` (tranzaksiya topilmadi), `-31008` (bajarib bo'lmaydi), `-31050...-31099` (`account` xatolari — buyurtma topilmadi, holat mos emas).

> ⚠️ Aniq kodlar, maydon nomlari va holat o'tishlarini **joriy rasmiy Payme Merchant API hujjatiga** solishtirib tekshiring — protokol vaqt o'tishi bilan o'zgargan.

**Testlash:** Payme sandbox to'plami CI'da avtomatik ishlasin (`pytest -m payme_sandbox`). Har bir metod uchun kontrakt testi: to'g'ri so'rov, noto'g'ri summa, takroriy so'rov, noma'lum booking, muddati o'tgan bron.

---

## ~~B3. 🔴 Hold 10 daqiqa, Payme tranzaksiyasi 12 soat — moslashmagan~~ ✅
> Refund'ning o'zi va mijozga SMS — B4 da (hozircha `needs_refund=True` + alert log).

**Muammo** (15-bo'lim, 6-band). Bu eng xavfli nomuvofiqlik. Mijoz 11-daqiqada to'lasa: bron allaqachon `expired`, slot boshqaga sotilgan, `confirm_booking` `BookingError` beradi, u ushlanmaydi → **HTTP 500**. Payme buni "javob bermadi" deb qayta yuboradi. Pul mijozdan yechilgan, bron yo'q, hech kim bilmaydi.

**Yechim — uch qatlam:**

**1. Hold'ni tranzaksiya ochilganda uzaytirish.**
```python
# CreateTransaction ichida
booking.hold_until = max(booking.hold_until, now() + PAYMENT_HOLD_EXTENSION)  # 15 daq
```
Mijoz checkout sahifasiga o'tdi = u jiddiy. 10 daqiqa "hech narsa qilmagan" mijoz uchun, 15 daqiqa "to'layotgan" mijoz uchun.

**2. `CheckPerformTransaction` da holat tekshiruvi.**
```python
if booking.status != "pending_payment":
    return error(-31099, "Buyurtma muddati o'tgan yoki bekor qilingan")
```
Shunda Payme **pulni yechmaydi** — muammo paydo bo'lishidan oldin to'xtaydi. Bu eng muhim bitta qator.

**3. `PerformTransaction` da fail-safe.** Agar shunga qaramay yetib kelsa (poyga holati):
```python
try:
    confirm_booking(booking_id)
except BookingError:
    # Pul yechilgan. Bronni tiklab bo'lmaydi.
    payment.status = "succeeded"           # haqiqatni yozamiz
    payment.needs_refund = True
    emit(RefundRequested(payment_id, reason="booking_unavailable"))
    alert("Avtomatik refund kerak")
    return ok(state=2)                      # Payme uchun muvaffaqiyat — 500 EMAS
```
Va mijozga SMS: "To'lovingiz qabul qilindi, lekin vaqt band bo'lib qolgan. Pul 1–3 kunda qaytariladi."

**Hech qachon 500 qaytarmang.** Payme protokolida har doim 200 + `error` maydoni.

---

## ~~B4. 🔴 Refund oqimi umuman yo'q~~ ✅
> Payme Merchant API'da savdogar refund'ni o'zi boshlay olmaydi → refund `manual_required` navbatiga tushadi + alert; kabinetda bekor qilinganda Payme `CancelTransaction -2` yuboradi va refund avtomat `succeeded` bo'ladi. AuditLog'ga yozish — A11 bilan (Sprint 3.3).

**Muammo** (15-bo'lim, 7-band). `cancel` `confirmed` bronni bekor qiladi, `Payment` `succeeded` qoladi, pul qaytmaydi.

**Ssenariy.** Mijoz to'ladi, 5 daqiqadan keyin bekor qildi. Slot bo'shadi, pul sizda qoldi. Mijoz shikoyat qiladi → qo'lda qaytarasiz → bu 100 ta bronda kuniga bir necha soat ish. 1000 tada — imkonsiz.

**Yechim — refund'ni hodisa qiling:**

```
cancel_booking (siyosatga ko'ra refund summasi hisoblanadi)
   ↓ bitta tranzaksiyada
Refund(payment, amount, reason, status=pending) + Outbox: RefundRequested
   ↓
run_refund_worker:  provider.refund(external_id, amount)
   ↓ muvaffaqiyat            ↓ xato
Refund=succeeded          retry (exponential backoff, max 5)
Payment=refunded /           ↓ 5 dan keyin
  partially_refunded      Refund=failed + ALERT + qo'lda ko'rib chiqish navbati
SMS mijozga
```

**Refund modeli** (`Payment` dan alohida — bitta to'lovga bir nechta qisman refund bo'lishi mumkin):
`id`, `payment_id`, `amount`, `reason`, `status`, `external_refund_id`, `initiated_by` (client/doctor/admin/system), `created_at`, `completed_at`.

**Qoidalar:**
- `sum(refunds.amount) <= payment.amount` — DB constraint
- Idempotent: bitta `booking_id` + `reason` uchun bitta refund
- Refund ham `AuditLog` ga tushadi

---

## ~~B5. 🔴 Outbound to'lov klienti yo'q~~ ✅
> `api/payments/providers.py`: Protocol, `call()` (retry + backoff + jitter, circuit breaker 5/60s, `ProviderRequestLog`). Haqiqiy Payme/Click HTTP klientlari — ular bilan API kelishilganda.

**Muammo.** Hozir faqat inbound webhook. Platforma **o'zi** hech narsa qila olmaydi: refund ham, holat so'rash ham, reconciliation ham.

**Yechim — provayder abstraksiyasi:**
```python
class PaymentProvider(Protocol):
    def create_checkout(self, payment: Payment) -> CheckoutDTO: ...
    def get_transaction(self, external_id: str) -> TransactionDTO: ...
    def refund(self, external_id: str, amount: Decimal, reason: str) -> RefundDTO: ...
    def get_statement(self, frm: datetime, to: datetime) -> list[TransactionDTO]: ...
```
Har bir chaqiruv uchun: **timeout** (connect 3 s, read 10 s), **retry** faqat idempotent operatsiyalarda, **exponential backoff + jitter**, **circuit breaker** (5 ta ketma-ket xato → 60 s ochiq), va **har bir so'rov/javob jurnalda**.

Bu abstraksiya Click qo'shishni bir necha kunlik ishga aylantiradi — hozir esa butun modulni qayta yozish kerak bo'lardi.

---

## ~~B6. 🟠 Click yo'q — bozorning yarmi~~ ✅
> `api/payments/click.py`: SHOP API `POST /payment/click/prepare` va `/complete`, MD5 imzo (`compare_digest`, kalit bo'sh bo'lsa fail-closed), `CLICK_ALLOWED_IPS`, idempotent (takroriy Prepare/Complete — o'sha javob), Click bekor qilsa (`error<0`) to'lov `cancelled`, B3 fail-safe. Umumiy "pul yechildi" logikasi `api/payments/settlement.py` ga chiqarildi — Payme va Click bitta funksiyani ishlatadi. Har bir chaqiruv `ProviderRequestLog` da. `ClickProvider.refund` — Merchant API `payment/reversal` (faqat to'liq summa; qisman — `manual_required`). Uzum/Apelsin/UzCard — keyin.

O'zbekistonda Payme yolg'iz yetmaydi. Minimal to'plam: **Payme + Click**. Keyin: Uzum Bank, Apelsin, karta orqali to'g'ridan-to'g'ri (UzCard/Humo acquiring).

B5 dagi abstraksiya bo'lsa, Click uchun faqat: `ClickProvider` klassi + Click webhook endpointi (`Prepare`/`Complete` metodlari, MD5 imzo tekshiruvi) + `Payment.provider` maydoni.

**Muhim:** har bir provayderning o'z holat mashinasi bor. Ularni umumiy `Payment.status` ga to'g'ri xaritalash kerak, provayder xususiyatlarini `Payment.provider_state` (JSON) da saqlang.

---

## ~~B7. 🔴 Reconciliation yo'q — pul jim yo'qoladi~~ ✅
> Tashqi (provayder reestri / statement) + ichki tekshiruvlar (daftarga tushmagan to'lov, to'lovsiz bron, yaratilmagan refund, balans). Payme uchun reestr fayli (`--file`) — formatini Payme bilan tasdiqlash qo'lda.

**Muammo.** Hech narsa "Payme'dagi pul bizning bazaga mos keladimi?" degan savolni bermaydi.

**Ssenariy.** Kuniga 500 bron, 0.2% xato — kuniga 1 ta yo'qolgan tranzaksiya. Oyiga 30 ta. Yil oxirida buxgalteriya mos kelmaydi va sababini topib bo'lmaydi.

**Yechim — kunlik `reconcile_payments` job:**
```
1. provider.get_statement(kecha 00:00 → 23:59)
2. Har bir provayder tranzaksiyasini bizning Payment bilan solishtirish
3. Farqlarni PaymentDiscrepancy ga yozish:
   - provider_only    → bizda yo'q, ularda succeeded  🔴 ENG XAVFLI (pul olindi, xizmat yo'q)
   - local_only       → bizda succeeded, ularda yo'q  🔴 (xizmat berildi, pul yo'q)
   - amount_mismatch  → summalar farq qiladi          🔴
   - status_mismatch  → holatlar farq qiladi          🟠
4. Har qanday farq → ALERT + admin panelda navbat
5. Metrika: medbron_reconciliation_discrepancies_total{type}
```
Maqsad: farqlar soni **doimiy 0**. Nolga teng bo'lmasa — har biri qo'lda hal qilinadi va sababi topiladi.

---

## ~~B8. 🟠 Pul modeli yo'q — komissiya, payout, ledger~~ ✅
> Komissiya snapshot, ikki yozuvli ledger, `PayoutPeriod` + payout oqimi (Sprint 2.4).

**Muammo.** `Booking.total_price` bor, lekin bu pul kimga tegishli ekani yozilmagan. Platforma qanday daromad qiladi? Shifokorga qachon va qancha to'lanadi?

**Yechim — `Booking` ga maydonlar:**
```
total_price        150 000   mijoz to'laydi
platform_fee        22 500   komissiya (15%)
provider_fee         1 500   Payme ushlab qoladi (1%)
doctor_payout      126 000   shifokorga
```
Snapshot qoidasi `BookingItem` dagidek: **komissiya foizi bron paytida muzlatiladi** (`commission_rate` maydoni). Ertaga foiz o'zgarsa, eski bronlar o'zgarmaydi.

**Payout oqimi:**
```
PayoutPeriod (shifokor × hafta) → status: open → pending → paid
  qamrab oladi: completed bronlar (cancelled/no_show emas)
  minus: refundlar, jarimalar
  →  PayoutStatement (PDF/Excel) + bank o'tkazmasi
```

**Ledger (ikki yozuvli daftar).** Jiddiy pul hajmida `total_price` maydonlari yetmaydi. Kerak: `LedgerEntry(account, debit, credit, ref_type, ref_id, created_at)` — har bir pul harakati ikki qatorda. Hisoblar: `client_payments`, `platform_revenue`, `doctor_payable`, `provider_fees`, `refunds`. Shunda istalgan paytda "shifokorlarga qancha qarzdormiz" savoliga aniq javob bor va u hech qachon buzilmaydi.

---

## B9. 🟠 `Payment` holat mashinasi to'liq ishlatilmaydi

Hozir: `created → succeeded → refunded`. `processing` va `failed` modelda bor, ishlatilmaydi.

**To'liq mashina:**
```
created ──checkout──> processing ──Perform──> succeeded
   │                      │                      │
   │                      └──Cancel(state -1)──> cancelled
   │                                             │
   └──timeout──> expired                         ├──Refund──> refunded
                                                 └──Partial──> partially_refunded
                     failed  ← provayder xatosi (qayta urinish mumkin)
```
Har bir o'tish uchun: kim boshlaydi, qanday hodisa chiqadi, SMS bormi — jadval ko'rinishida hujjatlashtiring.

---

## ~~B10. 🟠 To'lov modelini biznes nuqtai nazaridan qayta ko'rish~~ ✅
> `Booking.payment_mode` + `prepay_amount` (snapshot), `Doctor.clinic_payment_mode` (shifokor o'zi tanlaydi). Rejimni **mijoz tanlamaydi**: uy chaqiruvi har doim `prepaid`, no-show tarixi yomon mijoz (≥2) majburan `prepaid`, qolganida shifokor sozlamasi. `at_clinic` bron darhol `confirmed` (slot BOOKED, to'lov kutilmaydi). Checkout `prepay_amount` ni ishlatadi. Zaklad foizi — `DEPOSIT_PERCENT` (20%).

Texnik emas, lekin to'lov bahosining bir qismi. Hozirgi "avval to'la, 10 daqiqa" modeli O'zbekiston bozorida konversiyani keskin tushiradi.

| Rejim | Kimga | Qanday |
|---|---|---|
| **To'liq oldindan** | Uy chaqiruvi | Hozirgidek. Shifokor yo'lga chiqadi — kafolat kerak |
| **Zaklad (20%)** | Qimmat qabullar | Qolgani klinikada. No-show bo'lsa zaklad qolади |
| **Klinikada to'lash** | Oddiy klinika qabuli | Bron darhol `confirmed`, `payment_mode=at_clinic`. No-show riski bor, lekin konversiya 2–3 barobar yuqori |

`Booking.payment_mode` maydoni + shifokor/klinika sozlamasi (kim qaysi rejimni qabul qiladi). No-show tarixi yomon mijozlar uchun **majburiy oldindan to'lov** (C3 bilan bog'lanadi).

---

## B11. 🟠 Nizolar va chargeback — ⚠️ QISMAN
> ~~`Dispute` modeli, mijoz endpointi, admin navbati (SLA bo'yicha) va hal qilish + refund~~ qilindi. Qoldi: `dispute_rate` shifokor statistikasi (D6/D10).

**Muammo.** "Men to'ladim, shifokor kelmadi" — bu holatni hal qiladigan hech narsa yo'q.

**Yechim.** `Dispute` modeli: `booking_id`, `raised_by`, `reason`, `description`, `status` (open/investigating/resolved_client/resolved_doctor), `resolution_note`, `refund_id`. Admin panelda navbat, SLA (48 soat), va statistika (`dispute_rate` shifokor bo'yicha — yomon shifokorni topish vositasi).

---

## ~~B12. 🟡 Fiskal cheklar~~ ✅
> `Service.mxik_code / package_code / vat_percent` (bo'sh — `settings.FISCAL` default'i), `BookingItem` da SNAPSHOT. `api/payments/fiscal.py`: chek summasi to'lov summasiga teng (zakladda proporsional, qoldiq oxirgi elementga). Payme — `CheckPerformTransaction` javobida `detail.items` (tiyinda); `SetFiscalData` saqlanadi (bor edi). Click — `submit_fiscal_receipts` (har 5 daqiqa, CronJob) `payment/ofd_data/submit_items`, bir marta. ⚠️ MXIK kodi va QQS stavkasi — soliq maslahatchisi bilan (Q-23).

O'zbekistonda onlayn to'lovlar uchun fiskal chek shakllantirish talabi bor. Payme va Click buning uchun API taqdim etadi (`SetFiscalData` yoki shunga o'xshash). Har bir `BookingItem` uchun: nomi, miqdori, narxi, QQS stavkasi, IKPU/MXIK kodi.

> Bu huquqiy talab — aniq hozirgi talablarni soliq maslahatchisi va provayder hujjati bilan tasdiqlang. Men yurist emasman va talablar o'zgarib turadi.

---

## B13. 🟡 To'lov kuzatuvchanligi — ⚠️ QISMAN
> ~~checkout_created, succeeded, duration, provider_errors~~ metrikalari va ~~"0 to'lov" alerti~~ (`k8s/prometheus-rules.yaml`) qilindi. Qoldi: abandoned, refunds, reconciliation metrikalari (Bosqich 1 — B4/B7 bilan).

Qo'shilishi kerak bo'lgan metrikalar:
```
medbron_payment_checkout_created_total{provider}
medbron_payment_succeeded_total{provider}
medbron_payment_duration_seconds{provider}      # checkout → succeeded
medbron_payment_abandoned_total                  # checkout → expired
medbron_refunds_total{reason, status}
medbron_reconciliation_discrepancies_total{type}
medbron_provider_errors_total{provider, code}
```
**Eng muhim alert:** `succeeded` soni soatiga **0 ga tushsa** — to'lov to'liq ishlamayapti. Hozir buni hech narsa sezmaydi.

---

## ✅ To'lov "10/10" ta'rifi

- [x] ~~Checkout endpointi, `Payment` yaratiladi, idempotent (B1)~~
- [ ] ~~Payme protokoli to'liq~~, sandbox testidan o'tgan, ~~CI'da kontrakt testlari~~ (B2) — ⚠️ QISMAN
- [x] ~~Hold ↔ tranzaksiya muddati moslashtirilgan, `CheckPerform` da holat tekshiruvi, hech qachon 500 emas (B3)~~
- [x] ~~Refund hodisa sifatida, retry bilan, qisman refund qo'llab-quvvatlanadi (B4)~~
- [x] ~~Provayder abstraksiyasi: timeout, retry, circuit breaker, to'liq jurnal (B5)~~
- [ ] Click ishlaydi, provayder almashtiriladi (B6)
- [ ] ~~Kunlik reconciliation, alert bilan~~, farqlar doimiy 0 (B7) — ⏳ prodda 7 kun kuzatish
- [x] ~~Komissiya/payout maydonlari, payout oqimi, ikki yozuvli ledger (B8)~~
- [ ] To'liq holat mashinasi hujjatlashtirilgan (B9)
- [ ] `payment_mode`: prepaid / deposit / at_clinic (B10)
- [x] ~~Nizo oqimi va admin navbati (B11)~~
- [ ] Fiskal cheklar qonunga muvofiq (B12)
- [ ] ~~To'lov metrikalari va "0 to'lov" alerti~~ (B13) — ⚠️ QISMAN (refund/reconciliation metrikalari Bosqich 1 da)

---

# C. BIZNES LOGIKA — 5/10

**Nega 5:** Bor qoidalar o'ylangan — lead time, hold TTL, uy chaqiruvi buferi, narx snapshot, `cancelled` va `expired` ni ajratish, bron raqami formati. Bu domenni tushunadigan odamning ishi.

**Nega past:** Bron hayotiy sikli **oxirigacha yopilmagan** — qabul tugaganini belgilaydigan joy yo'q, ya'ni tizim "qabul bo'ldimi?" degan savolga javob bera olmaydi. Bu esa daromad hisobi, reyting, payout, no-show statistikasi — hammasini bloklaydi. Bundan tashqari bekor qilish siyosati, ko'chirish, eslatma, sharh yo'q.

---

## C1. 🔴 Bron sikli yopilmagan: `completed` va `no_show` — ⚠️ QISMAN
> ~~Holatlar + `completed_at/completed_by/no_show_by`~~, ~~`auto_complete_bookings` job~~, ~~admin endpointlari~~ qilindi. Qoldi: shifokor endpointlari (D4, Bosqich 2), mijoz tasdig'i SMS "Qabul bo'ldimi?" (C9, Bosqich 3).

**Muammo** (15-bo'lim, 8-band). `confirmed` dan keyin hech narsa yo'q. Tasdiqlangan bronlar abadiy `confirmed`.

**Nima buziladi:** daromad hisobi ("kelgusi qabul" va "bo'lgan qabul" farqlanmaydi) · shifokorga payout (nima uchun to'laymiz?) · sharh (faqat bo'lgan qabulga) · no-show statistikasi · konversiya funnelining oxiri.

**Yechim — uch manba:**
1. **Shifokor belgilaydi** (asosiy): `POST /doctor/appointments/{id}/complete` yoki `/no-show`. Qabul vaqti boshlangandan keyin faol bo'ladi.
2. **Avtomatik** (zaxira): `auto_complete_bookings` job — qabul tugaganidan 24 soat o'tib shifokor belgilamagan bo'lsa → `completed`. Sababi: shifokorlar unutadi, lekin pul oqimi to'xtamasligi kerak.
3. **Mijoz tasdiqlaydi** (sifat uchun): qabuldan keyin SMS/push "Qabul bo'ldimi? Ha / Yo'q". "Yo'q" javobi → nizo oqimi.

Yangi maydonlar: `completed_at`, `completed_by` (doctor/system/client), `no_show_by` (client/doctor).

**Muhim:** `no_show` ikki xil — **mijoz kelmadi** (pul shifokorga, mijoz reytingiga minus) va **shifokor kelmadi** (to'liq refund + kompensatsiya, shifokor reytingiga jiddiy minus). Bularni bitta holatga yig'manг.

---

## C2. 🔴 Bekor qilish siyosati yo'q — ⚠️ QISMAN
> ~~Vaqt oynalari (sozlamada)~~, ~~`evaluate_cancellation` + 100% test~~, ~~preview endpointi~~, ~~shifokor/admin bekor qilsa 100%~~ qilindi. Qoldi: shifokorga kompensatsiya promo kodi (C12), `doctor_cancellation_rate` (D-bo'limi), jarimaning payout'ga tushishi (B8, Sprint 1.3).

**Muammo.** Hozir istalgan vaqtda, istalgan holatdan, jarimasiz, refundsiz bekor qilinadi. Hatto **o'tib ketgan** qabulni ham.

**Yechim — vaqt oynalari (sozlamada, kodda emas):**

| Qabulgacha qolgan vaqt | Refund | Jarima |
|---|---|---|
| > 24 soat | 100% | yo'q |
| 2–24 soat | 50% | 50% shifokorga (vaqti behuda ketdi) |
| < 2 soat | 0% | to'liq |
| Qabul boshlangandan keyin | bekor qilib bo'lmaydi | — |

**Shifokor bekor qilsa:** har doim 100% refund + mijozga kompensatsiya (keyingi bronga chegirma kodi) + shifokor reytingiga ta'sir. `doctor_cancellation_rate` — shifokorni platformadan chiqarish mezoni.

**Kod tuzilishi:**
```python
@dataclass
class CancellationOutcome:
    allowed: bool
    refund_amount: Decimal
    penalty_amount: Decimal
    reason_code: str

def evaluate_cancellation(booking, actor, now) -> CancellationOutcome
```
Bu sof funksiya — 100% test qamrovi bilan. `cancel_booking` uni chaqiradi va natijaga ko'ra refund yaratadi.

**API'ga qo'shimcha:** `GET /bookings/{id}/cancellation-preview` → mijoz **bekor qilishdan oldin** qancha qaytishini ko'rsin. Bu nizolarning yarmini oldini oladi.

---

## ~~C3. 🔴 Ko'chirish (reschedule) yo'q~~ ✅
> `POST /booking/bookings/{id}/reschedule` + `rescheduled_count`. Tartib: avval yangi slot band qilinadi, keyin eskisi bo'shaydi (teskarisida mijoz ikkala vaqtdan ayrilardi). Oyna C2 bilan bir xil (24 soat), max 2 marta, narx snapshot tegilmaydi. `BookingRescheduled` → mijoz va shifokorga SMS, eslatmalar yangi vaqtga qayta rejalashtiriladi.

**Muammo.** Eng ko'p so'raladigan funksiya butunlay yo'q. Hozir mijoz bekor qilib, qaytadan bron qilishi kerak — pul qaytishini kutib, slot band bo'lib qolish riski bilan.

**Yechim.**
```
POST /bookings/{id}/reschedule
Body: {"new_slot_id": "uuid"}
```
Oqim (bitta tranzaksiyada): yangi slotni band qilish → eski slotni bo'shatish → `Booking.slot` yangilash → `rescheduled_count += 1` → hodisa `BookingRescheduled` → SMS.

**Qoidalar:**
- Faqat `confirmed` va `pending_payment` bronlar
- Qabulgacha > 24 soat (C2 oynasi bilan bir xil)
- Max 2 marta ko'chirish (aks holda slot spekulyatsiyasi)
- Yangi slot **o'sha shifokorniki**, o'sha `place` da
- Narx o'zgarmaydi (snapshot saqlanadi) — agar shifokor narxini oshirsa ham
- Yangi slot qimmatroq xizmat talab qilsa — ko'chirish emas, yangi bron

---

## ~~C4. 🟠 Uy chaqiruvi — masofa va marshrut hisobga olinmaydi~~ ✅
> 1 va 2-bosqich qilindi: `Doctor.home_base_latitude/longitude` (shifokor `PATCH /doctor/profile` da kiritadi) + Haversine (`api/geo.py`) — radiusdan tashqari manzilga 400 "Shifokor bu manzilga bormaydi". Katalogda `GET /catalog/doctors?home_only=1&lat=&lng=`. Dinamik bufer `10 + km×3` daqiqa, [15, 90] oralig'ida; `TimeSlot.travel_buffer_minutes` da saqlanadi va slot bo'shaganda AYNAN shu bufer ochiladi. Baza kiritilmagan shifokorda radius tekshirilmaydi, bufer default 30. `Address.district` qo'shildi (3-bosqich uchun). Marshrut API'si (Yandex/Google) — kelajakda.

**Muammo** (15-bo'lim, 10-band). `home_visit_radius_km` bor, ishlatilmaydi.

**Yechim — uch bosqich:**

**1. Radius (hozir, oson).** Haversine formulasi, 10 qator kod:
```python
if haversine_km(doctor.base_lat, doctor.base_lng, address.lat, address.lng) > doctor.home_visit_radius_km:
    raise InvalidBookingRequest("Shifokor bu manzilga bormaydi")
```
Va katalogda: `GET /doctors?home_only=1&lat=&lng=` — faqat yetib boradiganlar ko'rsatilsin.

**2. Dinamik bufer (keyingi bosqich).** Hozir har doim ±30 daqiqa. Toshkentda 2 km ga 30 daqiqa ko'p, 20 km ga kam. Yechim: masofaga proporsional bufer (`10 daq + masofa × 3 daq/km`), yoki marshrut API'si (Yandex/Google) bilan real vaqt.

**3. Ketma-ket qabullar (kelajak).** Shifokor bir kunda 6 ta uy chaqiruvini shahar bo'ylab tarqoq emas, bitta hududda qilsa samaraliroq. Bu marshrut optimizatsiyasi — birinchi 6 oyda kerak emas, lekin ma'lumot modeli buni bloklamasin (manzilda `district` maydoni bo'lsin).

---

## ~~C5. 🟠 Xizmat davomiyligi va ko'p slotli bron~~ ✅
> Variant A qilindi (sig'masa 400). Variant B (ko'p slotli bron) — kerak bo'lsa keyin.

**Muammo** (15-bo'lim, 3-band ichida). 60 daqiqalik xizmat 30 daqiqalik slotga tushadi.

**Yechim — ikki variant, biznes tanlaydi:**

- **A (oson):** `sum(duration) > slot.duration` → 400 "Bu xizmatlar uchun uzunroq vaqt kerak". Mijoz boshqa slot tanlaydi.
- **B (to'g'ri):** ketma-ket slotlarni birga band qilish. `Booking` ↔ `TimeSlot` **OneToOne emas, ManyToMany** bo'ladi (`BookingSlot` oraliq jadvali). Slot endpointi javobida `max_duration_minutes` qaytaradi — mijoz qancha xizmat sig'ishini oldindan ko'radi.

Variant B to'g'riroq, lekin `Booking.slot` OneToOne'ga bog'langan hamma joyni o'zgartiradi (hold, cancel, expire, confirm). Agar keyinchalik B kerak bo'lishiga ishonchingiz komil bo'lsa — **hozir qiling**, keyin qimmatroq bo'ladi.

---

## ~~C6. 🟠 Bemor va mutaxassislik mosligi tekshirilmaydi~~ ✅
> `Specialization`: `accepts_children` (default yo'q), `min_patient_age`, `max_patient_age`, `allowed_gender`. Yosh QABUL KUNIGA hisoblanadi. Tekshiruv `_validate_and_load_services` ichida (`check_patient_fits`). Mavjud mutaxassisliklarda `accepts_children` ni admin kerak bo'lsa yoqadi.

**Muammo.** `Specialization.is_pediatric` maydoni bor, ishlatilmaydi.

**Ssenariy.** 3 yoshli bola kattalar terapevtiga yoziladi. Yoki 45 yoshli odam pediatrga. Shifokor qabul qilmaydi, mijoz kelgan, vaqt behuda.

**Yechim.**
```python
age = calculate_age(patient.birth_date, slot.start_at)
if specialization.is_pediatric and age >= 18:
    raise InvalidBookingRequest("Bu shifokor bolalar bilan ishlaydi")
if not specialization.is_pediatric and age < 18 and not specialization.accepts_children:
    raise InvalidBookingRequest("Bu shifokor bolalarni qabul qilmaydi")
```
Kengroq qoidalar: ginekolog → faqat `female` (sozlanadigan), yosh chegaralari (`min_patient_age`, `max_patient_age` `Specialization` yoki `Doctor` darajasida).

---

## ~~C7. 🟠 Bir bemor — bir vaqtda ikki joyda~~ ✅

**Muammo.** Bitta bemor uchun bir vaqtga ikkita bron qilish mumkin (turli shifokorlarga).

**Yechim.** `create_booking` da: shu `patient_id` uchun `[start_at, end_at]` oralig'ida faol bron (`pending_payment`/`confirmed`) bormi? Bor bo'lsa — 409 "Bu bemorda shu vaqtda boshqa qabul bor".

Xuddi shunday: bitta mijozda **bir vaqtda max 3 ta `pending_payment`** (A7 bilan bir xil qoida).

---

## ~~C8. 🟠 Vaqt zonasi xatosi — bron raqamida~~ ✅

**Muammo** (15-bo'lim, 13-band). `timezone.now().strftime` UTC beradi. Toshkent vaqti bilan 00:00–05:00 orasida yaratilgan bron **kechagi sana** bilan raqamlanadi.

**Yechim.** `timezone.localtime(timezone.now(), TASHKENT).strftime("%y%m%d")`.

**Umumiy qoida:** loyihada `timezone.now()` dan olingan vaqt **faqat saqlash va solishtirish** uchun. Foydalanuvchiga ko'rinadigan yoki "kun" tushunchasiga bog'liq har qanday hisob — `localtime`. Buni kod review checklistiga qo'shing va `grep -r "now().strftime"` CI tekshiruvi qiling. Bu xato turi "bugungi bronlar", "kunlik daromad", "ish kuni" hisoblarida qaytadan paydo bo'ladi.

---

## ~~C9. 🟠 Eslatmalar yo'q~~ ✅
> `ScheduledNotification` + `run_notification_scheduler` (har daqiqa). T-24, T-2 va T+2 (baholash so'rovi). Ikki himoya qatlami: bron yopilganda qatorlar `cancelled`, yuborish paytida bron holati bazadan qayta tekshiriladi. Shifokor hali yakunlamagan bo'lsa, baholash so'rovi tashlanmaydi — soatiga bir marta qayta ko'riladi (30 soatgacha).

**Muammo.** SMS faqat tasdiqlash, bekor qilish, muddat o'tishida. Qabul haqida eslatma yo'q.

**Ta'siri.** Eslatmasiz no-show darajasi odatda 20–30%, eslatma bilan 10–15% ga tushadi. Bu to'g'ridan-to'g'ri daromad.

**Yechim.** Sizda hodisa infratuzilmasi tayyor — bu arzon qo'shimcha:
```
ScheduledNotification(booking_id, send_at, template, status)
  T-24 soat:  "Ertaga soat 14:00 da Dr. Aliyev qabuli. Manzil: ..."
  T-2 soat:   "2 soatdan keyin qabul. Kechiksangiz oldindan xabar bering."
  T+2 soat:   "Qabul qanday o'tdi? Baholang: <havola>"   (C11)
```
`run_notification_scheduler` har daqiqada `send_at <= now() AND status=pending` ni oladi. Bron bekor qilinsa — kutilayotgan eslatmalar `cancelled` bo'ladi.

---

## ~~C10. 🟠 Navbat (waitlist) — yo'qotilgan talab~~ ✅
> `WaitlistEntry` + `GET/POST /booking/waitlist`, `DELETE /booking/waitlist/{id}`. 409 javobi endi `code`, `alternatives` (3 ta eng yaqin bo'sh vaqt) va `waitlist_available` qaytaradi. Slot bo'shaganda (bekor/muddat o'tishi) navbatdagi **bitta** birinchi odamga SMS; 15 daqiqalik takroriy xabar oynasi, max 3 marta, 2 soatdan kam qolgan vaqtga xabar yo'q. Bron qilgan odam navbatdan avtomat chiqadi.

**Muammo.** `bookings_failed{slot_taken}` metrikasi "talab > taklif" ni ko'rsatadi, lekin bu talab **yo'qoladi**.

**Yechim.** Slot band bo'lsa (409), javobda taklif:
```json
{"detail": "Bu vaqt band", "code": "slot_taken",
 "alternatives": [{"slot_id": "...", "start_at": "..."}],
 "waitlist_available": true}
```
`POST /waitlist {doctor_id, date_from, date_to, place}` → slot bo'shaganda (bekor qilish/muddat o'tishi) navbatdagi birinchi odamga push/SMS: "Dr. Aliyevda 14:00 vaqt bo'shadi, 15 daqiqa ichida bron qiling".

Bu bekor qilingan slotlarni qayta sotadi — sof daromad.

---

## ~~C11. 🟠 Sharh va reyting oqimi yo'q~~ ✅
> `Review` modeli + `api/booking/reviews.py`. `POST /booking/bookings/{id}/review` (faqat `completed`, 30 kun, bitta), `GET /catalog/doctors/{id}/reviews`, shifokor: `GET /doctor/reviews`, `POST /doctor/reviews/{id}/reply` (bir marta), admin: `GET /moderation/reviews`, `POST .../approve|reject`. Avtomatik filtr (haqorat, telefon, havola) → `pending`. Reyting faqat `published` sharhlardan qayta hisoblanadi, `DoctorRatingChanged` (qidiruv indeksi) va `ReviewPublished` (shifokorga Telegram/SMS) outbox orqali. < 5 sharhda katalogda `rating: null`, `is_new: true`. Haqorat lug'ati hozir qisqa — kengaytirish kerak bo'lsa sozlamaga ko'chadi.

**Muammo.** `rating` va `reviews_count` maydonlari bor, ularni to'ldiradigan hech narsa yo'q. Hozir reyting qo'lda kiritiladi — ya'ni yolg'on.

**Yechim.**
```
POST /bookings/{id}/review {rating: 1-5, comment: "", is_anonymous: bool}
```
**Qoidalar:**
- Faqat `completed` bronga (soxta sharhga qarshi tabiiy himoya — sharh yozish uchun to'lash kerak)
- Bitta bronga bitta sharh, 30 kun ichida
- Moderatsiya: avtomatik filtr (haqorat, telefon raqami, reklama) → shubhalilar qo'lda
- Shifokor javob bera oladi (bir marta)
- `Doctor.rating` qayta hisoblanadi (hodisa orqali, real vaqtda emas)
- **Reyting ko'rsatish qoidasi:** < 5 ta sharh bo'lsa reyting ko'rsatilmasin ("Yangi shifokor") — 1 ta 5 ballik sharh 5.0 reyting emas

---

## ~~C12. 🟡 Narx siyosati: chegirma, promo, takroriy qabul~~ ✅
> ~~Promo kod~~, ~~birinchi bron chegirmasi~~, ~~takroriy qabul~~ qilindi (`api/booking/discounts.py`, `PromoCode`/`PromoRedemption`, admin'da boshqariladi). Chegirmalar QO'SHILMAYDI — eng foydalisi. `Booking.original_price / discount_amount / discount_kind / promo_code / discount_borne_by` snapshot. Taqsimot `split_with_discount`: platforma to'lasa shifokor ulushi o'zgarmaydi (farq komissiyadan, kerak bo'lsa manfiy — daftarda marketing xarajati), shifokor to'lasa — odatiy split. Bron bekor/muddati o'tsa promo limiti qaytadi. `POST /booking/price-preview` — to'lovdan oldin tekshirish. 100% chegirma — bron darhol tasdiqlanadi. ~~Qoldi: paketlar, dinamik narx.~~ ✅ **Paket** — `catalog.ServicePackage` (shifokorning kurs taklifi) + `booking.PackageEnrollment/PackageUse`. Mijoz OLDINDAN TO'LAMAYDI: paketga yoziladi, keyingi N ta broni chegirmali (`discount_kind=package`, shifokor ko'taradi). Sababi — `Payment` bronga bog'langan; paketni alohida sotish Payme/Click va refund yuzasini kengaytirardi, ishlatilmagan seans esa qaytariladigan pul emas. Bron bekor bo'lsa seans qaytadi, seanslar tugasa kurs yopiladi; bir xizmatga bitta faol kurs (`uniq_active_package_per_service`). **Dinamik narx** — `catalog.PriceRule` (hafta kuni + soat oralig'i + foiz, manfiy=arzon) va `api/booking/time_pricing.py`. Bu chegirma EMAS: narx SLOT vaqtiga qarab tuzatiladi, `BookingItem.price` snapshot'iga va `original_price` ga kiradi, va faqat shundan keyin chegirma qidiriladi — "arzon soat" ikki marta hisoblanmaydi. `Booking.price_adjust_percent` shaffoflik uchun saqlanadi. `POST /booking/price-preview` endi ixtiyoriy `slot_id` qabul qiladi. Endpointlar: `GET /booking/doctors/{id}/packages`, `POST /booking/packages/{id}/enroll`, `GET /booking/packages/mine`.

Hozir narx qat'iy. Kerak bo'ladi:

| Mexanizm | Nima uchun |
|---|---|
| **Promo kod** | Marketing kampaniyalari, kompensatsiya (C2) |
| **Birinchi bron chegirmasi** | Yangi mijozni jalb qilish — eng qimmat qadam |
| **Takroriy qabul** (`follow_up`) | 14 kun ichida o'sha shifokorga qayta qabul — arzon yoki bepul. Klinikalarda standart amaliyot |
| **Paket** | 5 ta qabul — 10% chegirma (surunkali kasallikda) |
| **Dinamik narx** | Kam talab vaqtlarida (ertalab 08:00) arzonroq — slotlarni to'ldiradi |

`BookingItem` snapshot'i bilan mos ishlashi uchun: `discount_amount`, `promo_code`, `original_price` maydonlari. Chegirmani **kim to'laydi** (platforma yoki shifokor) — `discount_borne_by` maydoni, payout hisobiga ta'sir qiladi (B8).

---

## ~~C13. 🟡 Bildirishnoma kanallari — faqat SMS~~ ✅
> `notification_services.notify()` — mijozga barcha xabarlar (bron, to'lov, eslatma, navbat) endi shu orqali: **push (FCM) > Telegram > SMS**. Tranzaksion xabar albatta yetkaziladi (bepul kanal ishlamasa SMS — foydalanuvchi SMS'ni o'chirgan bo'lsa ham); marketing — faqat `marketing_opt_in` bilan va SMS'siz (rozilik o'zgarishi audit log'da). `NotificationPreference`, `PushDevice` (yaroqsiz token avtomat faolsizlanadi), mijoz Telegram'i — o'sha bot, alohida imzolangan havola (`GET /accounts/me/telegram/link`). `GET/PATCH /accounts/me/preferences`, `POST/DELETE /accounts/me/push-devices`. Shablonlar bazada — `NotificationTemplate` (admin'da, parametrlar tekshiriladi, 60 s kesh), yo'q bo'lsa kod default'i. Push provayderi `PUSH_PROVIDER=console|fcm|off`.

**Muammo.** SMS pullik (~200 so'm/dona), sekin, va uzun matn sig'maydi.

**Yechim — kanal ustuvorligi:**
```
1. Push (ilova o'rnatilgan va ruxsat bergan)  — bepul
2. Telegram bot (raqam bog'langan)            — bepul, O'zbekistonda juda keng
3. SMS (zaxira)                               — pullik
```
`NotificationPreference` modeli: foydalanuvchi qaysi kanalni, qaysi turdagi xabar uchun xohlaydi. Marketing xabarlaridan **voz kechish** imkoniyati majburiy (qonuniy talab).

Shablonlar bazada (`NotificationTemplate`), kodda emas — matnni o'zgartirish uchun deploy kerak bo'lmasin. Har bir shablon: `uz`, `ru` versiyalari (C14).

---

## ~~C14. 🟡 Ko'p tillilik~~ ✅
> `LANGUAGE_CODE='uz'`, `LANGUAGES=[uz, ru]`, `LocaleMiddleware` (`Accept-Language` yoki `?lang=`), javobda `Content-Language`. Django/DRF xabarlari — ularning o'z tarjimasi; bizning biznes xabarlarimiz — `api/i18n.py` katalogi (kalit = o'zbekcha matn, parametrli xabarlar uchun regex), `ApiTranslationMiddleware` 4xx javoblardagi `detail` va maydon xatolarini o'giradi. Test yangi tarjimasiz xabarni ushlaydi. `User.preferred_language` — SMS/Telegram/push shu tilda. Katalog: `Specialization.name_ru`, `Service.name_ru` (shifokor o'zi kiritadi), bo'sh bo'lsa o'zbekcha. Shifokor bio'si va klinika tavsifi — foydalanuvchi matni, bir tilda qoladi.

O'zbekiston bozorida `uz` + `ru` **majburiy**. Hozir hamma narsa o'zbekcha qat'iy kodlangan.

Kerak: `Accept-Language` sarlavhasi bo'yicha javob tili · xato matnlari tarjima qilingan (`django.utils.translation`) · katalog ma'lumotlari ikki tilda (`Specialization.name_uz`, `name_ru`; yoki `django-modeltranslation`) · SMS shablonlari tilga qarab · `User.preferred_language` maydoni.

---

## C15. 🟡 Kichik, lekin ko'rinadigan kamchiliklar

| # | Muammo | Yechim |
|---|---|---|
| ~~1~~ ✅ | ~~`bookings/mine` — 50 ta, paginatsiya yo'q (15-bo'lim, 15-band)~~ | Cursor paginatsiya (20, max 50) + `status` (vergul bilan), `date_from/to` (Toshkent kuni). Javob: `{next, previous, results}` |
| ~~2~~ ✅ | ~~Bemorni o'chirish → 500 (15-bo'lim, 11-band)~~ | `Patient/Address.is_deleted`: bronlari bor — yashiriladi, yo'q — o'chiriladi, faol broni bor — 409. O'chirilgan bemor/manzil yangi bronga tushmaydi |
| ~~3~~ ✅ | ~~Klinika xizmatlari ko'rinmaydi (15-bo'lim, 14-band)~~ | `doctors/{id}/services` → `Service.doctor_id = X OR (Service.clinic_id IN shifokor klinikalari)` |
| ~~4~~ ✅ | ~~Qidiruv yo'q (15-bo'lim, 17-band)~~ | `GET /catalog/search?q=&specialization=&home_only=&lat=&lng=&radius=&min_rating=&sort=`. **ES saralaydi, Postgres ko'rsatadi:** ES dan faqat ID va tartib olinadi, ma'lumot `get_doctors` bilan bazadan to'ldiriladi — projector orqada qolsa eski reyting yoki moderatsiyadan chiqarilgan shifokor ko'rinib qolmaydi (A9). **ES yiqilsa Postgres zaxirasi** (`list_doctors(text=...)`), javobda `engine` maydoni — degradatsiya jim o'tmaydi. Metrika `medbron_search_requests_total{engine}`, alert `MedbronSearchDegraded` |
| ~~5~~ ✅ | ~~"Yaqinimdagi shifokorlar" yo'q~~ | `GET /catalog/doctors?lat=&lng=&radius=` (km, default 5, max 50) — faol klinikasi radiusda, eng yaqini birinchi, javobda `distance_km` |
| ~~6~~ ✅ | ~~Sevimlilar yo'q~~ | `PUT/DELETE /catalog/doctors/{id}/favorite` (idempotent), `GET /catalog/favorites` |
| ~~7~~ ✅ | ~~Bron tarixidan takrorlash yo'q~~ | `GET /booking/bookings/{id}/rebook` — tayyor forma (hozirgi narx, eng yaqin 5 bo'sh vaqt), keyin odatdagi `POST /bookings` |

---

## ✅ Biznes logika "10/10" ta'rifi

- [ ] `completed` / `no_show` — shifokor, avtomatik va mijoz tasdig'i (C1)
- [ ] ~~Bekor qilish siyosati: vaqt oynalari, jarima, preview endpointi~~ (C2) — ⚠️ QISMAN (kompensatsiya promo, cancellation rate)
- [ ] Ko'chirish (reschedule) qoidalar bilan (C3)
- [x] ~~Uy chaqiruvi: radius tekshiruvi + dinamik bufer (C4)~~
- [x] ~~Davomiylik ↔ slot mosligi (yoki ko'p slotli bron) (C5)~~
- [x] ~~Bemor yoshi ↔ mutaxassislik qoidalari (C6)~~
- [x] ~~Bemor va mijoz uchun parallel bron cheklovlari (C7)~~
- [x] ~~Vaqt zonasi: `localtime` qoidasi + CI tekshiruvi (C8)~~
- [ ] Eslatmalar T-24, T-2, T+2 (C9)
- [ ] Navbat (waitlist) + muqobil vaqt takliflari (C10)
- [x] ~~Sharh va reyting — faqat `completed` bronga, moderatsiya bilan (C11)~~
- [ ] Promo, takroriy qabul, paket narxlari (C12)
- [x] ~~Push + Telegram + SMS, shablonlar bazada (C13)~~
- [x] ~~`uz` / `ru` to'liq (C14)~~
- [ ] C15 jadvalining hammasi yopilgan — ⚠️ QISMAN (4-band — matnli qidiruv endpointi — qoldi)

---

# D. SHIFOKOR VA KLINIKA TOMONI — 1/10

**Nega 1:** `Doctor`, `Clinic`, `DoctorAffiliation`, `WorkingRule` modellari mavjud — shuning uchun 0 emas. Lekin **bitta ham endpoint yo'q**. Shifokor o'z jadvalini ko'ra olmaydi, qabullarini bilmaydi, narxini o'zgartira olmaydi. Hammasi `manage.py shell` orqali.

**Nega bu eng jiddiy:** MedBron — **marketplace**. Marketplace'da ikki tomon bor: talab (mijoz) va taklif (shifokor). Sizda taklif tomoni **umuman qurilmagan**. Mijoz tomoni qanchalik yaxshi bo'lmasin, shifokorlarsiz platforma bo'sh. Va shifokorni `shell` orqali qo'shish mumkin bo'lgan holda 50 tadan ortiq shifokorga o'sib bo'lmaydi.

---

## ~~D1. 🔴 Shifokor autentifikatsiyasi va rollar~~ ✅
> `api/roles.py`, `api/permissions.py`, `POST /accounts/auth/switch-role`, matritsa: `docs/ROLLAR_VA_RUXSATLAR.md`.

**Hozir.** OTP verify har doim `role=client` yaratadi. Shifokor kira olmaydi.

**Yechim.**
- OTP oqimi bir xil qoladi, lekin verify javobida **haqiqiy rol** qaytariladi. Raqam `Doctor.user` ga bog'langan bo'lsa → `role=doctor`.
- Bitta odam ikki rolda bo'lishi mumkin (shifokor ham mijoz) → javobda `available_roles: ["client", "doctor"]`, ilova rol tanlash ekranini ko'rsatadi, token'da `active_role`.
- **Ruxsat sinflari:** `IsDoctor`, `IsClinicAdmin`, `IsPlatformAdmin` + obyekt darajasida `IsOwnAppointment`, `IsOwnClinic`.

**Ruxsatlar matritsasi** (hujjatlashtirilishi shart):

| Amal | client | doctor | clinic_admin | platform_admin |
|---|:---:|:---:|:---:|:---:|
| Bron yaratish | ✅ o'ziga | ❌ | ❌ | ✅ (qo'ng'iroq orqali) |
| Bronni ko'rish | ✅ o'zinikini | ✅ o'ziga tegishlini | ✅ klinikasinikini | ✅ hammasini |
| `completed` belgilash | ❌ | ✅ | ✅ | ✅ |
| Jadval o'zgartirish | ❌ | ✅ o'zinikini | ✅ klinikasidagini | ✅ |
| Narx o'zgartirish | ❌ | ✅ shaxsiy xizmatini | ✅ klinika xizmatini | ✅ |
| Shifokorni tasdiqlash | ❌ | ❌ | ❌ | ✅ |
| Refund | ❌ | ❌ | ⚠️ so'rov yubora oladi | ✅ |

---

## ~~D2. 🔴 Shifokor ro'yxatdan o'tishi va moderatsiyasi~~ ✅
> Hujjatlar: fayl turi sehrli baytlar bo'yicha tekshiriladi, asl nom yo'lga tushmaydi, 5 daqiqalik imzolangan havola. Prodda S3/MinIO — `PRIVATE_STORAGE_BACKEND` (QOLDA_BAJARILADIGAN.md, 13-band).

**Hozir.** Shifokor bazaga qo'lda qo'shiladi. Bu 20 ta shifokorda ishlaydi, 200 tada yo'q.

**Yechim — o'z-o'zidan ro'yxatdan o'tish oqimi:**
```
POST /doctor/onboarding/start        telefon + OTP
POST /doctor/onboarding/profile      ism, mutaxassislik, tajriba, ta'lim, tillar
POST /doctor/onboarding/documents    diplom, litsenziya, sertifikat, pasport (fayl)
POST /doctor/onboarding/submit       → status: moderation
GET  /doctor/onboarding/status       → moderation | rejected(sabab) | approved
```
**Moderatsiya holatlari:** `draft → moderation → approved | rejected → (tuzatib qayta) → moderation`. Va `approved → suspended` (shikoyat/litsenziya muddati) `→ approved`.

**Hujjatlar:** S3/MinIO, imzolangan URL, **hech qachon ochiq emas**. Litsenziya amal muddati (`license_expires_at`) saqlanadi va tugashidan 30 kun oldin eslatma, tugaganda avtomatik `suspended`.

**Platforma admin uchun:** moderatsiya navbati, hujjatlarni ko'rish, tasdiqlash/rad etish + sabab, SLA (48 soat).

---

## ~~D3. 🔴 Jadval boshqaruvi — shifokorning asosiy ehtiyoji~~ ✅

**Hozir.** `WorkingRule` faqat `shell` orqali. Shifokor ertaga kasal bo'lsa, hech narsa qila olmaydi — mijozlar kelaveradi.

**Yechim.**

| Endpoint | Vazifa |
|---|---|
| `GET/POST/PATCH/DELETE /doctor/working-rules` | Muntazam jadval: hafta kuni, vaqt oralig'i, klinika, slot uzunligi, amal muddati |
| `GET/POST/DELETE /doctor/time-off` | Ta'til, dam olish, kasallik — sana oralig'i yoki bir necha soat |
| `POST /doctor/slots/{id}/block` | Bitta slotni bloklash ("bu vaqtda majlis") |
| `GET /doctor/schedule?from=&to=` | Kalendar ko'rinishi: slotlar + bronlar birga |
| `POST /doctor/schedule/regenerate` | Qoida o'zgargandan keyin slotlarni qayta yaratish |

**Eng nozik joy — mavjud bronlar bilan to'qnashuv.** Shifokor 15-sentabrga ta'til qo'ysa, lekin o'sha kunda 3 ta tasdiqlangan bron bo'lsa:
```json
409 {
  "detail": "Bu davrda tasdiqlangan qabullar bor",
  "conflicts": [{"booking_number": "MB-...", "start_at": "...", "patient": "A. Karimov"}],
  "options": ["cancel_and_refund", "contact_patients", "choose_other_dates"]
}
```
Bu holatni **hech qachon jim hal qilmang** — shifokor har bir bron bilan nima qilishni o'zi tanlasin.

---

## ~~D4. 🔴 Qabullar ro'yxati va yakunlash~~ ✅

Shifokorning kunlik ish quroli. Busiz `completed`/`no_show` (C1) ham ishlamaydi.

| Endpoint | Vazifa |
|---|---|
| `GET /doctor/appointments/today` | Bugungi qabullar, vaqt bo'yicha. Har birida: bemor ismi, yoshi, jinsi, xizmatlar, joy, manzil (uy chaqiruvida), telefon, izoh |
| `GET /doctor/appointments?from=&to=&status=` | Filtr bilan ro'yxat |
| `GET /doctor/appointments/{id}` | Tafsilot + bemorning oldingi qabullari tarixi |
| `POST /doctor/appointments/{id}/complete` | + ixtiyoriy izoh, tavsiya |
| `POST /doctor/appointments/{id}/no-show` | Mijoz kelmadi |
| `POST /doctor/appointments/{id}/cancel` | Sabab majburiy → to'liq refund + mijozga xabar |
| `POST /doctor/appointments/{id}/start` | Qabul boshlandi (ixtiyoriy — kutish vaqti statistikasi uchun) |

**Maxfiylik chegarasi:** shifokor bemorning **shu bron doirasidagi** ma'lumotini ko'radi. Telefon raqami — faqat qabul kunida (yoki uy chaqiruvida) ko'rinsin, oldindan emas. Bu mijozni platformadan tashqari qo'ng'iroqdan himoya qiladi (bu marketplace uchun asosiy risk — "shifokor bilan to'g'ridan-to'g'ri kelishib olish").

---

## ~~D5. 🔴 Xizmat va narx boshqaruvi~~ ✅

**Hozir.** Narx `shell` orqali. Shifokor narxini oshirolmaydi.

| Endpoint | Vazifa |
|---|---|
| `GET/POST/PATCH/DELETE /doctor/services` | Shaxsiy xizmatlar: nom, tavsif, narx, davomiylik, `place` (clinic/home) |
| `PATCH /doctor/services/{id}/toggle` | Vaqtincha o'chirish (o'chirmasdan) |
| `GET/POST/PATCH /clinic/services` | Klinika xizmatlari (clinic_admin) |

**Muhim qoidalar:**
- Narx o'zgarishi **mavjud bronlarga ta'sir qilmaydi** (`BookingItem` snapshot — bu allaqachon to'g'ri qilingan)
- Narx o'zgarishi tarixi saqlanadi (`ServicePriceHistory`) — analitika va nizolar uchun
- Keskin oshirish (masalan > 50%) → moderatsiya yoki hech bo'lmaganda ogohlantirish
- Xizmatni o'chirish: kelgusi bronlari bo'lsa — o'chirilmaydi, faqat `is_active=False`

---

## ~~D6. 🟠 Daromad va payout paneli~~ ✅
> Haftalik payout (`build_payouts`, min summa sozlamada), admin "to'landi" belgisi + ledger, CSV hisobot (Excel ochadi). PDF — kerak bo'lsa keyin.

Shifokor "qancha ishladim?" degan savolga javob olmasa, platformaga ishonmaydi.

| Endpoint | Vazifa |
|---|---|
| `GET /doctor/earnings/summary?period=` | Bugun / hafta / oy: qabullar soni, jami summa, komissiya, sof daromad |
| `GET /doctor/earnings/transactions` | Har bir bron bo'yicha satr: sana, bemor, xizmat, summa, komissiya, sof |
| `GET /doctor/payouts` | To'lov davrlari: davr, summa, holat, to'langan sana |
| `GET /doctor/payouts/{id}/statement` | PDF/Excel hisobot |

B8 (komissiya va ledger) bu bo'lim uchun old shart. Payout qoidalari (qachon, qanday davriylikda, minimal summa, bank rekvizitlari) shartnomada va sozlamada aniq bo'lsin.

---

## ~~D7. 🟠 Klinika admini~~ ✅
> `api/clinic/` — `/api/v1/clinic/*`. Profil (tavsif, telefon, ish vaqti `working_hours`, foto URL; nom/manzil — moderatsiya orqali). Shifokorlar: `DoctorAffiliation.status` (invited → active | declined; active ↔ paused; → ended) — **ikki tomonlama**: klinika `POST /clinic/doctors/invite {phone}`, shifokor `POST /doctor/clinic-invites/{id}/accept|decline` (Telegram/SMS xabari bilan), `POST /doctor/affiliations/{id}/leave`. To'xtatilganda klinikadagi bo'sh slotlar yopiladi, qaytarilganda ochiladi; faol bronlar bekor qilinmaydi (soni qaytariladi). Jadval: `GET /clinic/schedule?date=` — barcha shifokorlar. Dam olish kunlari `ClinicClosure`: bo'sh slotlar `BLOCKED(clinic_closed)`, generatsiya qilinmaydi, faol bronlar ro'yxati reception uchun qaytadi. Reception: `GET /clinic/appointments?date=&q=` (telefon / ism / bron raqami). Hisobot: daromad, bandlik %, no-show %, bekor qilishlar — jami va shifokorlar kesimida. Har bir amal audit log'da. Klinika fotosini yuklash (fayl) — URL maydoni bor, fayl yuklash keyin.

Klinika platformaga o'z shifokorlarini olib keladi — bu B2B kanal, eng arzon o'sish yo'li. Lekin klinika admini uchun hech narsa yo'q.

| Soha | Endpointlar |
|---|---|
| **Profil** | Klinika ma'lumotlari, manzil, ish vaqti, foto, tavsif |
| **Shifokorlar** | Ro'yxat, taklif yuborish (`POST /clinic/doctors/invite {phone}`), affiliatsiyani faollashtirish/to'xtatish |
| **Jadval** | Klinikadagi barcha shifokorlar jadvali (umumiy ko'rinish), klinika dam olish kunlari |
| **Xizmatlar** | Klinika xizmatlari va narxlari |
| **Qabullar** | Bugungi barcha qabullar (reception uchun), qidiruv (telefon/ism bo'yicha) |
| **Xonalar** | D8 |
| **Hisobot** | Daromad, band slotlar ulushi, no-show darajasi, shifokorlar bo'yicha kesim |

**`DoctorAffiliation` ikki tomonlama tasdiqlash bilan bo'lsin:** klinika taklif qiladi → shifokor qabul qiladi. Aks holda klinika istalgan shifokorni o'ziga "yozib" olishi mumkin.

---

## ~~D8. 🟠 Xona (kabinet) resursi — yashirin cheklov~~ ✅
> `Room(clinic, name, floor, equipment)`, `TimeSlot.room` + `UNIQUE(room, start_at)` (xona bo'lsa), `WorkingRule.room`. Klinika admini xonani ish qoidasiga biriktiradi (`POST /clinic/rules/{id}/room`) — kelajak slotlariga ham qo'llanadi: xona bo'sh bo'lsa ko'chadi, boshqa shifokorda band bo'lsa bo'sh slot yopiladi ("Xona band"), bronli slot `conflicts` da qaytadi. `generate_slots` xona ustma-ustligini ham tekshiradi (UNIQUE faqat bir xil boshlanishni ushlaydi).

**Muammo.** Hozir faqat `UNIQUE(doctor, start_at)` bor. Lekin klinikada **xona** ham cheklangan resurs. 10 shifokor, 4 xona — jadval real emas.

**Yechim.** `Room(clinic, name, floor, equipment)` modeli. Slot yaratishda xona biriktiriladi yoki bron paytida ajratiladi. Cheklov: `UNIQUE(room, start_at)`.

Bu birinchi kundan kerak emas, lekin **ma'lumot modeli buni bloklamasin**. `TimeSlot` ga `room_id` (nullable) maydonini hozir qo'shsangiz, keyin migratsiya og'riqsiz bo'ladi.

---

## D9. 🟠 Shifokorga bildirishnomalar — ⚠️ QISMAN
> ~~Yangi bron, bekor qilindi, kunlik xulosa, to'lov, litsenziya~~ — Telegram → SMS zaxirasi bilan. Qoldi: "Ko'chirildi" (C3 bilan), ~~"Yangi sharh" (C11 bilan)~~ ✅, push (mobil ilova bo'lganda).

Shifokor yangi bron haqida bilmasa, tizim ishlamaydi.

| Hodisa | Xabar |
|---|---|
| Yangi bron | "Ertaga 14:00 — A. Karimov, konsultatsiya" |
| Bekor qilindi | "15-sentabr 14:00 qabul bekor qilindi" |
| Ko'chirildi | "Qabul 16-sentabr 10:00 ga ko'chirildi" |
| Kunlik xulosa | Ertalab 08:00: "Bugun 6 ta qabul, birinchisi 09:00" |
| To'lov | "Haftalik to'lov amalga oshirildi: 2 450 000 so'm" |
| Sharh | "Yangi sharh: 5 ★" |
| Litsenziya | "Litsenziya muddati 30 kundan keyin tugaydi" |

Kanal: push (shifokor ilovasi) > Telegram > SMS. C13 bilan bir xil infratuzilma.

---

## D10. 🟡 Platforma admin paneli

Hozir "admin panel bo'sh". Kerak bo'lgan minimal to'plam:

- **Moderatsiya navbati:** shifokorlar, klinikalar, sharhlar, narx o'zgarishlari
- **Bron operatsiyalari:** qidiruv, holatni qo'lda o'zgartirish (sabab majburiy, auditga tushadi), qo'lda bron yaratish (call-center)
- **To'lov operatsiyalari:** refund boshlash, reconciliation farqlari navbati (B7), nizolar (B11)
- **Foydalanuvchilar:** bloklash, sessiyalarni tozalash, rol berish
- **Kontent:** mutaxassisliklar, bildirishnoma shablonlari, promo kodlar, sozlamalar (komissiya, bekor qilish oynalari)
- **Dashboard:** kunlik bronlar, daromad, konversiya, alertlar

Django admin bunga yetarli — lekin `ModelAdmin` sozlamalari bilan (`list_display`, `list_filter`, `search_fields`, `readonly_fields`, `has_delete_permission=False` moliyaviy modellar uchun) va A12 xavfsizlik talablari bilan.

---

## D11. 🟡 Interfeys strategiyasi — ⚠️ QISMAN
> ~~Shifokor uchun Telegram bot MVP~~ (bog'lash, /today, /tomorrow, ✅/🚫 tugmalar). Mobil ilova / veb panel — mahsulot qarori va frontend ishi (QOLDA_BAJARILADIGAN.md, 16-band).

API — bu ishning yarmi. Qaror qabul qilinishi kerak:

| Foydalanuvchi | Tavsiya |
|---|---|
| **Mijoz** | Mobil ilova (iOS/Android) — bron mobil harakat |
| **Shifokor** | Mobil ilova (kunlik ro'yxat, `completed` belgilash — telefonda qulay) + veb (jadval sozlash) |
| **Klinika admin** | Veb panel (reception kompyuterda ishlaydi) |
| **Platforma admin** | Django admin |

Shifokor uchun eng tez yo'l: **Telegram bot**. Kunlik qabullar, `completed`/`no_show` tugmalari, bekor qilish — bularning hammasi botda ishlaydi va ilova yozishdan 10 barobar arzon. Birinchi 100 shifokor uchun bu yetarli.

---

## ✅ Shifokor/klinika "10/10" ta'rifi

- [x] ~~Shifokor va klinika admini kira oladi, rollar va ruxsatlar matritsasi hujjatlashtirilgan (D1)~~
- [x] ~~O'z-o'zidan ro'yxatdan o'tish + hujjat yuklash + moderatsiya navbati + litsenziya muddati nazorati (D2)~~
- [x] ~~Jadval to'liq boshqariladi, ta'til bronlar bilan to'qnashuvi ochiq hal qilinadi (D3)~~
- [x] ~~Kunlik qabullar ro'yxati, `completed`/`no_show`/`cancel`, maxfiylik chegarasi bilan (D4)~~
- [x] ~~Xizmat va narx boshqaruvi, tarix va snapshot to'g'ri (D5)~~
- [x] ~~Daromad paneli va payout hisobotlari (D6)~~
- [ ] Klinika admini paneli, ikki tomonlama affiliatsiya (D7)
- [ ] Xona resursi modelda hisobga olingan (D8)
- [ ] ~~Shifokorga bildirishnomalar~~ (D9) — ⚠️ QISMAN (ko'chirish/sharh xabarlari C3/C11 bilan)
- [ ] Platforma admin paneli ishlaydi, audit bilan (D10) — Bosqich 4
- [ ] ~~Shifokor uchun Telegram bot~~; har bir rol uchun interfeys qarori (D11) — ⏳ mahsulot qarori

---

# E. ARXITEKTURA — 8/10

**Nega 8:** Bu loyihaning eng kuchli tomoni. Transactional outbox, at-least-once + idempotent consumer'lar, compare-and-set slot hold, `Idempotency-Key`, DTO orqali modul chegaralari, liveness/readiness ning to'g'ri ajratilishi, 400/409 farqi, UUID va UTC konventsiyalari, N+1 dan qochish, k6 SLA. Bularning har biri alohida o'ylangan va sababi hujjatda yozilgan.

**Nega 10 emas:** Skelet to'g'ri, lekin **ishonchlilik qatlami to'liq emas**. Asosiy naqshlar joyida, ammo ular buzilganda buni **hech narsa sezmaydi**. Va ikkinchi muammo — infratuzilma mahsulotdan oldinga ketib ketgan: Kafka, ClickHouse, Elasticsearch, Prometheus bor, lekin refresh token endpointi yo'q.

> Bu bo'limdagi kamchiliklar sinfi boshqacha: A–D dagilar "ishlamaydi" bo'lsa, bu yerdagilar **"jim ishlamay qoladi"**. Aynan shuning uchun ular xavfliroq.

---

## ~~E1. 🔴 Outbox publisher — yagona nuqta, jim yiqiladi~~ ✅

**Muammo.** `run_outbox_publisher` "uzluksiz, 1 nusxa" deb belgilangan. U yiqilsa yoki osilib qolsa: SMS ketmaydi, analitika to'xtaydi, qidiruv indeksi eskiradi — lekin **API mutlaqo sog'lom ko'rinadi**, `healthz/ready` 200 qaytaradi, mijozlar bron qilaveradi.

**Ssenariy.** Juma kuni kechqurun publisher osilib qoladi. Dushanba ertalab 300 ta mijoz "tasdiqlandi" SMS'ini olmagan, ularning yarmi qo'ng'iroq qilgan, ClickHouse'da uch kunlik teshik bor.

**Yechim — ikki qism:**

**1. Ko'p nusxali qilish.** `SELECT ... FOR UPDATE SKIP LOCKED LIMIT N`:
```sql
SELECT * FROM outbox_event
WHERE status = 'pending' AND next_retry_at <= now()
ORDER BY created_at
LIMIT 100
FOR UPDATE SKIP LOCKED;
```
Shunda 3 ta nusxa parallel ishlaydi, bir xil qatorni ikki marta olmaydi. Tartib esa buzilmaydi, chunki Kafka'da partition kaliti `booking_id` — bitta bronning hodisalari bitta partitionda tartibda qoladi.

**2. Yoki bitta nusxa + leader election.** Agar qat'iy global tartib kerak bo'lsa: `pg_try_advisory_lock(OUTBOX_LOCK_ID)` — K8s'da `replicas: 2`, biri ishlaydi, ikkinchisi qulfni kutadi. Yiqilsa 5 soniyada ikkinchisi oladi.

Tavsiya: **SKIP LOCKED**, chunki u sodda va gorizontal masshtablanadi.

---

## E2. 🔴 Outbox lag metrikasi yo'q — eng muhim yetishmayotgan signal — ⚠️ QISMAN
> ~~Outbox metrikalari (lag/pending scrape paytida bazadan) + 4 ta alert~~ qilindi. Qoldi: `medbron_consumer_lag{group}` — Kafka consumer lag eksporteri (kafka-exporter, infratuzilma).

**Muammo.** 13-bo'limdagi metrikalar jadvalida bron, to'lov, saga bor. Outbox yo'q. Ya'ni E1 dagi nosozlikni ko'rsatadigan hech narsa yo'q.

**Yechim.**
```
medbron_outbox_lag_seconds       = now() - min(created_at) WHERE status='pending'
medbron_outbox_pending_total
medbron_outbox_published_total
medbron_outbox_failed_total
medbron_outbox_publish_duration_seconds
```

**Alertlar:**
| Shart | Daraja | Ma'nosi |
|---|---|---|
| `lag > 60s` 5 daqiqa davomida | warning | Publisher sekinlashdi yoki Kafka muammosi |
| `lag > 300s` | critical | Publisher o'lgan |
| `failed_total` o'sishi | critical | Hodisalar butunlay yo'qolmoqda |
| `published_total` 10 daqiqa 0 | critical | Zanjir uzilgan |

Xuddi shu naqsh har bir consumer uchun: `medbron_consumer_lag{group}` — Kafka consumer lag'ini eksport qiling.

---

## ~~E3. 🔴 `failed` hodisalar — DLQ ham, replay ham yo'q~~ ✅

**Muammo.** "10 urinishdan keyin `failed`". Keyin nima? Hech narsa. Hodisa jim yo'qoladi va uni qaytarish yo'li yo'q.

**Yechim.**
1. **Alert:** birinchi `failed` hodisa — darhol xabar. Bu normal holat emas.
2. **Qayta yuborish buyrug'i:**
```bash
python manage.py republish_outbox --event-id <uuid>
python manage.py republish_outbox --since 2026-09-14T00:00 --type BookingConfirmed --dry-run
```
3. **Sabab saqlansin:** `OutboxEvent.last_error`, `attempts`, `next_retry_at`. Aks holda nega yiqilganini bilib bo'lmaydi.
4. **Exponential backoff + jitter:** hozir qanday retry qilinishi aytilmagan. 10 ta urinish ketma-ket 1 soniyada bo'lsa — bu retry emas, DDoS. `2^n` soniya + tasodifiy qo'shimcha.

---

## ~~E4. 🔴 Analitika consumer'ida idempotentlik aytilmagan~~ ✅
> Mavjud ClickHouse jadvalini ko'chirish qo'lda (QOLDA_BAJARILADIGAN.md, 11-band).

**Muammo.** SMS consumer'da `ProcessedEvent(event_id PK)` bor — bu to'g'ri. ClickHouse consumer haqida bunday narsa aytilmagan. At-least-once kafolat + dedup yo'q = **takroriy qatorlar**.

**Ssenariy.** Kafka rebalance (pod qayta ishga tushdi, consumer group o'zgardi) — oxirgi partiya qayta o'qiladi. 1000 ta qator ikki marta yoziladi. `daily_revenue` shishadi. Siz buni 3 oydan keyin, investorga raqam ko'rsatganingizda bilasiz.

**Yechim — ClickHouse'ga xos:**
```sql
CREATE TABLE fact_booking (
    event_id UUID,
    booking_id UUID,
    ...
) ENGINE = ReplacingMergeTree()
ORDER BY (event_id);
```
Va so'rovlarda `FINAL` yoki `GROUP BY event_id`. Muqobil: `ProcessedEvent` naqshini bu yerda ham qo'llash, lekin ClickHouse buning uchun mos emas — `ReplacingMergeTree` to'g'ri yechim.

**Offset commit tartibi ham muhim:** avval yozish, keyin commit. Teskarisida — ma'lumot yo'qoladi (at-most-once).

---

## ~~E5. 🟠 Poison message — bitta buzuq xabar partitionni bloklashi mumkin~~ ✅
> Uchala consumer'da. Yo'l-yo'lakay tuzatildi: notification consumer hodisani SMS'dan OLDIN "ishlangan" deb belgilardi — vaqtinchalik xatoda SMS abadiy yo'qolardi.

**Muammo.** SMS consumer buzuq xabarni o'tkazib yuboradi (yaxshi, hujjatda yozilgan). Analitika va search projector haqida bu aytilmagan.

**Ssenariy.** Payload'da `total_price: null` (bug tufayli). ClickHouse consumer `Decimal` ga o'tkaza olmaydi, exception, qayta urinish, yana exception — cheksiz. Shu partitiondagi **hamma keyingi hodisalar** to'xtaydi.

**Yechim — umumiy consumer bazasi:**
```python
try:
    handle(event)
except SchemaError | ValidationError:
    dead_letter.write(event, error)   # dead_letter_event jadvali
    metrics.poison_total.inc()
    commit()                           # o'tib ketamiz, bloklanmaymiz
except TransientError:
    raise                              # retry qilinsin
```
Farq muhim: **doimiy xato** (schema) → DLQ, **vaqtinchalik xato** (tarmoq, ClickHouse o'chgan) → retry. Ikkalasini bir xil ushlash eng keng tarqalgan xato.

---

## E6. 🟠 Hodisa sxemasi versiyalanmagan

**Muammo.** Konvertda `event_id`, `event_type`, `occurred_at`, `data` bor. `schema_version` yo'q.

**Ssenariy.** Payload'ga `platform_fee` maydonini qo'shdingiz (B8). Yangi API deploy bo'ldi, eski consumer'lar hali ishlayapti. Ular nima qiladi? Agar qat'iy parsing bo'lsa — hammasi yiqiladi. Deploy tartibi muhim bo'lib qoladi, bu esa har safar xavf.

**Yechim.**
1. Konvertga `schema_version: 1` qo'shing.
2. **Qoida:** consumer'lar noma'lum maydonlarga toqat qilishsin (forward compatible), ishlab chiqaruvchilar maydon **o'chirmasin va ma'nosini o'zgartirmasin** (backward compatible). Faqat qo'shish mumkin.
3. Sxemalar repo'da fayl sifatida: `events/schemas/BookingConfirmed.v1.json`. CI'da test: har bir chiqarilgan hodisa o'z sxemasiga mos keladimi.
4. Buzuvchi o'zgarish kerak bo'lsa — `v2` yangi maydon sifatida, ikkalasi bir muddat parallel chiqariladi.

---

## ~~E7. 🟠 Katalog domenida outbox yo'q — projector bo'sh aylanadi~~ ✅
> `api/catalog/events.py`: DoctorApproved, DoctorSuspended, DoctorUpdated, ServicePriceChanged. ~~`DoctorRatingChanged`~~ ✅ (C11). `ClinicUpdated` — D7 yozilganda.

**Muammo** (15-bo'lim, 17-band). `run_search_projector` ishlaydi, lekin `DoctorApproved` kabi hodisalar hech qayerda chiqarilmaydi. Elasticsearch faqat qo'lda `reindex_doctors` bilan to'ldiriladi.

**Yechim.** Bron domenidagi outbox naqshini katalogga ham qo'llang:
- Topic: `catalog.events`, partition kaliti `doctor_id`
- Hodisalar: `DoctorApproved`, `DoctorSuspended`, `DoctorProfileUpdated`, `ServicePriceChanged`, `ClinicUpdated`, `DoctorRatingChanged`
- Bitta `OutboxEvent` jadvali ishlatilsa ham bo'ladi (`topic` maydoni bilan) — alohida jadval shart emas

**Muhim:** bu D2/D5 (shifokor o'z profilini va narxini o'zgartiradi) bilan bir vaqtda qilinsin. Aks holda shifokor narxni o'zgartiradi, qidiruvda eski narx qoladi.

---

## ~~E8. 🔴 Korrektlik cron'ga bog'langan — arxitektura xatosi~~ ✅

**Muammo.** Slotning bo'shashi `expire_bookings` ishlashiga bog'liq. Job 5 daqiqa ishlamasa — bo'sh slotlar band ko'rinadi va sotilmaydi.

**Nega bu arxitektura xatosi:** tizimning **to'g'riligi** fon jarayoniga tayanmasligi kerak. Fon jarayoni faqat **yon ta'sirlar** (SMS, holat yangilash, tozalash) uchun bo'lsin.

**Yechim — lazy expiry.** Hold muddatini **o'qish paytida** hal qiling:
```sql
-- slot bo'sh hisoblanadi:
status = 'free' OR (status = 'held' AND held_until < now())
```
Va `hold_slot` compare-and-set shartiga ham shu kirsin:
```sql
UPDATE time_slot SET status='held', held_until = now() + interval '10 minutes'
WHERE id = %s AND doctor_id = %s
  AND (status = 'free' OR (status = 'held' AND held_until < now()))
RETURNING id;
```
Natija: `expire_bookings` o'chib qolsa ham **slotlar to'g'ri ko'rinadi va to'g'ri sotiladi**. Job faqat bron statusini `expired` qilish va SMS yuborish uchun qoladi — ya'ni kechikishi mumkin, lekin hech narsani buzmaydi.

Bu bitta o'zgarish arxitektura bahosini eng ko'p ko'taradigan nuqta.

---

## ~~E9. 🟠 `expire_bookings` — 1000 ta cheklov va backlog~~ ✅
> Alert qoidasi (`backlog > 5000`) Prometheus'da sozlanishi kerak.

**Muammo.** "Bir siklda max 1000". Agar bir daqiqada 1000 dan ortiq bron muddati o'tsa (kampaniya, hujum, yoki job bir soat o'chib qolgandan keyin) — job hech qachon yetib olmaydi.

**Yechim.**
- `medbron_expire_backlog` metrikasi: qancha qator kutmoqda
- Agar bitta siklda to'liq 1000 ta ishlangan bo'lsa — keyingi siklni **darhol** boshlash (60 soniya kutmasdan)
- Alert: `backlog > 5000`

---

## ~~E10. 🟠 Fon joblarida distributed lock yo'q~~ ✅
> `expire_bookings` va `generate_slots` da. Yangi joblar (reconcile, notification scheduler) yozilganda ham `advisory_lock` qo'shilsin.

**Muammo.** K8s'da ikkita pod bo'lsa yoki CronJob ustma-ust tushsa — `expire_bookings` ikki marta ishlaydi.

**Ssenariy.** Bitta bron uchun ikkita "bekor bo'ldi" SMS'i. Mijoz chalkashadi, siz ikki marta to'laysiz.

**Yechim.**
```python
with advisory_lock("expire_bookings", timeout=0) as acquired:
    if not acquired:
        return          # boshqa nusxa ishlayapti
    run()
```
`pg_try_advisory_lock` — qo'shimcha infratuzilma talab qilmaydi. K8s CronJob uchun qo'shimcha: `concurrencyPolicy: Forbid`.

Xuddi shu qoida `generate_slots`, `reconcile_payments` (B7), `run_notification_scheduler` (C9) uchun.

---

## E11. 🟠 Kesh invalidatsiyasida poyga

**Muammo.** "Slot keshi tozalanadi (commit'dan keyin)". Commit va tozalash orasida boshqa so'rov keladi, eski ma'lumotni o'qiydi va keshni **qayta to'ldiradi**. Natija: band slot bo'sh ko'rinib qoladi, TTL tugagunicha.

**Ssenariy.** Mijoz bo'sh deb ko'rgan slotga bron qiladi → 409. UX buziladi, `bookings_failed{slot_taken}` metrikasi yolg'on o'sadi.

**Yechim — versiya kaliti:**
```
kesh kaliti:  doctor:{id}:slots:{date}:v{version}
version:      Redis INCR, bron tranzaksiyasi bilan birga oshadi
```
Versiya oshgach eski kalit avtomatik yetim qoladi va TTL bilan o'ladi. Invalidatsiya deganda hech narsa o'chirilmaydi — poyga ham yo'q.

Qo'shimcha: TTL qisqa bo'lsin (10–15 soniya). Slotlar uchun "biroz eski" ma'lumot "noto'g'ri" ma'lumotdan yaxshiroq, lekin 5 daqiqalik eski ma'lumot ham yomon.

---

## E12. 🟡 `healthz/ready` keshni va tashqi bog'liqliklarni tekshirmaydi

**Muammo.** Baza va Kafka tekshiriladi. Redis tekshirilmaydi, garchi slot endpointining SLA'si (p95 < 100 ms) unga bog'liq bo'lsa ham.

**Yechim — uch daraja:**
```json
{
  "status": "ready" | "degraded",
  "checks": {
    "database": "ok",              // kritik → 503
    "cache":    "degraded: ...",   // kritik emas, lekin ko'rsatiladi
    "kafka":    "ok"               // kritik emas
  }
}
```
`degraded` holati **alert beradi, lekin podni trafikdan chiqarmaydi**. Hozirgi ikki holatli model (ready / 503) bu nuansni yo'qotadi.

---

## E13. 🟠 Distributed tracing yo'q — nosozlikni topib bo'lmaydi — ⚠️ QISMAN
> ~~Minimal versiya: correlation_id middleware → loglar → outbox → Kafka konverti → consumer; xato javobida `trace_id`; JSON loglar~~ qilindi. Qoldi: to'liq OpenTelemetry + Jaeger/Tempo (ixtiyoriy, infratuzilma).

**Muammo.** "Nega bu mijozga SMS kelmadi?" degan savolga javob berish uchun hozir: API loglari → outbox jadvali → Kafka → consumer loglari → SMS provayder. To'rt joyda, bog'lovchi identifikatorsiz.

**Yechim — minimal versiya (bir kunlik ish):**
1. Middleware har so'rovga `correlation_id` beradi (yoki `X-Correlation-ID` sarlavhasidan oladi)
2. U hodisa konvertiga tushadi
3. Consumer loglarida chiqadi
4. Xato javoblarida mijozga qaytariladi: `{"detail": "...", "trace_id": "abc123"}` — support'ga ayta oladi

**To'liq versiya:** OpenTelemetry + Jaeger/Tempo. Django, psycopg, kafka-python uchun avtomatik instrumentatsiya bor. Lekin `correlation_id` 80% foyda beradi.

**Strukturali log ham shu yerda:** JSON format, majburiy maydonlar `timestamp`, `level`, `correlation_id`, `user_id`, `event`. `print()` va erkin matn loglar qidirib bo'lmaydi.

---

## ~~E14. 🟠 Ma'lumotlar o'sishi rejasi yo'q~~ ✅ (ClickHouse TTL/agregat — alohida band)
> Reja yozildi: **`docs/MALUMOT_OSISHI.md`** — har bir jadval uchun "qancha o'sadi / kim o'chiradi" javobi, va yangi jadval qo'shishdagi qoida. `purge_expired_data` kengaytirildi: o'tgan va **bronga bog'lanmagan** `TimeSlot` (90 kun; bronli slot PROTECT tufayli va tarix uchun qoladi) hamda yopilgan `WaitlistEntry` (`active`/`notified` tegilmaydi). O'chirish **bo'laklab** (5000 qator/tranzaksiya, yurishiga 200 bo'lak) — bitta ulkan `DELETE` jadvalni qulflab, tozalash ishining o'zi prodni to'xtatib qo'yardi. Metrika: `medbron_rows_purged_total{table}` va `medbron_table_rows_estimate{table}` (`pg_class.reltuples`, `COUNT(*)` emas — har scrape'da million qatorni skanerlamaslik uchun). Alertlar: `MedbronPurgeStalled` (cron o'ldi), `MedbronTimeSlotTableLarge` (partitioning haqida o'ylash vaqti). **Partitioning ataylab qilinmadi:** retention jadvalni barqaror hajmda ushlaydi; partitioning 10M qator yoki p95 > 100 ms chegarasidan keyin, DBA qo'li bilan (`QOLDA_BAJARILADIGAN.md`). ClickHouse TTL va `daily_booking_agg` — "agregat jadvallar" bandida.

**Muammo.** `TimeSlot` = shifokorlar × 30 kun × kuniga ~16 slot. 1000 shifokorda oyiga ~500K qator, va eskilar hech qachon o'chirilmaydi. 3 yildan keyin 18 million qator, indekslar sekinlashadi.

**Yechim.**

| Jadval | Strategiya |
|---|---|
| `TimeSlot` | `start_at` bo'yicha oylik partitioning. Eski partitionlar `DETACH` + arxiv yoki `DROP` (90 kundan keyin) |
| `Booking` | Partitioning shart emas (hajmi kichikroq), lekin `created_at` bo'yicha indeks va eski yozuvlarni sovuq saqlashga ko'chirish |
| `OutboxEvent` | `published` holatidagilar 7 kundan keyin o'chiriladi (`cleanup_outbox` job) — aks holda cheksiz o'sadi |
| `ProcessedEvent` | 30 kundan keyin o'chiriladi (dedup oynasidan uzunroq bo'lsa yetarli) |
| `OtpCode` | 24 soatdan keyin o'chiriladi |
| `PaymeCallbackLog` | 1 yil (A8), keyin arxiv |
| ClickHouse | TTL siyosati, agregat jadvallar (`daily_booking_agg`) — xom hodisalarni abadiy skanerlamang |

**Qoida:** har bir jadval yaratilganda "bu qancha o'sadi va eskilarini kim o'chiradi?" savoliga javob yozilsin.

---

## E15. 🟠 Modul chegaralari majburlanmagan

**Muammo.** "View'lar `services.py` dagi DTO qaytaradigan funksiyalarni chaqiradi" — bu ajoyib qoida. Lekin uni **hech narsa majburlamaydi**. Ertaga kimdir shoshilib `from api.catalog.models import Doctor` deb yozadi, code review'da o'tib ketadi, va 6 oydan keyin mikroservisga ajratish imkonsiz bo'ladi.

**Yechim — `import-linter`** (bitta konfiguratsiya fayli, CI'da ishlaydi):
```ini
[importlinter:contract:layers]
name = Domen qatlamlari
type = layers
layers =
    api.booking
    api.payments
    api.schedule
    api.catalog
    api.patients
    api.account

[importlinter:contract:no-cross-models]
name = Domenlar bir-birining modellarini import qilmaydi
type = forbidden
source_modules = api.booking, api.payments
forbidden_modules = api.catalog.models, api.patients.models, api.schedule.models
```
Bu arzon, lekin arxitekturaning eng qimmatli xususiyatini (ajratilishi mumkinligini) asrab qoladi.

---

## E16. 🟡 Deploy va migratsiya strategiyasi

Hujjatda aytilmagan, lekin arxitektura qismi:

- **Zero-downtime migratsiyalar:** maydon qo'shish → to'ldirish → kodni o'zgartirish → eski maydonni o'chirish (to'rt deploy, bitta emas). Ayniqsa `Booking` va `Payment` uchun.
- **Rollback rejasi:** har bir deploy qaytarilishi mumkin bo'lsin. Buzuvchi migratsiya (`DROP COLUMN`) — alohida, keyingi relizda.
- **Consumer va API deploy tartibi:** avval consumer (yangi maydonni tushunadigan), keyin API (yangi maydonni chiqaradigan). E6 bilan bog'liq.
- **Feature flag:** yangi to'lov provayderi, yangi bekor qilish siyosati — flag ortida chiqarilsin, darhol o'chirish mumkin bo'lsin.
- **Muhitlar:** `local` → `staging` (Payme sandbox bilan) → `prod`. Staging'siz to'lov integratsiyasini sinab bo'lmaydi.

---

## E17. 🟡 Test strategiyasi to'liq emas

Hozir: k6 yuk testi faqat slotlar endpointida.

**Kerak bo'lgan qatlamlar:**

| Tur | Nimani qamrab oladi | Maqsad |
|---|---|---|
| Unit | `evaluate_cancellation` (C2), narx hisobi, `haversine` (C4), holat o'tishlari | 100% — bular sof funksiyalar |
| Integration | `create_booking` to'liq oqimi, `hold_slot` poygasi (parallel 10 ta so'rov) | Asosiy oqimlar |
| Kontrakt | Payme sandbox to'plami (B2), Click | Har deployda |
| Consumer | Hodisa → SMS, hodisa → ClickHouse, takroriy hodisa, buzuq hodisa | Idempotentlik va E5 |
| Yuk | Slotlar (bor) + **bron yaratish** (eng muhimi, poyga bor) + katalog | SLA |
| Chaos | Kafka o'chirilgan holda API ishlaydimi? Publisher o'lganda nima bo'ladi? | E1, E12 |

**Eng muhim yetishmayotgani:** `hold_slot` ning parallel testi. Compare-and-set to'g'ri yozilganini faqat shu isbotlaydi.

---

## ✅ Arxitektura "10/10" ta'rifi

- [x] ~~Outbox publisher ko'p nusxada (`SKIP LOCKED`) yoki leader election bilan (E1)~~
- [ ] ~~Outbox~~ va consumer lag ~~metrikalari + alertlar~~ (E2) — ⚠️ QISMAN (Kafka consumer lag eksporteri)
- [x] ~~`failed` hodisalar uchun alert, sabab saqlanadi, `republish` buyrug'i bor (E3)~~
- [x] ~~Barcha consumer'lar idempotent, ClickHouse `ReplacingMergeTree` (E4)~~
- [x] ~~Poison message DLQ'ga, doimiy va vaqtinchalik xato ajratilgan (E5)~~
- [ ] `schema_version` + sxema fayllari + CI testi (E6)
- [x] ~~Katalog domenida outbox, projector real ishlaydi (E7)~~
- [x] ~~**Slot korrektligi cron'ga bog'liq emas** (lazy expiry) (E8)~~
- [x] ~~Expire backlog metrikasi va adaptiv sikl (E9)~~
- [x] ~~Barcha fon joblarida advisory lock (E10)~~
- [ ] Kesh versiya kaliti bilan, poyga yo'q (E11)
- [ ] `ready` uch darajali: ok / degraded / 503 (E12)
- [x] ~~`correlation_id` butun zanjir bo'ylab, strukturali JSON loglar (E13)~~
- [ ] Har bir jadval uchun o'sish va tozalash siyosati (E14)
- [ ] `import-linter` CI'da modul chegaralarini majburlaydi (E15)
- [ ] Zero-downtime migratsiya qoidalari, staging muhiti, feature flag (E16)
- [ ] Test piramidasi to'liq, `hold_slot` parallel testi bor (E17)

---

# BOSQICHMA-BOSQICH YO'L XARITASI

Beshala soha bir-biriga bog'langan. Noto'g'ri tartibda qilsangiz, ishni ikki marta bajarasiz. Quyidagi tartib **bog'liqliklar grafiga** asoslangan, xohishga emas.

## Bog'liqliklar — nima nimadan oldin bo'lishi shart

```
A1,A2,A3 (validatsiya) ──> B1 (checkout) ──> B2 (Payme) ──> B3 (timeout)
                                                              │
E8 (lazy expiry) ─────────────────────────────────────────────┤
                                                              ▼
                                            ISHLAYDIGAN TO'LOV OQIMI
                                                     │
                     ┌───────────────────────────────┼───────────────┐
                     ▼                               ▼               ▼
              C1 (completed)                  B4 (refund)      B5 (provider)
                     │                               │               │
                     ├──> C2 (bekor siyosati) <──────┘               ▼
                     │                                        B6 (Click)
                     ├──> B8 (komissiya) ──> D6 (daromad paneli)
                     │                              ▲
                     └──> C11 (sharh)               │
                                                    │
              D1 (rollar) ──> D2 ──> D3 ──> D4 ─────┘
                                      │
                                      └──> D5 ──> E7 (katalog outbox)
```

**O'qish usuli:** strelka "busiz ishlamaydi" degani. Masalan `D6` (shifokor daromad paneli) `B8` (komissiya maydonlari) siz ma'nosiz — ko'rsatadigan raqam yo'q.

---

## 🩸 BOSQICH 0 — Qon ketishini to'xtatish — ✅ KOD TAYYOR (real muhit tekshiruvlari qoldi)

**Muddat:** 2 hafta · **Maqsad:** mahsulot **ishlaydigan** bo'lsin

Hozir mijoz bron qilib to'lay **olmaydi**. Bu bosqich tugamaguncha boshqa hech narsa qilmang — har qanday yaxshilanish ishlamaydigan mahsulotga qo'shiladi.

### Sprint 0.1 — Bron validatsiyasi (3–4 kun)

| Vazifa | Bo'lim | Nima qilinadi |
|---|---|---|
| ~~Egalik loader'lari~~ ✅ | ~~A1, A4~~ | ~~`get_owned_patient`, `get_owned_address`~~ |
| ~~Slot ↔ shifokor~~ ✅ | ~~A2~~ | ~~`hold_slot(slot_id, doctor_id)` — CAS shartiga qo'shish~~ |
| ~~Xizmat ↔ shifokor ↔ joy ↔ davomiylik~~ ✅ | ~~A3, C5~~ | ~~`_validate_and_load_services` to'liq qayta yozish~~ |
| ~~Vaqt zonasi~~ ✅ | ~~C8~~ | ~~`localtime` + CI grep qoidasi~~ |

**Bitta PR.** Bo'lib yubormang — bular bitta funksiyaning bitta muammosi.

**Tayyorlik mezoni:** begona `patient_id` bilan bron → 400; boshqa shifokorning sloti → 400; 60 daqiqalik xizmat 30 daqiqalik slotga → 400; barcha holatlar uchun test bor.

### Sprint 0.2 — Slot korrektligi (2 kun)

| Vazifa | Bo'lim |
|---|---|
| ~~Lazy expiry — `held_until < now()` o'qish paytida~~ ✅ | ~~E8~~ |
| ~~Advisory lock fon joblarida~~ ✅ | ~~E10~~ |
| ~~Expire backlog metrikasi~~ ✅ | ~~E9~~ |

**Tayyorlik mezoni:** `expire_bookings` ni 1 soatga o'chirib qo'ying — slotlar hamon to'g'ri ko'rinadi va to'g'ri sotiladi. Bu testni qo'lda o'tkazing.

### Sprint 0.3 — To'lov oqimi (5–6 kun)

| Vazifa | Bo'lim | Nima qilinadi |
|---|---|---|
| ~~Checkout endpointi~~ ✅ | ~~B1~~ | ~~`POST /payment/bookings/{id}/checkout`~~ |
| ~~Payme to'liq protokoli~~ ✅ | ~~B2~~ | ~~`CreateTransaction`, `CheckTransaction`, `GetStatement`~~ |
| ~~Timeout mosligi~~ ✅ | ~~B3~~ | ~~Hold uzaytirish + `CheckPerform` da holat + fail-safe~~ |
| ~~Callback jurnali~~ ✅ | ~~A8~~ | ~~`PaymeCallbackLog`~~ |
| ~~To'lov metrikalari~~ ✅ | ~~B13~~ | ~~`succeeded_total` + "0 to'lov" alerti~~ (`k8s/prometheus-rules.yaml`) |

**Tayyorlik mezoni:** Payme **sandbox to'plami to'liq o'tadi** va CI'da avtomatik ishlaydi. Bu muzokara qilinmaydigan shart.

### Sprint 0.4 — Sessiya (2 kun)

| Vazifa | Bo'lim |
|---|---|
| ~~`TokenRefreshView` ulash~~ ✅ | ~~A6~~ |
| ~~Logout + logout/all~~ ✅ | ~~A6~~ |
| ~~Idempotency-Key foydalanuvchiga bog'lash~~ ✅ | ~~A5~~ |

**Bosqich 0 natijasi:** mijoz OTP bilan kiradi → shifokor topadi → bron qiladi → **to'laydi** → SMS oladi → 15 daqiqadan keyin qayta OTP so'ramaydi. Ya'ni mahsulot mavjud bo'ladi.

---

## 💰 BOSQICH 1 — Pul mantiqini yopish — ✅ KOD TAYYOR (prod mezonlari QOLDA_BAJARILADIGAN.md da)

**Muddat:** 3 hafta · **Maqsad:** har bir so'm hisobga olinsin

Bosqich 0 dan keyin pul **kiradi**, lekin qaytmaydi, hisoblanmaydi va tekshirilmaydi.

### ~~Sprint 1.1 — Bron siklini yopish (4 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~`completed` / `no_show` holatlari va o'tishlari~~ ✅ | ~~C1~~ |
| ~~`auto_complete_bookings` job~~ ✅ | ~~C1~~ |
| ~~Vaqtinchalik endpoint (D4 gacha): `POST /bookings/{id}/complete` admin uchun~~ ✅ | ~~C1~~ |

> `completed` holati **hamma narsaning kaliti**: payout, sharh, daromad hisobi, no-show statistikasi. Uni kechiktirmang.

### ~~Sprint 1.2 — Bekor qilish va refund (6 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~`evaluate_cancellation` sof funksiyasi + 100% test~~ ✅ | ~~C2~~ |
| ~~`cancellation-preview` endpointi~~ ✅ | ~~C2~~ |
| ~~Provayder abstraksiyasi (timeout, retry, circuit breaker)~~ ✅ | ~~B5~~ |
| ~~`Refund` modeli + `RefundRequested` hodisasi + worker~~ ✅ | ~~B4~~ |

### ~~Sprint 1.3 — Hisob va nazorat (5 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Komissiya maydonlari + snapshot~~ ✅ | ~~B8~~ |
| ~~`LedgerEntry` ikki yozuvli daftar~~ ✅ | ~~B8~~ |
| ~~Kunlik `reconcile_payments` + `PaymentDiscrepancy`~~ ✅ | ~~B7~~ |
| ~~Nizo modeli~~ ✅ | ~~B11~~ |

**Tayyorlik mezoni:** kunlik reconciliation ishlaydi va **farqlar soni 0**. Nolga teng bo'lmasa — bosqichni tugallanmagan deb hisoblang.

### ~~Sprint 1.4 — Ishonchlilik qatlami (4 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Outbox lag metrikasi + alertlar~~ ✅ | ~~E2~~ |
| ~~`SKIP LOCKED` publisher (ko'p nusxa)~~ ✅ | ~~E1~~ |
| ~~`failed` alerti + `republish_outbox`~~ ✅ | ~~E3~~ |
| ~~ClickHouse `ReplacingMergeTree`~~ ✅ | ~~E4~~ |
| ~~Poison message → DLQ~~ ✅ | ~~E5~~ |
| ~~`correlation_id` butun zanjirda~~ ✅ | ~~E13~~ |

> Qo'shimcha topilma: loyihada Prometheus `/metrics` endpointi umuman yo'q edi — barcha metrikalar hech qayerga chiqmasdi. `/healthz/metrics` qo'shildi.

**Bosqich 1 natijasi:** pul oqimi yopiq, tekshiriladigan va kuzatiladigan. Shifokorga qancha to'lash kerakligi aniq. Nosozlik jim o'tmaydi.

---

## 👨‍⚕️ BOSQICH 2 — Taklif tomonini qurish — ✅ KOD TAYYOR (prod mezonlari QOLDA_BAJARILADIGAN.md da)

**Muddat:** 4 hafta · **Maqsad:** `manage.py shell` dan butunlay voz kechish

Bu bosqichgacha platforma 20–30 shifokordan ortiq o'sa olmaydi.

### ~~Sprint 2.1 — Kirish va onboarding (5 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Rollar, ruxsat sinflari, ruxsatlar matritsasi~~ ✅ | ~~D1~~ |
| ~~Ko'p rolli akkaunt (`available_roles`, `active_role`)~~ ✅ | ~~D1~~ |
| ~~Onboarding oqimi + hujjat yuklash (S3, imzolangan URL)~~ ✅ | ~~D2~~ |
| ~~Moderatsiya navbati (admin) + litsenziya muddati nazorati~~ ✅ | ~~D2~~ |

> Qo'shimcha: A9 (tasdiqlanmagan shifokor 404) va E7 ning bir qismi (`DoctorApproved`/`DoctorSuspended` hodisalari) shu sprintda qilindi.

### ~~Sprint 2.2 — Jadval (6 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~`WorkingRule` CRUD~~ ✅ | ~~D3~~ |
| ~~Ta'til / dam olish / bitta slotni bloklash~~ ✅ | ~~D3~~ |
| ~~Kalendar ko'rinishi~~ ✅ | ~~D3~~ |
| ~~**Bronlar bilan to'qnashuvni ochiq hal qilish** (409 + variantlar)~~ ✅ | ~~D3~~ |

> Yo'l-yo'lakay tuzatildi: (1) uy chaqiruvi bekor qilinganda qo'shni BLOCKED slotlar sababidan qat'i nazar ochilib ketardi — endi `block_reason` bor; (2) qoida o'zgarganda yangi slotlar band slot bilan ustma-ust yaratilishi mumkin edi.

> To'qnashuv oqimini jim hal qilmang — bu shifokor ishonchini yo'qotadigan joy.

### ~~Sprint 2.3 — Qabullar va xizmatlar (6 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Bugungi qabullar, filtr bilan ro'yxat, tafsilot~~ ✅ | ~~D4~~ |
| ~~`complete` / `no_show` / `cancel` (shifokor)~~ ✅ | ~~D4, C1~~ |
| ~~Maxfiylik chegarasi (telefon faqat qabul kunida)~~ ✅ | ~~D4~~ |
| ~~Xizmat va narx boshqaruvi + narx tarixi~~ ✅ | ~~D5~~ |
| ~~Katalog outbox + projector (narx o'zgarishi qidiruvga tushsin)~~ ✅ | ~~E7~~ |

> Qo'shimcha: klinika admini uchun klinika xizmatlari endpointlari va C15.3 (klinika xizmatlari shifokor katalogida ko'rinadi) shu sprintda qilindi.

### ~~Sprint 2.4 — Daromad va bildirishnomalar (4 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Daromad paneli, payout davrlari, hisobot~~ ✅ | ~~D6~~ |
| ~~Shifokorga bildirishnomalar (yangi bron, bekor, kunlik xulosa)~~ ✅ | ~~D9~~ |
| ~~Telegram bot MVP (tez yo'l)~~ ✅ | ~~D11~~ |

> Yo'l-yo'lakay tuzatildi: onboarding'da litsenziya muddati UTC sanasi bilan tekshirilardi (C8 xatosining takrori) — `today_local()` ga o'tkazildi va `timezone.localdate()` CI to'sig'iga qo'shildi.

**Bosqich 2 natijasi:** shifokor mustaqil ishlaydi. Platforma 500 shifokorgacha o'sishi mumkin.

---

## ⭐ BOSQICH 3 — Sifat va konversiya

**Muddat:** 3 hafta · **Maqsad:** no-show tushsin, konversiya o'ssin, ishonch qurilsin

### ~~Sprint 3.1 — Konversiya (5 kun)~~ ✅

| Vazifa | Bo'lim | Kutilayotgan ta'sir |
|---|---|---|
| ~~Eslatmalar T-24, T-2~~ ✅ | ~~C9~~ | No-show 20–30% → 10–15% |
| ~~Ko'chirish (reschedule)~~ ✅ | ~~C3~~ | Bekor qilishning bir qismini saqlab qoladi |
| ~~Navbat (waitlist) + muqobil vaqt takliflari~~ ✅ | ~~C10~~ | Bo'shagan slotlarni qayta sotadi |
| ~~`payment_mode` (klinikada to'lash)~~ ✅ | ~~B10~~ | Konversiya 2–3 barobar |

### ~~Sprint 3.2 — Ishonch (5 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Sharh va reyting oqimi + moderatsiya~~ ✅ | ~~C11~~ |
| ~~Uy chaqiruvi radiusi (Haversine) + dinamik bufer~~ ✅ | ~~C4~~ |
| ~~Bemor yoshi ↔ mutaxassislik qoidalari~~ ✅ | ~~C6~~ |
| ~~Parallel bron cheklovlari~~ ✅ | ~~C7, A7~~ (throttle qismi — 3.3) |

### ~~Sprint 3.3 — Xavfsizlikni yopish (5 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Throttle barcha yozuv endpointlarida~~ ✅ | ~~A7~~ |
| ~~`approved` bo'lmagan shifokor 404~~ ✅ (oldin qilingan) | ~~A9~~ |
| ~~PII: analitikada telefon yo'q, retention siyosati~~ ✅ | ~~A10~~ |
| ~~Audit log~~ ✅ | ~~A11~~ |
| ~~Admin 2FA + IP cheklov~~ ✅ | ~~A12~~ |
| ~~Infratuzilma checklisti, CI'da SAST~~ ✅ (kod/CI; infra — qo'lda) | ~~A13~~ |
| ~~OTP verify limiti, faqat `+998`~~ ✅ | ~~A14~~ |

### ~~Sprint 3.4 — Kichik, lekin ko'rinadigan (4 kun)~~ ✅

| Vazifa | Bo'lim |
|---|---|
| ~~Paginatsiya, filtrlar~~ ✅ | ~~C15.1~~ |
| ~~Bemor soft-delete~~ ✅ | ~~C15.2~~ |
| ~~Klinika xizmatlari ko'rinishi~~ ✅ (oldin qilingan) | ~~C15.3~~ |
| ~~"Yaqinimdagi", sevimlilar, "yana bron qilish"~~ ✅ | ~~C15.5–7~~ |

**Bosqich 3 natijasi:** mahsulot raqobatbardosh. Mijoz qaytib keladi.

---

## 🏗️ BOSQICH 4 — Masshtab va bozor

**Muddat:** davomiy · **Maqsad:** o'sishga tayyorlik

| Blok | Vazifalar |
|---|---|
| **To'lov** | ~~Click (B6)~~ ✅, ~~fiskal cheklar (B12)~~ ✅, ~~promo, paketlar va dinamik narx (C12)~~ ✅ |
| **B2B** | ~~Klinika admini paneli (D7)~~ ✅, ~~xonalar (D8)~~ ✅ |
| **Bozor** | ~~Ruscha til (C14)~~ ✅, ~~push + Telegram (C13)~~ ✅ |
| **Ma'lumot** | ~~Partitioning va arxivlash (E14)~~ ✅ (retention + o'sish metrikasi; partitioning — chegaradan keyin, DBA), ~~agregat jadvallar~~ ✅ (`daily_booking_agg` + `fact_booking` TTL), ~~Elasticsearch qidiruvi (C15.4)~~ ✅ (Postgres zaxirasi bilan) |
| **Muhandislik** | `import-linter` (E15), `schema_version` (E6), `ready` degraded (E12), kesh versiya kaliti (E11), deploy qoidalari (E16), test piramidasi (E17) |
| **Admin** | Platforma admin paneli (D10) |

---

# BALLAR QANDAY O'SADI

| Bosqich | Xavfsizlik | To'lov | Biznes logika | Shifokor tomoni | Arxitektura | **Umumiy** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Hozir** | 5 | 3 | 5 | 1 | 8 | **6** |
| 0 dan keyin | 7 | 6 | 6 | 1 | 9 | **7** |
| 1 dan keyin | 7 | 9 | 7 | 2 | 9 | **7.5** |
| 2 dan keyin | 8 | 9 | 7 | 7 | 9 | **8.5** |
| 3 dan keyin | 10 | 9 | 9 | 8 | 9 | **9** |
| 4 dan keyin | 10 | 10 | 10 | 10 | 10 | **10** |

> Umumiy baho oddiy o'rtacha emas — to'lov va xavfsizlik ko'proq vazn oladi, chunki ulardagi xato qaytarib bo'lmaydigan zarar keltiradi.

---

# HAR BOSQICH UCHUN "TAYYOR" MEZONI

Bosqichni tugallandi deb hisoblash uchun **hammasi** bajarilgan bo'lishi kerak:

> **Belgilar:** ✅ `[x]` — bajarilgan va avtomat test bilan isbotlangan · ⏳ `[ ]` — kod tayyor, lekin mezon faqat real muhitda (kalit, telefon, prod ma'lumoti, vaqt) tekshiriladi — [`QOLDA_BAJARILADIGAN.md`](QOLDA_BAJARILADIGAN.md)
>
> | Bosqich | Bajarilgan | Qolgan (real muhit) |
> |---|:---:|:---:|
> | 0 | 2 / 5 | 3 |
> | 1 | 1 / 4 | 3 |
> | 2 | 2 / 3 | 1 |
> | 3 | 0 / 3 | boshlanmagan |
> | 4 | 0 / 3 | boshlanmagan |

**Bosqich 0:**
- [ ] ⏳ Payme sandbox to'plami CI'da yashil — kontrakt to'plami (`pytest -m payme_sandbox`, 25 test) va `.github/workflows/ci.yml` tayyor; haqiqiy test.paycom.uz sinovi uchun merchant kalitlari kerak (Q-2)
- [ ] ⏳ Real telefon bilan to'liq oqim o'tkazilgan: OTP → bron → to'lov → SMS — avtomat oqim testi yashil (`apps/utils/test_e2e_flow.py`); real telefon bilan qo'lda sinov kerak (Q-3)
- [ ] ⏳ `hold_slot` 100 tomonli parallel testi PostgreSQL'da yashil — Postgres paroli kerak; CI'da avtomat ishlaydi (Q-1)
- [x] ✅ `expire_bookings` o'chirilgan holda slotlar to'g'ri ishlaydi — `test_cron_ishlamasa_ham_muddati_otgan_hold_qayta_sotiladi`
- [x] ✅ Begona ID bilan bron qilish barcha yo'llarda bloklangan — `apps/booking/test_validation.py` + CI to'sig'i `apps/utils/test_guards.py`

**Bosqich 1:**
- [ ] ⏳ Reconciliation 7 kun ketma-ket **0 farq** bilan ishladi — `reconcile_payments` + testlar tayyor; prodda 7 kun kuzatish (Q-9)
- [ ] ⏳ Refund oqimi real pul bilan sinovdan o'tdi (test rejimida) — refund oqimi testlangan; Payme bilan real sinov (Q-6)
- [x] ✅ Ledger balansi bazadagi summalar bilan mos — har to'lov/refund/payout ikki yozuvli, `trial_balance() == 0` testlarda; kunlik `reconcile_payments` `ledger_imbalance` va `missing_ledger` ni avtomat tekshiradi (`apps/payment/test_money.py`)
- [ ] ⏳ Publisher qo'lda o'ldirilganda alert 60 soniyada keldi — `MedbronOutboxPublisherDead` qoidasi va `/healthz/metrics` tayyor; prodda sinov (Q-12)

**Bosqich 2:**
- [x] ✅ Yangi shifokor `shell` ga tegmasdan ro'yxatdan o'tdi va birinchi bronini qabul qildi — OTP → onboarding → moderatsiya → jadval → xizmat → mijoz broni va to'lovi → yakunlash → payout, bitta testda (`apps/utils/test_e2e_doctor_flow.py`)
- [x] ✅ Shifokor ta'til qo'yganda mavjud bronlar to'g'ri hal qilindi — 409 + to'qnashuvlar, `cancel_and_refund` (100% refund) / `keep_bookings`, rollback (`apps/schedule/test_doctor_schedule.py`)
- [ ] ⏳ Narx o'zgarishi qidiruvda 1 daqiqada aks etdi — `ServicePriceChanged` → outbox → projector testlangan; vaqtni Kafka + Elasticsearch bilan o'lchash kerak

**Bosqich 3:**
- [ ] No-show darajasi eslatmalardan oldingi ko'rsatkichdan past
- [ ] Reyting real sharhlardan hisoblanadi, qo'lda kiritilmaydi
- [ ] Tashqi penetration test o'tkazilgan, kritik topilma yo'q

**Bosqich 4:**
- [ ] Ikki to'lov provayderi parallel ishlaydi
- [ ] Klinika o'z shifokorlarini o'zi boshqaradi
- [ ] Ikki tilda to'liq ishlaydi

---

# UCHTA UMUMIY QOIDA

**1. Har bir tuzatilgan kamchilik uchun uni qaytarmaydigan to'siq qo'ying.**
A4 → loader qoidasi + CI grep · C8 → `localtime` CI tekshiruvi · B2 → sandbox testlari · E15 → `import-linter` · E17 → parallel `hold_slot` testi. Aks holda 6 oydan keyin xuddi shu ro'yxatni qayta yozasiz.

**2. Infratuzilmani mahsulotdan oldinga qo'ymang.**
Hozirgi holatning asosiy sababi shu: Kafka, ClickHouse, Elasticsearch, Prometheus bor, lekin refresh token endpointi yo'q va mijoz to'lay olmaydi. Bosqich 4 gacha yangi infratuzilma **qo'shmang** — bori yetadi.

**3. "Jim ishlamay qolish" eng xavfli nosozlik turi.**
Yiqilgan API'ni hamma ko'radi. Ishlamayotgan publisher, takrorlangan analitika qatorlari, yo'qolgan tranzaksiya — bularni hech kim ko'rmaydi. Shuning uchun E2 (lag metrikasi), B7 (reconciliation) va B13 ("0 to'lov" alerti) — bu uchtasi funksional talablardan kam emas.
