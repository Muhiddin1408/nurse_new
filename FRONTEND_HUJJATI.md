# MedBron — Frontend / Mobile ishlab chiqish hujjati

> Kimga: mobil (mijoz, shifokor) va web (klinika, moderatsiya) ishlab chiquvchilarga.
> Backend holati: `api/v1.py` — 8 modul, ~110 endpoint, 4 rol.
> Interaktiv API: `http://127.0.0.1:8000/api/docs/` (Swagger), `/api/schema/` (OpenAPI JSON).
> Biznes qoidalar: `API_HUJJATI.md`, arxitektura: `LOYIHA_HUJJATI.md`.

---

## Mundarija

1. [Nima quriladi — 4 ta klient](#1-nima-quriladi--4-ta-klient)
2. [Texnologiya tanlovi](#2-texnologiya-tanlovi)
3. [Umumiy qoidalar (hamma klient uchun majburiy)](#3-umumiy-qoidalar-hamma-klient-uchun-majburiy)
4. [Mijoz ilovasi (mobile)](#4-mijoz-ilovasi-mobile)
5. [Shifokor ilovasi (mobile)](#5-shifokor-ilovasi-mobile)
6. [Klinika paneli (web)](#6-klinika-paneli-web)
7. [Moderatsiya paneli (web)](#7-moderatsiya-paneli-web)
8. [Umumiy komponentlar kutubxonasi](#8-umumiy-komponentlar-kutubxonasi)
9. [Ish tartibi va sprintlar](#9-ish-tartibi-va-sprintlar)
10. [Definition of Done](#10-definition-of-done)
11. [Backenddan kutilayotgan ishlar](#11-backenddan-kutilayotgan-ishlar)

---

## 1. Nima quriladi — 4 ta klient

| # | Klient | Platforma | Rol (`active_role`) | Ekran/sahifa | Prioritet |
|---|---|---|---|---|---|
| 1 | **Mijoz ilovasi** | iOS + Android (mobile) | `client` | ~32 | P0 |
| 2 | **Shifokor kabineti** | mobile (yoki responsive web) | `doctor` | ~22 | P1 |
| 3 | **Klinika paneli** | web (desktop) | `clinic_admin` | ~12 | P1 |
| 4 | **Moderatsiya paneli** | web (desktop) | `platform_admin` | ~10 | P2 |

> **Muhim:** 2–4 bir monorepo'da bo'lishi mumkin, lekin **bitta ilovaga tiqilmaydi**.
> Mijoz ilovasi store'da alohida turadi — unga shifokor kodi tushmasligi kerak (bundle hajmi + xavfsizlik).
> Klinika va moderatsiya paneli esa bitta web loyihada, rol bo'yicha route guard bilan bo'lishi mumkin.

---

## 2. Texnologiya tanlovi

### Mobil (tavsiya)

```
Flutter 3.x  yoki  React Native (Expo)
├─ State:    Riverpod / Bloc        |  TanStack Query + Zustand
├─ Network:  Dio + interceptor      |  axios + interceptor
├─ Kod gen:  openapi-generator      |  openapi-typescript
├─ Saqlash:  flutter_secure_storage | expo-secure-store   ← token SHU YERDA
├─ Push:     FCM + APNs
└─ Karta:    Google/Yandex Maps (uy chaqiruvi manzili uchun)
```

### Web panel (tavsiya)

```
React 18 + TypeScript + Vite
├─ Routing: React Router v6 (rol bo'yicha guard)
├─ Data:    TanStack Query (cache + invalidation)
├─ Form:    React Hook Form + Zod
├─ UI:      Ant Design (admin panel uchun tezroq) yoki shadcn/ui
└─ Jadval:  TanStack Table (server-side filtr)
```

### API klientni QO'LDA yozmang

Backend `drf-spectacular` bilan OpenAPI schema beradi:

```bash
curl http://127.0.0.1:8000/api/schema/ -o openapi.yaml
npx openapi-typescript openapi.yaml -o src/api/types.ts
```

Har sprint boshida qayta generatsiya qiling. Tip mos kelmasa build sinadi — bu **yaxshi**.

---

## 3. Umumiy qoidalar (hamma klient uchun majburiy)

### 3.1 Base URL va modullar

```
BASE = https://<host>/api/v1
/accounts  /booking  /catalog  /clinic  /doctor
/moderation  /patient  /payment  /schedule  /telegram
```

`BASE` ni `.env` dan oling (dev / staging / prod). Hech qachon kodga yozmang.

### 3.2 Auth oqimi (OTP → JWT)

```
1. POST /accounts/auth/otp/request   {phone}
   → 200 {"detail": "Kod yuborildi"}    ← raqam bor-yo'qligi OSHKOR QILINMAYDI
   → 429 limit (10/soat, IP bo'yicha)   → taymer ko'rsating
   → 503 SMS provayder yo'q             → "birozdan keyin urinib ko'ring"

2. POST /accounts/auth/otp/verify    {phone, code}
   → 200 {
       "tokens": {"access": "...", "refresh": "..."},
       "is_new_user": true|false,
       "user": {"id","phone","full_name","role",
                "available_roles": ["client","doctor"],
                "active_role": "client"}
     }
```

**Klient mantiqi:**

- `is_new_user == true` → "Ismingiz?" ekrani → bemor profilini yaratish.
- `available_roles.length > 1` → **rol tanlash ekrani**.
- Rol almashtirish: `POST /accounts/auth/switch-role {role, refresh}` → **yangi token juftligi**, eski refresh bekor bo'ladi. Ya'ni rol almashganda butun cache tozalanadi.

### 3.3 Token boshqaruvi — interceptor pattern

```
Har so'rovga:  Authorization: Bearer <access>

401 kelsa:
  ├─ refresh band bo'lsa → navbatga qo'y (bir vaqtda 5 ta 401 normal hol)
  ├─ POST /accounts/auth/token/refresh {refresh}
  ├─ muvaffaqiyat → so'rovni QAYTA yubor (faqat 1 marta)
  └─ xato → storage tozala → login ekraniga
```

- `access` faqat xotirada + secure storage'da. `localStorage` — **taqiqlanadi**.
- Chiqish: `POST /accounts/auth/logout {refresh}` (204). Hamma qurilmadan: `/auth/logout/all`.

### 3.4 Xato kodlari — har biriga alohida UX

| Kod | Ma'no | Klient nima qiladi |
|---|---|---|
| 400 | So'rov xato (format yoki biznes qoidasi) | Maydon ostida xato matni. **Qayta yuborish foydasiz** |
| 401 | Token eskirgan | Sokin refresh → qayta urinish |
| 403 | Rol/akkaunt ruxsat bermaydi | "Ruxsat yo'q" ekrani + logout |
| 404 | Yo'q **yoki begona** | "Topilmadi" — egalik oshkor qilinmaydi |
| 409 | **Siz to'g'ri, lekin kimdir ulgurdi** (slot band) | Alohida ekran: "Bu vaqt band bo'ldi" + slotlarni **avtomatik qayta yuklash** |
| 429 | Limit | Taymer + tugmani bloklash |
| 503 | Tashqi servis yo'q | "Birozdan keyin" + retry |

> **400 va 409 ni aralashtirmang.** 400 — formani tuzating. 409 — boshqa vaqt tanlang.
> Xato formati: `{"detail": "..."}` yoki maydon bo'yicha `{"phone": ["..."]}`.
> Ikkalasini ham parse qiladigan bitta `parseApiError()` yozing.

### 3.5 Idempotency (pul yo'qotmaslik uchun)

Bron yaratishda **majburiy**:

```
POST /booking/bookings
Headers: Idempotency-Key: <uuid v4, 1–64 belgi>
```

- Kalit **ekran ochilganda** generatsiya qilinadi, tugma bosilganda emas.
- Retry'da **o'sha kalit** yuboriladi → backend eski bronni qaytaradi, ikkinchisini yaratmaydi.
- Payload o'zgarsa → `409 "Idempotency key reused with different payload"` → yangi kalit generatsiya qiling.
- To'lov ham idempotent: `POST /payment/bookings/{id}/checkout`.

### 3.6 Vaqt zonasi — eng ko'p xato qilinadigan joy

- API **UTC** (ISO 8601) qaytaradi: `2026-09-24T09:00:00Z`.
- Foydalanuvchiga **Asia/Tashkent** da ko'rsatiladi.
- "Bugun" tushunchasi ham Tashkent bo'yicha (kalendar, jadval).
- Qoida: **qo'lda +5 qilmang** — `intl` / `date-fns-tz` kabi kutubxonadan foydalaning.

### 3.7 Pul

- `Decimal` string keladi: `"150000.00"`. **Float'ga aylantirmang.**
- Ko'rsatish: `150 000 so'm`.
- Payme/Click bilan tiyin (×100) almashuvi — backend ishi, klient aralashmaydi.

### 3.8 Chegirma ko'rsatish

`BookingSerializer` beradi: `total_price`, `original_price`, `discount_amount`, `discount_kind`, `promo_code`.
`original_price != null` bo'lsa — eski narxni **ustidan chizib** ko'rsating, yonida chegirma sababi.

### 3.9 Til (i18n)

`uz` (lotin) — asosiy, `ru`, `en` — keyin. Backend `status_display` kabi tayyor matn qaytaradi,
lekin **unga tayanmang**: klientda o'z lug'atingiz bo'lsin, mashina kodi (`status`) bo'yicha map qiling.
Sozlash: `GET/PUT /accounts/me/preferences`.

### 3.10 Push va bildirishnoma

```
Qurilma tokeni:   POST /accounts/me/push-devices
Telegram ulash:   POST /accounts/me/telegram/link
```

- FCM tokeni login'dan **keyin** va har yangilanganda yuboriladi.
- Logout'da token o'chiriladi (aks holda begona odam bildirishnoma oladi).
- Push kelganda deep link: `medbron://booking/<uuid>`.

### 3.11 Ro'yxatlar

Filtrlar query param orqali: `status`, `from`, `to`, `q`, `limit/offset`.
Infinite scroll + pull-to-refresh. Har ro'yxatda **empty state majburiy** — rasm + tushuntirish + harakat tugmasi.

### 3.12 Holatlar

Har ekranda 4 holat: `loading` (skeleton) / `success` / `empty` / `error` (retry bilan).
Spinner o'rniga **skeleton** — kutish qisqaroq tuyuladi.

---

## 4. Mijoz ilovasi (mobile)

### 4.1 Navigatsiya

```
Splash → (token bor?) → Tab bar
                      ↘ (yo'q) → Auth flow

Tab bar: [Bosh sahifa] [Qidiruv] [Bronlarim] [Profil]
```

### 4.2 Auth (4 ekran)

| Ekran | Endpoint | Eslatma |
|---|---|---|
| Onboarding / Splash | — | 3 slayd, bir marta |
| Telefon kiritish | `POST /accounts/auth/otp/request` | Mask `+998 (__) ___-__-__` |
| OTP kiritish | `POST /accounts/auth/otp/verify` | 6 xona, SMS avto-o'qish, 60s taymer |
| Rol tanlash | `POST /accounts/auth/switch-role` | Faqat `available_roles.length > 1` bo'lsa |

### 4.3 Katalog va qidiruv (9 ekran)

| Ekran | Endpoint |
|---|---|
| Bosh sahifa | `GET /catalog/specializations`, `GET /catalog/doctors?limit=10` |
| Qidiruv | `GET /catalog/search?q=&specialization=&clinic=&min_rating=&sort=&radius=` |
| Mutaxassisliklar | `GET /catalog/specializations` |
| Klinikalar | `GET /catalog/clinics?city=` |
| Shifokorlar ro'yxati | `GET /catalog/doctors?specialization=&clinic=` |
| Shifokor kartochkasi | `GET /catalog/doctors/{id}` |
| Xizmatlar | `GET /catalog/doctors/{id}/services?place=clinic|home` |
| Sharhlar | `GET /catalog/doctors/{id}/reviews` |
| Sevimlilar | `GET /catalog/favorites`, `POST /catalog/doctors/{id}/favorite` |

**Qidiruv UX:** debounce 300ms, filtrlar bottom-sheet'da, `sort=relevance|rating|price|distance`.
`radius` faqat geolokatsiya ruxsati bo'lsa yuboriladi — bo'lmasa yubormang (backend default beradi).

### 4.4 Bron qilish oqimi — eng muhim qism (4 ekran)

```
Shifokor kartochkasi → "Bron qilish"
   │
   ▼
1. Xizmat tanlash     GET /catalog/doctors/{id}/services?place=
   │                  (place: klinikada / uyda — segment control)
   ▼
2. Sana + slot        GET /schedule/doctors/{id}/slots?date=YYYY-MM-DD
   │                  (faqat BO'SH slotlar keladi: {id, start_at, end_at})
   ▼
3. Bemor + manzil     GET /patient/patients/   (uy chaqiruvi → /patient/addresses/)
   │
   ▼
4. Narxni ko'rsatish  POST /booking/price-preview
   │   {patient_id, doctor_id, slot_id, service_ids[], address_id?, promo_code?}
   ▼
5. Bron               POST /booking/bookings  + Idempotency-Key
   │   → 201 {id, number, status: "pending_payment", total_price, ...}
   │   → 409 slot band → 2-qadamga qayt, slotlarni qayta yukla
   ▼
6. To'lov             POST /payment/bookings/{id}/checkout {provider: "payme"|"click"}
   │   → {payment_id, checkout_url, amount, expires_at}
   │   → checkout_url WebView / tashqi brauzerda ochiladi
   ▼
7. Natija             GET /booking/bookings/{id} ni POLLING (3s, max 60s)
                      status == "confirmed" → muvaffaqiyat animatsiyasi
```

> **To'lov natijasini `checkout_url` redirect'idan bilmang.** Haqiqat manbai — backend webhook'i.
> Klient faqat `GET /booking/bookings/{id}` statusiga ishonadi; Payme/Click deep-link qaytmasligi mumkin.
>
> `expires_at` — to'lov kutish muddati. Ekranda **teskari taymer** ko'rsating.
> Muddat tugasa status `expired` bo'ladi → "Vaqt tugadi, qayta bron qiling".

### 4.5 Bronlarim (6 ekran)

| Ekran | Endpoint |
|---|---|
| Ro'yxat (tab: Faol / Tarix) | `GET /booking/bookings/mine?status=` |
| Bron tafsiloti | `GET /booking/bookings/{id}` |
| Bekor qilish | preview → `POST /booking/bookings/{id}/cancel` |
| Qayta bron | `POST /booking/bookings/{id}/rebook` |
| Sharh yozish | `POST /booking/bookings/{id}/review` |
| Nizo ochish | `POST /booking/bookings/{id}/disputes` |

**Status → UI (`Booking.Status`):**

| status | Badge | Ko'rinadigan tugmalar |
|---|---|---|
| `pending_payment` | sariq + taymer | To'lash, Bekor qilish |
| `confirmed` | ko'k | Bekor qilish, Reschedule, Yo'nalish (xarita) |
| `completed` | yashil | Sharh yozish, Qayta bron, Nizo ochish |
| `cancelled` | kulrang | Qayta bron |
| `expired` | kulrang | Qayta bron |
| `no_show` | qizil | Nizo ochish |

**Bekor qilishda:** avval cancellation preview'ni chaqirib **jarima va qaytariladigan summani ko'rsating**,
keyin "Rostdan bekor qilasizmi?" dialogi. Jarimani ko'rsatmasdan bekor qilish — support'ga shikoyat manbai.

**Nizo:** `reason` faqat 4 qiymat — `doctor_no_show`, `poor_service`, `wrong_charge`, `other`.
Dropdown qiling, erkin matn emas. `due_at` (ko'rib chiqish muddati) ni ham ko'rsating.

### 4.6 Paketlar va navbat (3 ekran)

| Ekran | Endpoint |
|---|---|
| Shifokor paketlari | `GET /booking/doctors/{id}/packages` |
| Mening paketlarim | `GET /booking/packages/mine` → qolgan seanslar progress bar |
| Navbat (waitlist) | `POST /booking/waitlist`, `DELETE /booking/waitlist/{id}` |

Waitlist: slot yo'q bo'lganda "Bo'shasa xabar bering" tugmasi → push kelganda deep link bilan bron ekraniga.

### 4.7 Profil (6 ekran)

| Ekran | Endpoint |
|---|---|
| Profil bosh sahifasi | — |
| Bemorlar (CRUD) | `/patient/patients/` (DRF router: list/create/retrieve/update/delete) |
| Manzillar (CRUD) | `/patient/addresses/` + kartada pin |
| Sozlamalar | `GET/PUT /accounts/me/preferences` |
| Telegram ulash | `POST /accounts/me/telegram/link` → `t.me/...` deep link |
| Akkauntni o'chirish | `POST /accounts/me/delete` |

> **Bemor ≠ foydalanuvchi.** Bitta akkaunt bir nechta bemor (bola, ota-ona) uchun bron qiladi.
> Bron ekranida bemor tanlash **majburiy qadam**, default — o'zi.
>
> **Akkauntni o'chirish** 409 qaytishi mumkin (faol bron bor) → "Avval faol bronlarni yakunlang".
> Ikki bosqichli tasdiq talab qiling.

---

## 5. Shifokor ilovasi (mobile)

Prefiks: `/api/v1/doctor/`. Kirish — **xuddi shu OTP**, lekin `active_role = "doctor"`.

### 5.1 Onboarding (5 ekran)

```
POST   /doctor/onboarding/start
  ▼
PUT    /doctor/onboarding/profile        (ism, mutaxassislik, tajriba, bio)
  ▼
POST   /doctor/onboarding/documents      (diplom, litsenziya — multipart)
DELETE /doctor/onboarding/documents/{id}
  ▼
POST   /doctor/onboarding/submit         → status: pending
  ▼
GET    /doctor/onboarding/status         → draft|pending|approved|rejected|suspended
```

**Har statusga alohida UI (`Doctor.Status`):**

| status | Ekran |
|---|---|
| `draft` | "Anketani to'ldiring" + davom etish |
| `pending` | "Moderatsiyada, 1–2 kun" + progress, **tahrirlash bloklangan** |
| `approved` | To'liq kabinet |
| `rejected` | **Rad sababi ko'rsatiladi** + "Tuzatib qayta yuborish" (→ `pending`) |
| `suspended` | "Faoliyat to'xtatilgan" + support kontakti, faqat o'qish rejimi |

Hujjat yuklash: rasm kompressiyasi (max ~2MB), progress bar.
Ko'rish: `GET /doctor/documents/download/{token}` (token vaqtinchalik).

### 5.2 Kundalik ish (7 ekran)

| Ekran | Endpoint |
|---|---|
| **Bugun** (bosh ekran) | `GET /doctor/appointments/today` |
| Qabullar ro'yxati | `GET /doctor/appointments?from=&to=&status=` |
| Qabul tafsiloti | `GET /doctor/appointments/{id}` |
| Boshlash | `POST /doctor/appointments/{id}/start` |
| Yakunlash | `POST /doctor/appointments/{id}/complete {note?}` |
| Kelmadi | `POST /doctor/appointments/{id}/no-show {absent: "client"|"doctor", note?}` |
| Bekor qilish | `POST /doctor/appointments/{id}/cancel` |

> **"Bugun" — shifokor kuniga 20 marta ochadigan ekran.** Unga eng ko'p vaqt sarflang:
> vaqt chizig'i (timeline), hozirgi qabul ajratilgan, keyingisigacha qolgan vaqt,
> har kartada 1 bosishda `start`/`complete`, mijozga qo'ng'iroq tugmasi.

### 5.3 Jadval (5 ekran)

| Ekran | Endpoint |
|---|---|
| Kalendar | `GET /doctor/schedule?from=&to=&resolution=` |
| Ish qoidalari | `GET/POST /doctor/working-rules`, `PUT/DELETE /doctor/working-rules/{id}` |
| Ta'til / dam | `GET/POST /doctor/time-off`, `/doctor/time-off/{id}` |
| Slot bloklash | `POST /doctor/slots/{id}/block` \| `/unblock` |
| Jadvalni qayta yaratish | `POST /doctor/schedule/regenerate` |

> **Ish qoidasi o'zgarsa slotlar avtomatik qayta yaratilmaydi** — `regenerate` chaqirish kerak.
> UI: qoida saqlangach "Jadvalni yangilash" banner'i chiqsin → bosilganda `regenerate` + kalendar refresh.
> Regenerate uzoq davom etishi mumkin — progress ko'rsating.

### 5.4 Xizmat va narx (3 ekran)

`GET/POST /doctor/services`, `PUT/DELETE /doctor/services/{id}`,
`POST /doctor/services/{id}/toggle`, `GET /doctor/services/{id}/price-history`.

Narx tarixi — grafik. Narx o'zgartirishda "Bu faqat yangi bronlarga ta'sir qiladi" ogohlantirishi.

### 5.5 Moliya (5 ekran)

| Ekran | Endpoint |
|---|---|
| Daromad | `GET /doctor/earnings/summary?period=&from=&to=` |
| Tranzaksiyalar | `GET /doctor/earnings/transactions` |
| To'lovlar (payout) | `GET /doctor/payouts` |
| Payout hisoboti | `GET /doctor/payouts/{id}/statement` (PDF / share) |
| To'lov rekviziti | `GET/PUT /doctor/payout-account` |

Daromad ekrani: davr tanlagich (hafta / oy / o'z), ustunli grafik, "kutilayotgan / to'langan" ajratilgan.
Rekvizit o'zgartirish sezgir amal — OTP bilan qayta tasdiq so'rang.

### 5.6 Sharh va klinika (4 ekran)

`GET /doctor/reviews`, `POST /doctor/reviews/{id}/reply`,
`GET /doctor/clinic-invites`, `POST /doctor/clinic-invites/{affiliation_id}/{accept|decline}`,
`POST /doctor/affiliations/{id}/leave`, `GET/PUT /doctor/profile`.

> **Klinika taklifi ikki tomonlama:** klinika taklif qiladi → shifokor qabul qiladi.
> `invited → active | declined`, `active ↔ paused`, `→ ended`.
> Taklif kelganda push + kabinet ustida badge.

---

## 6. Klinika paneli (web)

Prefiks: `/api/v1/clinic/`. Rol: `clinic_admin`.
Ko'p endpoint `clinic_id` (query yoki body) bilan ishlaydi — bitta admin bir nechta klinikani boshqarishi mumkin.
Shuning uchun yuqori panelda **klinika tanlagich (switcher)** bo'lsin, tanlov global state'da saqlansin.

| Sahifa | Endpoint | Tarkib |
|---|---|---|
| Dashboard / Profil | `GET/PUT /clinic/profile` | Ma'lumot, logo, manzil |
| Shifokorlar | `GET /clinic/doctors` | Jadval: ism, mutaxassislik, affiliation status |
| Taklif qilish | `POST /clinic/doctors/invite` | Telefon bo'yicha |
| Affiliation amallari | `POST /clinic/doctors/{affiliation_id}/{action}` | pause / resume / end |
| Jadval | `GET /clinic/schedule` | Shifokorlar bo'yicha kunlik grid |
| Yopiq kunlar | `GET/POST /clinic/closures`, `/{id}` | Bayram, ta'mir |
| Qabullar | `GET /clinic/appointments?day=&q=` | Kunlik ro'yxat + qidiruv |
| Xonalar | `GET/POST /clinic/rooms`, `/{id}` | Raqam, sig'im |
| Qoidalar | `GET/POST /clinic/rules`, `POST /clinic/rules/{id}/room` | Qoidani xonaga biriktirish |
| Xizmatlar | `GET/POST /clinic/services`, `/{id}` | Klinika darajasidagi xizmatlar |
| Hisobotlar | `GET /clinic/reports` | Grafiklar + CSV eksport |

**UX qoidalari:**

- Jadval ko'rinishi — shifokorlar ustun, vaqt qator (gantt uslubi). Band slot rangli, bo'sh — och.
- Har amal (pause / end) — **tasdiqlash dialogi**, chunki jadvalga ta'sir qiladi.
- Qoida → xona biriktirishda konflikt (bir xona ikki qoidaga) darhol qizil bilan ko'rsatilsin.

---

## 7. Moderatsiya paneli (web)

Prefiks: `/api/v1/moderation/`. Rol: `platform_admin`.

| Sahifa | Endpoint |
|---|---|
| Shifokorlar navbati | `GET /moderation/doctors?status=pending` |
| Shifokor tafsiloti | `GET /moderation/doctors/{id}` + hujjatlar |
| Tasdiqlash / rad | `POST .../approve`, `POST .../reject` |
| To'xtatish / tiklash | `POST .../suspend`, `POST .../reinstate` |
| Sharhlar moderatsiyasi | `GET /moderation/reviews`, `POST /{id}/approve|reject` |
| Payout'lar | `GET /moderation/payouts?status=pending`, `POST /{id}/mark-paid` |
| Nizolar | `GET /booking/disputes` |
| Nizoni yechish | `POST /booking/disputes/{id}/resolve {in_favor_of, note, refund_amount}` |
| Bronni majburiy yopish | `POST /booking/bookings/{id}/complete` \| `/no-show` |

**UX qoidalari:**

- Shifokor tafsiloti — **ikki ustun**: chapda anketa, o'ngda hujjat viewer (zoom, aylantirish). Moderator ikkalasini bir vaqtda ko'rsin.
- Rad etishda **sabab majburiy** — shifokor uni `rejected` ekranida ko'radi.
- Nizo yechishda `refund_amount` bron summasidan oshmasligini klientda ham tekshiring.
- `mark-paid` — qaytarib bo'lmaydigan amal → summani takror yozdirib tasdiqlating.
- Kim qanday amal qilgani ko'rinsin (backend audit yozadi, siz ko'rsating).

---

## 8. Umumiy komponentlar kutubxonasi

Bularni **birinchi sprintda** yozing — keyin har ekranda qayta ishlatiladi:

| Komponent | Nima qiladi |
|---|---|
| `ApiClient` | Base URL, Bearer, 401 refresh navbati, retry, xato normalizatsiyasi |
| `parseApiError()` | `{detail}` va `{field: [...]}` ni bitta shaklga keltiradi |
| `<StatusBadge status>` | Bron / shifokor / affiliation statuslari → rang + matn |
| `<Money value>` | Decimal string → `150 000 so'm` |
| `<DateTime value>` | UTC → Asia/Tashkent, "Bugun 14:00" kabi nisbiy format |
| `<SlotPicker>` | Sana + vaqt tanlash, 409 da avto-refresh |
| `<PhoneInput>` | `+998` maskasi, validatsiya |
| `<OtpInput>` | 6 xona, avto-focus, SMS avto-o'qish |
| `<AsyncView>` | loading / empty / error / success 4 holati |
| `<ConfirmDialog>` | Xavfli amallar uchun |
| `useIdempotencyKey()` | Ekran ochilganda UUID, retry'da o'sha |

---

## 9. Ish tartibi va sprintlar

### Sprint 0 — poydevor (1 hafta)

- Repo, CI, lint, `.env` (dev / staging / prod)
- OpenAPI'dan tip generatsiyasi + skript
- `ApiClient` + auth interceptor + secure storage
- Design token'lar (rang, shrift, spacing) + 8-bo'limdagi komponentlar
- **Mock server** (OpenAPI'dan: Prism / MSW) — backend kutmasdan ishlash uchun

### Sprint 1 — mijoz: auth + katalog (2 hafta)

Auth 4 ekran, katalog 9 ekran. **Chiqish mezoni:** shifokorni topib, kartochkasini ochib bo'ladi.

### Sprint 2 — mijoz: bron + to'lov (2 hafta) — eng xavfli sprint

Bron oqimi 4 ekran + to'lov + polling + idempotency + 409 ishlovi.
**Chiqish mezoni:** Payme sandbox bilan uchdan uchiga real bron.

### Sprint 3 — mijoz: bronlarim + profil (1.5 hafta)

6 + 6 ekran, push, deep link.

### Sprint 4 — mijoz: paket / waitlist + polish (1 hafta)

Animatsiya, empty state, i18n, crash reporting (Sentry), store'ga chiqarish.

### Sprint 5–6 — shifokor ilovasi (3 hafta)

Onboarding → kundalik ish → jadval → moliya.

### Sprint 7 — klinika paneli (2 hafta)

### Sprint 8 — moderatsiya paneli (1.5 hafta)

> **Parallel ishlash:** mobil va web jamoalari Sprint 1 dan keyin ajralib ketadi.
> Umumiy komponentlar va `ApiClient` faqat Sprint 0 da bir marta kelishiladi.

---

## 10. Definition of Done

Ekran "tayyor" deyilishi uchun:

- [ ] 4 holat ishlaydi: loading (skeleton) / success / empty / error
- [ ] Kutilayotgan xato kodlari (400/401/403/404/409/429/503) alohida ishlanadi
- [ ] Vaqtlar Asia/Tashkent da, pul Decimal'dan — hech qanday float yo'q
- [ ] Offline'da crash bo'lmaydi, retry tugmasi bor
- [ ] Katta shrift va kichik ekran (320px) da buzilmaydi
- [ ] Analitika hodisasi yuborilgan (ekran ochildi, asosiy tugma bosildi)
- [ ] Token yo'q holatda ochilsa login'ga yo'naltiradi — oq ekran qolmaydi
- [ ] Yozuv amallari (POST/PUT/DELETE) ikki marta bosilganda bitta so'rov yuboradi
- [ ] Ro'yxatlarda sahifalash + pull-to-refresh
- [ ] `TODO` va hardcode'langan URL / token qolmagan

**Release DoD:** Sentry ulangan, versiya va build ko'rinadi, majburiy yangilanish mexanizmi bor,
maxfiylik siyosati linki bor (store talabi), akkauntni o'chirish ekrani bor
(Apple talabi — backendda bor: `/accounts/me/delete`).

---

## 11. Backenddan kutilayotgan ishlar

Frontend boshlashdan oldin backend jamoasi bilan **yozma ravishda** kelishing:

1. **Sahifalash formati** — hamma ro'yxat endpointi bir xil shaklda javob bersinmi (`{count, next, previous, results}`)? Hozir modulga qarab farq qiladi.
2. **`price-preview` javob shakli** — chegirma, jarima, yakuniy summa qaysi maydonlarda.
3. **Cancellation preview** — bekor qilish jarimasi qaysi endpointdan olinadi (`CancellationPreviewSerializer` kodda bor, URL'ini tasdiqlang).
4. **Fayl yuklash limiti** — hujjat hajmi va formatlari (`onboarding/documents`).
5. **Push payload sxemasi** — `type`, `booking_id`, deep link formati.
6. **WebSocket / SSE bormi** — yo'q bo'lsa to'lov va "Bugun" ekrani polling'da qoladi (interval kelishilsin).
7. **`/api/schema/` prod'da yopiq** (`SWAGGER_ENABLED`) — CI uchun staging schema URL'i kerak.
8. **Media / CDN** — shifokor avatari, klinika logosi qaysi URL'dan olinadi.

> Bular aniqlanmaguncha mock server bilan ishlang, lekin **Sprint 2 dan oldin 2-, 3-, 5-band majburiy hal qilinsin** —
> ular bron va to'lov oqimini to'g'ridan-to'g'ri bloklaydi.
