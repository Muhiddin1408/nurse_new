# ADR-0002: Event bus — RabbitMQ (Kafka o'rniga)

- **Holat:** Taklif qilingan · 2026-10-20

## Kontekst
Monolit Kafka (`confluent-kafka`) ishlatadi. BLUEPRINT RabbitMQ 3.13 (topic exchange + DLX + retry)
ni standart qiladi. MedBron yuklamasi: kuniga o'n minglab event, replay talabi yo'q (analitika
ClickHouse'da saqlanadi), lekin per-message retry/DLQ va oddiy operatsiya muhim.

## Qaror
Bitta RabbitMQ broker, bitta `medbron.events` topic exchange, `medbron.dlx` → `medbron.dlq`,
`medbron.retry` (5s/30s/5m) — `ARCHITECTURE.md` 8-bo'lim. Quorum navbatlar.

## Oqibatlar
- (+) Per-message ack/retry/DLQ tayyor; consumer guruhlari va partitsiyalarni boshqarish shart emas.
- (+) Bitta jamoa uchun operatsion jihatdan sodda.
- (−) Event replay yo'q: search qayta indekslash uchun eventlarni analytics (ClickHouse) yoki
  catalog eksport endpointi manba bo'ladi.
- (−) O'tish davrida Kafka→RabbitMQ ko'prigi kerak.
