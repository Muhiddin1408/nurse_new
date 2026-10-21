# Runbook: asosiy domenga o'tish (3-bosqich cutover)

Maqsad: katalog, jadval, narx/promo/paketlar va bronlar monolitdan catalog-, schedule-, pricing- va
booking-service'ga o'tadi (ADR-0011). Identity (2-bosqich) allaqachon ko'chgan bo'lishi shart — bronlardagi
`client_id`, `patient_id`, `address_id` o'sha UUID'lar.

Kutilgan to'xtash: **yozish uchun qisqa muzlatish** (≈ 10–15 daqiqa, ma'lumot hajmiga bog'liq). Identity'dan
farqi: bron, slot va promo limitlari ikki joyda bir vaqtda yozilsa invariantlar buziladi (bitta slotda ikki bron,
promo limiti oshib ketadi). O'qish (katalog, bo'sh vaqtlar) to'xtamaydi.

## 0. Tayyorgarlik (oldindan, ish vaqtida)
1. To'rtala servis deploy qilingan: `alembic upgrade head` (catalog, pricing, booking), schedule —
   `migrations/*.up.sql` (servis migrate konteyneri), `/health/ready` = 200.
2. **schedule-scheduler hali ishga TUSHIRILMAGAN** (`docker compose up -d schedule-service schedule-relay
   schedule-consumer` — scheduler'siz). Aks holda u ko'chirishdan oldin slot generatsiya qiladi va monolit
   slotlari `UNIQUE(doctor_id, start_at)` da to'qnashib tushib qoladi (hisobotda `inserted < plan`).
3. Kong'da yangi `/v1/catalog|schedule|pricing|bookings...` yo'llari bor, lekin frontend hali `/api/*` da.
4. Dry-run — sonlarni ko'zdan kechiring, `skipped` bo'limini o'qing (pastda):
   ```bash
   pip install -r scripts/migrate/requirements.txt
   python scripts/migrate/core.py --dry-run --source "$MONOLITH_DATABASE_URL"
   ```

## 1. Yozishni muzlatish (monolit)
1. Monolitda bron/katalog/jadval yozish yo'llarini o'chiring (maintenance: Kong'da `/api/v1/booking`,
   `/api/v1/catalog`, `/api/v1/schedule`, `/api/v1/doctor`, `/api/v1/clinic`, `/api/v1/payment` uchun
   `request-termination` 503 + `Retry-After`; to'lov webhook'lari (`/api/v1/payment/(payme|click)/`) OCHIQ qoladi).
2. Monolit cron'larini to'xtating: slot generatsiyasi, hold/bron expiry, avto-yakunlash, litsenziya eslatmasi.
3. 1 daqiqa kuting: davom etayotgan to'lov sessiyalari (`pending_payment`) ko'chiriladi va booking-service
   `payment.captured` ni qabul qiladi (payment-service 4-bosqichgacha monolitda — quyidagi 4-qadam).

## 2. Ma'lumot ko'chirish
```bash
python scripts/migrate/core.py --source "$MONOLITH_DATABASE_URL" \
  --catalog "$CATALOG_DATABASE_URL" --schedule "$SCHEDULE_DATABASE_URL" \
  --pricing "$PRICING_DATABASE_URL" --booking "$BOOKING_DATABASE_URL" | tee core-cutover.json
```
- Idempotent: xato bo'lsa sababini tuzatib qayta ishga tushiring — mavjud qatorlar tegilmaydi, ikkinchi
  ishga tushishda `inserted` nolga yaqin. Har maqsad ixtiyoriy (bittasini alohida qayta yuborish mumkin).
- `inserted` == `plan` bo'lishi kerak. Farq — maqsadda allaqachon bor qator (masalan, servis o'zi yaratgan
  slot yoki bir xil `slug`); `core-cutover.json` ni saqlang.
- Pul: so'm → tiyin ×100 (ROUND_HALF_UP), stavkalar → bp. To'langan/qaytarilgan summa `payment_payment`
  (`succeeded`, `refunded`, `partially_refunded`) va `payment_refund` (`succeeded`) dan.

### `skipped` — nima va nima uchun
| Kalit | Ma'nosi | Nima qilish kerak |
|---|---|---|
| `catalog.doctor_documents: … file-service ID yo'q` | Hujjat fayli monolitning lokal private storage'ida | Tasdiqlangan shifokorga ta'sir yo'q (bron hujjatga bog'liq emas). Moderatsiyadagilar (`pending`) hujjatni ilovadan qayta yuklaydi |
| `catalog.clinics: pending → draft` | Servisda klinika moderatsiya holati yo'q | Admin `/v1/admin/clinics/{id}/publish` qiladi |
| `catalog.services: narx 0` | Servisda `price_tiyin > 0` | Shifokor/klinika narx bilan qayta yaratadi |
| `schedule.slots: 'booked', lekin bron yo'q` | Monolit nomuvofiqligi | Qator tashlanadi; kelajakdagi vaqt bo'lsa generatsiya qayta yaratadi |
| `schedule.slots: egasiz 'held' → free` | Bronsiz hold (muddati o'tgan) | Yo'q — slot bo'sh |
| `schedule.slots: egasiz travel_buffer bloki → free` | ADR-0011 #4: egasini topib bo'lmagan yo'l bloki | Yo'q — slot sotuvga qaytadi |
| `schedule.working_rules / price_rules: noto'g'ri …` | Servis CHECK cheklovi | Shifokor qoidani qayta kiritadi |
| `booking.bookings: slot topilmadi` | Monolitda slot o'chirilgan | Qo'lda ko'rib chiqing (moliyaviy tarix) |

Monolitda yo'q, servis standarti qo'yiladigan maydonlar: `bookings.confirmed_at`, `cancelled_by`,
`cancel_reason_code` (bo'sh), `booking_items.base_price_tiyin` (= yakuniy narx), `quote_id` (NULL).
`confirmed` + `started_at` → `in_progress`. Uy chaqiruvi yo'l bloklariga `blocked_by_slot_id` (eng yaqin band
uy chaqiruvi sloti) yoziladi.

## 3. Servislarni to'liq yoqish
1. `schedule-scheduler` ni ishga tushiring — u gorizontgacha yetishmayotgan slotlarni generatsiya qiladi
   (mavjudlariga tegmaydi). Tekshiruv: `GET /v1/schedule/doctors/{id}/slots?date=<ertaga>`.
2. booking-scheduler: `pending_payment` bronlardan muddati o'tganlari 1 daqiqada `expired` bo'ladi.
3. Kong: frontend `/v1/*` ga o'tadi; eski `/api/v1/booking|catalog|schedule...` yo'llarini 503 da qoldiring
   (ikki joyda yozish yo'q) — frontend relizidan keyin olib tashlanadi.

## 4. To'lov (4-bosqichgacha)
> 4-bosqichdan keyin: `docs/runbooks/money-cutover.md` (payment/billing servislari).

payment monolitda qoladi: monolit `payment.captured`/`refund.succeeded` ni outbox orqali `medbron.events`
ga yuborishi kerak (booking-service consumer). Ko'chirish paytida `pending_payment` bo'lgan bronlar uchun
to'lov yechilsa — booking `confirm` qiladi yoki `confirmation_failed` (slot yo'q) chiqaradi → refund.

## 5. Tekshiruv (cutover'dan keyin 15 daqiqa)
- `core-cutover.json`: `skipped` jadvaldagi kutilganlardan boshqa kalit yo'q.
- Monolit va servis sonlari: `SELECT status, count(*) FROM booking_booking GROUP BY 1` ↔ booking-service
  `bookings` (`in_progress` monolitda `confirmed` ichida).
- Grafana: `bookings_created_total` o'smoqda, `booking_transitions_total` da kutilmagan o'tishlar yo'q, DLQ bo'sh.
- e2e (stage): `tests/e2e/test_booking_flow.py`.

## Orqaga qaytish
Servislarga yangi bron tushmagan bo'lsa (muzlatish ichida): Kong'da `/api/*` yozishni qayta oching, servislar
to'xtatiladi — monolit ma'lumoti o'zgarmagan (skript manbaga faqat o'qish uchun ulanadi). Yangi bronlar tushgan
bo'lsa orqaga qaytarish — qo'lda (booking-service → monolit eksport); shuning uchun 3-qadamdan keyin 15 daqiqalik
tekshiruvni muzlatish olib tashlanguncha bajaring.
