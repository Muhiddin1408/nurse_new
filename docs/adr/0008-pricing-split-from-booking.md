# ADR-0008: Narx hisoblash alohida servis

- **Holat:** Taklif qilingan · 2026-10-20

## Kontekst
Monolitning `api/booking/` ichida `pricing.py`, `discounts.py`, `packages.py`, `time_pricing.py`,
`payment_mode.py` bor — bron moduli eng tez o'sayotgan qism. Trasso'da order-service 26k qatorgacha
o'sgan (BLUEPRINT 12-bo'lim, #1).

## Qaror
pricing-service: quote (`POST /internal/v1/quotes`), promo, paketlar, birinchi bron / follow-up,
to'lov rejimi qarori, komissiya taqsimoti. Booking natijani **snapshot** qilib saqlaydi va eventlarda
(`money_split`) uzatadi; billing va payment qayta hisoblamaydi.

## Oqibatlar
- (+) Booking faqat holat mashinasi va orkestratsiya.
- (+) Narx qoidalari mustaqil deploy qilinadi va test qilinadi.
- (−) Bron yaratishda qo'shimcha sinxron chaqiruv (quote, ~10 ms).
