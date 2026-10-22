# ADR-0012: Pul (4-bosqich) — to'lov sagasi, yagona taqsimot formulasi, refund ulushi, payout daftari

- **Holat:** Qabul qilingan (4-bosqich) · 2026-10-21

## Kontekst
Monolitda to'lov, ledger, refund va payout bitta bazada, bitta tranzaksiyada ishlaydi (`api/payments/*`,
`api/doctor/earnings.py`). Mikroservislarda ular payment-service (pul harakati, daftar) va billing-service
(shifokor daromadi, payout) ga bo'linadi (reestr 10–11). Monolitni o'rganish bir nechta haqiqiy xatoni ko'rsatdi.

## Qarorlar

### 1. To'lov sagasi — bron sinxron tasdiqlanmaydi (ADR-0006 davomi)
Monolit Payme `PerformTransaction` ichida bronni o'zi tasdiqlardi. Endi: to'lov `succeeded` + ledger +
`payment.captured` BITTA tranzaksiyada, provayderga darhol muvaffaqiyat. booking slotni tasdiqlaydi; tasdiqlay
olmasa (`booking_expired`, `booking_cancelled`, `slot_unavailable`) `booking.confirmation_failed` → payment
to'liq refundni navbatga qo'yadi (`needs_refund`). Sinxron yo'nalish faqat payment → booking
(`/payable` — CheckPerform/Prepare'da, pul yechilishidan OLDIN; `/extend-payment` — best effort).

### 2. Yagona taqsimot formulasi — `medbron_common.money.share_of(money_split, amount)`
payment ledger'i ham, billing payout'i ham shu funksiyani chaqiradi (qayta hisoblash yo'q, ADR-0008):
to'liq summa — snapshot'ning o'zi; qismi (zaklad, qisman refund) — proporsional (half-up), qoldiq provayder
ulushiga. Snapshot (bron `money_split`) payment'ga `/payable` orqali, billing'ga `booking.completed/no_show/cancelled`
orqali keladi (`booking.cancelled` ga `money` qo'shildi).

### 3. Refund ulushi — shifokor faqat o'z ulushini qaytaradi
`doctor_share = share_of(money, R).doctor`, to'lovdagi shifokor ulushidan oshmaydi (qisman refundlar yaxlitlashi
yig'ilmaydi), oxirgi refund aniq qoldiqni oladi. Qolgan qismni — shu jumladan provayder qaytarmaydigan
komissiyani — **platforma** ko'taradi. Nizoda `doctor_liable=false` — ulush 0. Ulush event'da
(`refund.succeeded.doctor_share_tiyin`, `payment.reversed.doctor_share_tiyin`) — billing aynan shuni ayiradi.

Monolitda `R − R × commission` edi: shifokor provayder komissiyasini ham qaytarardi, to'liq refundda uning
sof daromadi manfiy bo'lib, panelda `max(0, …)` bilan yashirilardi (ledger esa manfiy qolardi).

### 4. Payout — shifokor "daromad daftari" (billing `earning_entries`)
Har yozuv imzoli shifokor summasi: `earning` (+, bron yakuni / mijoz kelmagani / to'langan bron bekor
qilingani), `refund` va `reversal` (−). Haftalik payout = payout'ga kirmagan yozuvlar yig'indisi (≥ admin
`payout.min_amount_tiyin`, aks holda keyingi haftaga). Payout'dan KEYIN kelgan refund keyingi payout'dan
ayiriladi (monolitda bron `PayoutLine` da bo'lsa, keyingi refund hech qachon ushlanmasdi). Refund earning'dan
oldin kelsa — `doctor_id=NULL` "yetim" yozuv, earning kelganda to'ldiriladi (tartib kafolatlanmagan; retry/DLQ
emas). Bekor qilishdagi jarimaning shifokor qismi alohida qoidasiz chiqadi: earning (to'langan) − refund.

### 5. Zaklad bronlarda daromad — to'langan summadan
Monolit payout'ni `total_price` taqsimotidan hisoblardi — zaklad (20%) bronda shifokorga platforma olmagan pul
uchun ham to'lanardi. Endi earning = `share_of(money, paid_tiyin)`.
**Ochiq:** klinikada olingan qoldiq uchun platforma komissiyasi undirilmaydi (ARCHITECTURE 14 #6).

### 6. Provayder qaytarishi (Payme `CancelTransaction` 2 → −2)
Ochiq refundlar (`manual_required` — Payme Merchant API refund qila olmaydi) `succeeded` bo'ladi, faqat
qolgan summa `payment.reversed` bilan (booking bronni bekor qiladi, billing ulushni ayiradi). Holat — `reversed`
(monolitda `refunded` edi — bizning refund bilan farqlanmasdi).

### 7. Fiskal chek — provayder OFD'si (ARCHITECTURE 14 #2)
Payme — `CheckPerformTransaction.detail`; Click — `ofd_data/submit_items` (scheduler, 3 kun retry).
Elementlar checkout'da `/payable` dan snapshot (bo'sh kodlar — admin standarti), summa = to'lov summasi.

### 8. Texnik
- `uq_payments_money_taken_per_booking` — bitta bronda pul bir marta (keyin qaytarilgan bo'lsa ham).
- Refund ishchisi: `next_attempt_at` "lease" (5 daq) bilan egallaydi, provayder chaqiruvi tranzaksiyadan
  tashqarida — bir nechta nusxa xavfsiz; backoff `2^n` daq + jitter, `REFUND_MAX_ATTEMPTS` → `refund.failed`.
- Provayder jurnali (`provider_logs`, kiruvchi + chiquvchi) — alohida sessiyada (rollback bo'lsa ham dalil
  qoladi), imzo/kalit yozilmaydi, 365 kun.
- Admin qo'lda refund — `Idempotency-Key` majburiy; `payment.discrepancy_found.provider` — ichki farqlarda `null`.
- Reconciliation'dagi "tasdiqlangan bron, to'lov yo'q" tekshiruvi payment'dan chiqdi (bron ma'lumoti booking'da).

## Oqibatlar
- (+) Pul qoidasi bitta funksiyada; payment daftari va billing payout'i hech qachon ajralmaydi.
- (+) Booking payment'ga bog'liq emas; payment yiqilsa bron yaratish ishlaydi, faqat onlayn to'lov kutadi.
- (−) "Pul yechildi, bron hali tasdiqlanmadi" oraliq holati (soniyalar) — UI "To'lov tekshirilmoqda".
- (−) Monolit ma'lumotlarini ko'chirishda eski `net` qiymatlari o'zgartirilmay olinadi (`scripts/migrate/money.py`);
  yangi formula faqat ko'chirishdan keyingi hodisalarga.
