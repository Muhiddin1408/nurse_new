# ADR-0006: Bron sagasi — xoreografiya + holat mashinasi

- **Holat:** Taklif qilingan · 2026-10-20

## Kontekst
Monolitda `transaction.atomic()` slot, bron va to'lovni birga himoya qilardi. Servislarga
bo'linganda bu kafolat yo'qoladi.

## Qaror
- Tez, javob kerak bo'lgan qadamlar sinxron: quote (pricing), hold/confirm (schedule).
- Natijalar event orqali: `payment.captured` → booking confirm; muvaffaqiyatsizlikda kompensatsiya
  eventlari: `booking.confirmation_failed` → to'liq refund, `booking.cancelled/expired` → slot release.
- Alohida saga orkestratori / `saga_state` jadvali **yo'q**: bron holat mashinasining o'zi saga holati
  (`pending_payment` = "to'lov kutilmoqda" qadami), har o'tish idempotent.
- Muddatlar ikki qavatli: `payment_due_at` (booking cron) va hold TTL = due + grace (schedule cron).

## Oqibatlar
- (+) Booking 5 ta servisni chaqiradi, lekin hech kim uni bloklamaydi; payment booking'ga bog'liq, aksincha emas.
- (−) "Pul yechildi, bron yo'q" oraliq holat mumkin — `confirmation_failed` alert + avtomatik refund bilan yopiladi.
