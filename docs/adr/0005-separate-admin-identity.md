# ADR-0005: Admin — alohida identity va JWT

- **Holat:** Qabul qilingan (2-bosqichda amalga oshirildi) · 2026-10-21

## Kontekst
Monolitda platforma admini — `User.role=platform_admin` + TOTP. BLUEPRINT admin-service alohida
admin JWT (`aud=admin`, `permissions[]`) chiqarishini talab qiladi.

## Qaror
Admin akkauntlari admin-service'da (login + argon2 parol + TOTP majburiy), alohida RS256 kalit.
Permission'lar granular (`doctors.moderate`, `payments.refund`, ...). `clinic_admin` esa user roli
bo'lib qoladi (klinika xodimi — mijoz ilovasi foydalanuvchisi). Monolitdagi `platform_admin`
foydalanuvchilar ko'chirishda admin-service'ga o'tkaziladi.

## Oqibatlar
- (+) User kaliti sizib chiqsa ham admin endpointlariga kirib bo'lmaydi.
- (−) Admin bir vaqtda mijoz ham bo'lsa, ikki xil akkaunt.
