# ADR-0011: Asosiy domen (3-bosqich) — egalik chegaralari, bron sagasi, monolit xatolari

- **Holat:** Qabul qilingan (3-bosqich) · 2026-10-21

## Kontekst
Monolitda katalog, jadval, narx va bron bitta tranzaksiyada ishlaydi (`api/booking/services.create_booking`).
Mikroservislarda bu to'rt servisga bo'linadi (ADR-0008, reestr 06–09). Monolitni o'rganish chegara masalalarini
va bir nechta haqiqiy xatoni ko'rsatdi.

## Qarorlar — egalik
| Ma'lumot | Monolitda | Egasi | Sabab |
|---|---|---|---|
| `PriceRule` (vaqtga qarab narx foizi, C12) | catalog | **schedule** | Foiz slot vaqtiga bog'liq; schedule slot o'qilganda `price_adjust_percent` ni o'zi hisoblaydi — pricing'ga qo'shimcha chaqiruv yo'q |
| `ServicePackage` + yozilishlar | catalog + booking | **pricing** | Chegirma qarori bitta joyda |
| `PromoCode` | booking (faqat Django admin) | **pricing** (admin API qo'shildi) | |
| Mutaxassisliklar (C6 bemor qoidalari bilan) | catalog (Django admin) | **catalog** (admin API qo'shildi) | |
| Klinika hayot sikli (draft → active ↔ suspended) | kod YO'Q (seed) | **catalog** (admin API) | `clinic.published/suspended` kontraktlari bor edi |
| Klinika a'zolari (clinic_admin) | seed | **catalog** (admin API, `user_id` bo'yicha) | |
| Shifokor Telegram chati | catalog | **notification** (2-bosqich) | |

Pul — hamma joyda **tiyin** (BIGINT, ADR-0007). Monolit so'mda `Decimal(12,2)` edi: foiz va komissiya
tiyinda `ROUND_HALF_UP`, stavkalar bazis punkt (admin sozlamalari, 2-bosqich).

## Qaror — bron sagasi (xoreografiya + sinxron qadamlar, ADR-0006)
`POST /v1/bookings` (booking-service):
1. Idempotency: `(client_id, key)` UNIQUE qator **tranzaksiya ichida birinchi** yoziladi (monolitda kalit
   bron yaratilgandan keyin alohida UPDATE bilan saqlanardi → parallel ikki so'rov ikki bron yaratardi).
2. Sinxron o'qishlar: catalog (`bookability`, `services?bookable_for=`), user (bemor/manzil egaligi),
   schedule (`GET slot`: shifokor, joy, davomiylik, **lead time** — monolitda yaratishda tekshirilmasdi).
3. pricing `POST /internal/v1/quotes` → narx, chegirma, taqsimot, to'lov rejimi (hold'dan OLDIN: noto'g'ri
   promo slotni band qilmaydi).
4. schedule `hold` (`hold_ref = booking_id`, TTL admin sozlamasidan) → pricing `commit` (promo/paket band qilinadi,
   `reserved` holatda) → booking qatori + `booking.created` (+ `booking.confirmed` va schedule `confirm`, agar
   to'lov kerak bo'lmasa).
5. Kompensatsiya: xatoda hold qo'lda `release` qilinadi; javob kelmasa — hold TTL bilan o'zi tugaydi; pricing
   `reserved` band qilish `booking.created` kelmasa 15 daqiqada o'zi bo'shaydi (sweeper). Sinxron kompensatsiya
   chaqiruvi yo'qolsa ham hech narsa abadiy band qolmaydi.
6. Keyingi holatlar eventlar bilan: `booking.cancelled/expired/rescheduled` → schedule slotni bo'shatadi;
   pricing band qilishni bo'shatadi. `payment.captured` → booking schedule `confirm`; slot yo'qolgan bo'lsa —
   `booking.confirmation_failed` (pul qaytarish — payment, 4-bosqich).

Schedule booking'ni chaqirmaydi (DAG). Shuning uchun shifokor jadvalini o'zgartirganda (qoida, ta'til) faol
bronlar bo'lsa schedule **409 + band slotlar ro'yxati** qaytaradi va faqat `resolution=keep_bookings` ni bajaradi;
bekor qilish kerak bo'lsa — shifokor booking'dagi `POST /v1/doctor/appointments/{id}/cancel` orqali (100% refund),
keyin o'zgarishni qayta yuboradi. Monolitdagi `cancel_and_refund` bitta so'rovda edi — bu ataylab ikki qadamga bo'lindi.

## Monolit xatolari — yangi servislarda tuzatiladi
| # | Xato (monolit) | Tuzatish |
|---|---|---|
| 1 | Idempotency kaliti tranzaksiyadan tashqarida | 1-qadam (yuqorida) |
| 2 | Bron yaratishda lead time / o'tgan slot tekshirilmaydi | schedule slot javobidagi `start_at` ≥ now + `min_lead_time_minutes` |
| 3 | Muddati o'tgan hold'ni bo'shatish job'i ID bo'yicha (qayta band qilingan slotni bo'shatishi mumkin) | `UPDATE ... WHERE status='held' AND hold_expires_at < now` (CAS) |
| 4 | Uy chaqiruvi buferi qaysi tashrifga tegishli ekani saqlanmaydi (qo'shni tashrifning buferi ochilib ketadi) | bloklangan slotda `blocked_by_slot_id` |
| 5 | Paket tugab yopilgach, bekor qilingan bron qaytargan seans ishlatilmaydi | yozilish `sessions_left > 0` bo'lsa qayta faollashadi |
| 6 | Jadval generatsiyasi to'xtatilgan hamkorlik klinikasi uchun ham slot yaratadi | generatsiya faqat faol hamkorlik/klinika uchun (proyeksiya) |
| 7 | `confirm`/`cancel` statusni qulfsiz o'qiydi | `SELECT ... FOR UPDATE` |
| 8 | Litsenziya muddati o'tib to'xtatilgan shifokor litsenziyani yangilay olmaydi (deadlock) | `PUT /v1/doctor/license` — to'xtatilgan shifokor ham yangilaydi; admin qayta faollashtiradi |
| 9 | Qayta faollashtirish `DoctorApproved` chiqaradi, yuborish/rad etish event chiqarmaydi | kontraktdagi `doctor.reinstated`, `doctor.onboarding_submitted`, `doctor.rejected` |
| 10 | `/doctors/{id}/services` tasdiqlanmagan shifokor xizmatlarini ham ko'rsatadi | faqat bron qilinadigan shifokor |

## Oqibatlar
- (+) Har qaror bitta egada; pul qoidalari bitta servisda (pricing).
- (−) Bron yaratish 5–6 sinxron chaqiruv (p95 byudjeti: catalog/user/schedule/pricing — har biri < 50 ms, timeout 2 s).
- (−) Shifokor jadval o'zgarishi bilan bronlarni bekor qilish ikki qadam (UI ketma-ket bajaradi).
