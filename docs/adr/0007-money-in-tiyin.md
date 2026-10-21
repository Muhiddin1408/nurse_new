# ADR-0007: Pul — BIGINT tiyin

- **Holat:** Taklif qilingan · 2026-10-20

## Kontekst
Monolit `DecimalField(12,2)` so'mda saqlaydi. Payme tiyinda, Click so'mda ishlaydi. Servislararo
JSON'da decimal string va float xatolari xavfi bor.

## Qaror
Barcha servis bazalarida va eventlarda pul `BIGINT` tiyinda, maydon nomi `*_tiyin`. Foizlar
`NUMERIC(5,4)`. Yaxlitlash (`ROUND_HALF_UP`) faqat pricing-service'da; boshqa servislar tayyor
summalarni ko'chiradi, qayta hisoblamaydi. Ko'chirishda `amount * 100` (butun son ekanligi tekshiriladi).

## Oqibatlar
- (+) JSON'da aniq butun sonlar, float yo'q; Payme bilan to'g'ridan-to'g'ri mos.
- (−) UI va Click adapterida konvertatsiya.
