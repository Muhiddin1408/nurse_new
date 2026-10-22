# ADR-0003: schedule-service va ws-gateway — Go

- **Holat:** Taklif qilingan · 2026-10-20

## Kontekst
Slot endpointi — tizimdagi eng ko'p chaqiriladigan so'rov (har shifokor kartochkasi, 10 000 rps
maqsad, p95 < 100 ms). `hold` — eng qattiq konkurensiya invarianti (CAS). BLUEPRINT yuqori yuklamali
servislar uchun Go'ni tavsiya qiladi. WebSocket gateway — ko'p uzoq ulanishlar.

## Qaror
- schedule-service: Go 1.22+, chi, pgx, go-redis, amqp091-go, `libs/go-common`.
- ws-gateway: Go.
- Qolgan 14 servis: Python 3.12 + FastAPI.

## Oqibatlar
- (+) Kam xotira, bashorat qilinadigan latency, goroutine'lar bilan stampede lock oson.
- (−) Ikki til: outbox/consumer/auth ikki marta yoziladi (`libs/py-common`, `libs/go-common`) va
  bir xil integratsiya testlari bilan tekshiriladi.
