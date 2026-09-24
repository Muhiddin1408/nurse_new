# Ma'lumot o'sishi rejasi (E14)

> **Qoida:** har bir jadval yaratilganda ikki savolga javob shu yerga yoziladi —
> **bu qancha o'sadi?** va **eskilarini kim o'chiradi?**
> Javobi yo'q jadval — ertangi kunning sekin so'rovi.

Kod holati: 2026-09-22.

---

## 1. Nega bu kerak

`TimeSlot` = shifokorlar × 30 kun × kuniga ~16 slot. 1000 shifokorda **oyiga
~500K qator**. Hech narsa o'chirilmasa, 3 yildan keyin ~18 million qator: slot
endpointining SLA'si (p95 < 100 ms) indekslar bilan birga sekinlashadi, `VACUUM`
uzayadi, backup og'irlashadi.

Muammo sekin keladi va hech qachon alert bermaydi — shuning uchun u rejalashtiriladi,
kutilmaydi.

---

## 2. Jadvallar bo'yicha javoblar

| Jadval | O'sish manbai | Strategiya | Kim bajaradi |
|---|---|---|---|
| `schedule_timeslot` | Slot generatsiyasi (`generate_slots`, 30 kun oldinga) | O'tgan va **bronga bog'lanmagan** slotlar 90 kundan keyin o'chiriladi. Bronli slot QOLADI (`Booking.slot` PROTECT — bron tarixi slot vaqtiga tayanadi) | `purge_expired_data` |
| `booking_booking` | Mijoz harakati | O'chirilmaydi (moliyaviy va tibbiy tarix). `created_at` indekslari bor; hajm katta bo'lganda sovuq saqlashga ko'chirish — 4-bo'lim | — |
| `utils_outboxevent` | Har bir domen hodisasi | `published` 7 kundan keyin o'chiriladi. `failed` TEGILMAYDI — ular qo'lda tekshirilishi kerak | `purge_expired_data` |
| `utils_deadletterevent` | Consumer ishlov bera olmagan xabarlar | **Avtomatik o'chirilmaydi.** DLQ bo'sh bo'lishi kerak; to'lib ketishi — bu o'sish emas, nosozlik | Qo'lda (E3 replay) |
| `utils_auditlog` | Har bir admin/tizim o'zgarishi | 5 yil (`RETENTION_AUDIT_DAYS`). PostgreSQL trigger'i faqat shu job'ga DELETE ruxsati beradi | `purge_expired_data` |
| `notifications_processedevent` | Har bir iste'mol qilingan hodisa | 30 kun (dedup oynasidan ancha uzun) | `purge_expired_data` |
| `notifications_smslog` | Har bir SMS | 90 kun | `purge_expired_data` |
| `payment_paymecallbacklog` | Har bir Payme so'rovi | 1 yil (A8 — nizolar uchun), keyin arxiv | `purge_expired_data` |
| `account_otpcode` | Har bir kirish urinishi | 7 kun | `purge_expired_data` |
| `booking_waitlistentry` | Band slotga urinish (C10) | Yopilgan yozuvlar 90 kundan keyin. `active` va `notified` TEGILMAYDI — ular hali javob kutmoqda | `purge_expired_data` |
| `payment_payment` / `refund` / `ledger*` / `payout*` | To'lovlar | O'chirilmaydi — buxgalteriya | — |
| `catalog_servicepricehistory` | Narx o'zgarishi | Sekin o'sadi (faqat INSERT, shifokor harakati bilan) | — |
| `booking_packageuse`, `booking_promoredemption` | Bron soniga proporsional | Bron bilan birga (CASCADE) | — |
| ClickHouse `fact_booking` | Har bir bron hodisasi | TTL + agregat jadvallar — 5-bo'lim | ClickHouse TTL |

---

## 3. Tozalash qanday ishlaydi

`apps/utils/management/commands/purge_expired_data.py`, kuniga bir marta
(`k8s/booking-cronjobs.yaml`).

O'chirish **bo'laklab** (5000 qator) bajariladi. Sababi: bitta `DELETE` bilan
million qator o'chirish tranzaksiyani uzaytiradi, jadvalni qulflaydi va
replikatsiya lagini oshiradi — ya'ni **tozalash ishining o'zi prodni
to'xtatishi mumkin**. Har bir bo'lak — alohida qisqa tranzaksiya; job uzilsa,
keyingi safar qolganidan davom etadi (o'chirish idempotent).

Bir yurishda ko'pi bilan 200 bo'lak (1M qator). Backlog undan katta bo'lsa job
tugaydi va ertaga davom etadi — cheksiz sikl yo'q.

### Kuzatuv
| Metrika | Nima aytadi |
|---|---|
| `medbron_rows_purged_total{table=...}` | Job nimani o'chirdi |
| `medbron_table_rows_estimate{table=...}` | Jadvalning taxminiy hajmi (`pg_class.reltuples`, `COUNT(*)` emas) |

**Alert:** `medbron_rows_purged_total{table="time_slots"}` bir hafta davomida
o'smasa — cron o'lgan va jadval jim o'syapti.

---

## 4. Partitioning — qachon va qanday (qo'lda, DBA)

Retention tozalashi `TimeSlot` ni **barqaror** hajmda ushlab turadi, shuning
uchun partitioning DARHOL kerak emas. U quyidagi chegaradan keyin kerak
bo'ladi:

> `medbron_table_rows_estimate{table="schedule_timeslot"}` **10 million**dan
> oshsa, yoki slot endpointi p95 > 100 ms ga chiqsa.

Sababi — partitioning bu bir martalik yengillik emas: `DETACH`/`ATTACH`
avtomatikasi, migratsiya vositalarining qo'llab-quvvatlashi va deploy
tartibiga qo'shimcha qoidalar keltiradi. Uni ehtiyoj isbotlanmasdan kiritish
— arzimagan narsaga doimiy murakkablik to'lash.

Kerak bo'lganda: `start_at` bo'yicha **oylik** `RANGE` partitioning; eski
partitionlar `DETACH` + arxiv, keyin `DROP`. Bu sxema o'zgarishi va nol
to'xtovli ko'chirish talab qiladi — qadamlar `QOLDA_BAJARILADIGAN.md` da.

`Booking` partitioning shart emas: u mijoz harakatiga bog'liq, ya'ni
slotlardan taxminan **ikki daraja** kichik o'sadi.

---

## 5. ClickHouse

Xom hodisalarni abadiy skanerlamaslik kerak: dashboard har safar `fact_booking`
ni to'liq o'qisa, ma'lumot o'sgani sayin sekinlashadi.

- `fact_booking` — TTL **730 kun** (`analytics/schema.py: FACT_TTL_DAYS`).
  Partition kaliti ham `created_date`, shuning uchun eski oylar butunlay
  o'chadi — bu arzon operatsiya.
- `daily_booking_agg` — kunlik agregat. Dashboardning uzoq oynali panellari
  (`analytics/queries.py: AGG_QUERIES`) **shundan** o'qiydi, xom jadvaldan emas.

### Nega agregat MATERIALIZED VIEW emas

MV har `INSERT` da ishlaydi. `fact_booking` — ReplacingMergeTree: Kafka'ning
at-least-once yetkazishida takrorlangan hodisa jadvalga **yoziladi** va faqat
fonda birlashtiriladi. MV o'sha takrorni ko'rib, daromadni ikki marta qo'shib
yuborardi — va buni hech kim sezmasdi, chunki grafik baribir ishonchli
ko'rinadi.

Shuning uchun agregat kuniga bir marta `FINAL` bilan **qayta hisoblanadi**
(`analytics/aggregates.py`, `rebuild_analytics_aggregates`, oxirgi 7 kun).
Sekinroq, lekin takrorlangan va **kech kelgan** hodisalarga chidamli, va
idempotent — istalgan payt qayta ishlatish mumkin.

⚠️ `unique_clients` kunlar bo'ylab **qo'shilmaydi** (bir mijoz ikki kunda
kelsa ikki marta sanaladi). Oylik noyob mijoz kerak bo'lsa — xom jadvaldan
`uniqExact(client_hash)`.

Batafsil: `analytics/` va `QOLDA_BAJARILADIGAN.md`.

---

## 6. Yangi jadval qo'shayotganda

1. Shu faylning 2-bo'limiga qator qo'shing.
2. O'sish manbai "har bir so'rov" yoki "har bir hodisa" bo'lsa — `purge_expired_data`
   ga tozalash va `settings.DATA_RETENTION` ga muddat qo'shing.
3. Muddat biznes yoki qonun bilan bog'liq bo'lsa (to'lov, tibbiy ma'lumot) —
   o'chirmang, `QOLDA_BAJARILADIGAN.md` ga yuriding va yuridik tekshiruvni yozing.
