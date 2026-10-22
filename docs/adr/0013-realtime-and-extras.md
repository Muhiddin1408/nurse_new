# ADR-0013: Real-time va qo'shimchalar (5-bosqich) — ws-gateway, review, dispute, search, analytics

- **Holat:** Qabul qilingan (5-bosqich) · 2026-10-21

## Kontekst
Monolitda sharh va nizo `booking` ilovasida, qidiruv Elasticsearch'ga bron bilan bir jarayonda yozardi, real-time
va analitika yo'q edi. 5-bosqichda ular reestr 12–16 servislariga ajraldi. Monolitni o'rganish bir nechta xatoni
ko'rsatdi.

## Qarorlar

### review-service
- Egalik `booking.completed` proyeksiyasidan; kelmagan bo'lsa booking `/review-eligibility` (sinxron fallback).
- Sharh oynasi faqat `completed_at` dan (monolitda `completed_at or updated_at` — tahrir oynani siljitardi),
  muddat — admin `sla.review_window_days` (review endi admin sozlamalarini o'qiydi: reestrda `review → admin`).
- Rad etish sababi majburiy (`review.rejected`); e'lon qilingan sharhni olib tashlash mumkin (reyting qayta).
- Klinika reytingi ham hisoblanadi; < 5 sharhda eventda `rating=null` (monolit saqlab, faqat yashirardi).
- Servis shaxsiy ma'lumot saqlamaydi: muallif ismi ilovadan keladi (bayroq tekshiruvidan o'tadi).
- Telefon bayrog'i: monolit regex'i raqamlar orasida `\b` ishlatardi — eng ko'p uchraydigan `+998901234567`
  (bo'shliqsiz) ushlanmasdi (e2e topdi). Endi `(?<!\d)…(?!\d)` chegarasi.

### dispute-service
- Bitta bronga bitta nizo (monolitda yopilgandan keyin qayta ochib, qayta refund so'rash mumkin edi).
- Yechim: `refund_full | refund_partial | rejected` + alohida `doctor_liable` (monolitda refund DOIM shifokordan
  ushlanardi). Refund ≤ to'langan summa (monolitda jim kesilardi). SLA admin `sla.dispute_hours` dan
  (monolitda 48 soat qotirilgan va buzilishi kuzatilmasdi) — `dispute.sla_breached`.
- Nizo refund navbatga tushganda emas, `refund.succeeded` kelganda yopiladi. Dalil — file-service.

### search-service
- **Elasticsearch 8** (monolit bilan bir xil; client 8.x — 9.x client ES 8 serverini rad etadi).
- Oddiy rejim — faqat eventlar (16 ta), Postgres proyeksiyasi + har jihat uchun `occurred_at` (eski event
  yangisini ustidan yozmaydi), ES'ga `external` versiya bilan.
- **Reindex — catalog `GET /internal/v1/export/*`** (keyset; 2026-10-21 qarori): yangi `doctors_v<N>` indeks →
  to'ldirish (shu paytda eventlar ikkala indeksga) → alias atomik almashtiriladi. Monolit jonli indeksga yozardi.
- `is_bookable` = approved VA kamida bitta haqiqiy taklif; joylashuv — barcha faol klinikalar + uy bazasi.
- ES yiqilsa qidiruv Postgres proyeksiyasidan (`degraded=true`).

### analytics-service
- `#` navbat (audit'dan tashqari) → ClickHouse `events_raw` (ReplacingMergeTree, `event_id` dedup, FINAL).
  libs `EventConsumer` emas — partiyali ingest (500 ta / 2 s), ClickHouse'ga yozilgandan keyin ack.
- Hisobotlar Toshkent vaqtida: kogorta konversiya / bekor / no-show, GMV, onlayn to'lov sofi.

### ws-gateway (Go)
- Ma'lumot saqlamaydi; Postgres yo'q — event dedup Redis `SET NX` (24 soat), servis ichidagi consumer
  (go-common consumer Postgres `processed_events` talab qiladi).
- Kanallar: `user:<id>` (avtomatik), `booking:<id>` (booking `/authz`, Redis'da 60 s), `doctor:<id>:schedule`.
  Redis pub/sub `rtws:*` — gorizontal masshtab. Xabarda maydonlar oq ro'yxati (PII yo'q).

### ClickHouse va Elasticsearch — egasining compose'ida
Database-per-service: `search-service-elasticsearch`, `analytics-service-clickhouse` ichki tarmoqda, infra'da emas.

## Ochiq
Sharh muallifi ismi manbai va akkaunt o'chirilganda anonimlashtirish; nizo ochish muddati va refundsiz
qolgan nizoni yopish; analytics'dagi foydalanilmayotgan Postgres; ws ulanish limiti global bo'lishi;
`available_on` aniq kunlik filtri (`schedule.availability_changed` ga bo'sh kunlar). ARCHITECTURE 14.

## Oqibatlar
- (+) Har qo'shimcha funksiya alohida ishlaydi; yiqilsa bron/to'lov oqimi to'xtamaydi (hammasi pastki oqim).
- (−) Qidiruv va real-time eventual (soniyalar); ES va ClickHouse — qo'shimcha operatsion yuk.
