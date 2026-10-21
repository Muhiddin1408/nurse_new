# ADR-0001: Mikroservislar, monolitdan strangler-fig bilan ko'chish

- **Holat:** Taklif qilingan · 2026-10-20

## Kontekst
MedBron Django monoliti modul chegaralari (`services.py` + DTO) bilan yozilgan va "Faza 1–7" yo'l
xaritasi mikroservislarga o'tishni ko'zda tutgan. `MICROSERVICES_BLUEPRINT.md` maqsadli andozani
belgilaydi. Ishlab turgan tizimni bir kunda almashtirish (big-bang) — to'lovlar va bronlar uchun
qabul qilib bo'lmaydigan xavf.

## Qaror
- 16 ta servis (`SERVICE_REGISTRY.md`), database-per-service, async-first.
- Ko'chish **strangler fig**: Kong monolit oldiga qo'yiladi, marshrutlar bounded context bo'yicha
  bittadan yangi servisga yo'naltiriladi. Ma'lumot UUID'lari saqlangan holda bir martalik
  skriptlar bilan ko'chiriladi.
- Monolit kodi o'zgartirilmaydi (faqat vaqtinchalik ko'priklar: RS256 verifikator, Kafka→RabbitMQ bridge).

## Oqibatlar
- (+) Har bosqich alohida qaytarib olinadi (Kong marshrutini orqaga qaytarish).
- (−) O'tish davrida ikki tizim ishlaydi; ko'priklar keyin o'chirilishi kerak (5-bosqich vazifasi).
