# billing-service

> NN=11 · Python · ichki port 8080 · host 8111 · Postgres 5411 · Redis 6311

## Vazifasi
Shifokorlarga to'lov (payout): daromad daftari, haftalik payout davrlari, to'lov rekvizitlari, shifokor va
klinika daromad paneli, CSV ko'chirma (docs/SERVICE_REGISTRY.md, 11; ADR-0012).

**Daromad daftari** (`earning_entries`) — shifokorning imzoli ulushi:
- `earning` (+) — to'langan bron yakunlandi / mijoz yoki shifokor kelmadi / tasdiqlangandan keyin bekor
  qilindi: `share_of(money, paid_tiyin).doctor_tiyin` (`medbron_common.money`, payment ledger'i bilan
  YAGONA formula). Klinikada to'langan (`paid_tiyin = 0`) bron yozilmaydi.
- `refund` / `reversal` (−) — payment eventidagi `doctor_share_tiyin` (qayta hisoblanmaydi).
  Bekor qilish jarimasining shifokor ulushi = earning − refund ulushi.
- Idempotent: `UNIQUE(kind, source_ref)` (earning — booking_id, refund — refund_id, reversal — payment_id).
- Tartib kafolatlanmagan: refund bron eventidan oldin kelsa "yetim" (`doctor_id NULL`) yoziladi va earning
  kelganda to'ldiriladi. Hech qachon earning bermaydigan bron (`confirmation_failed`) refund'i payout'ga kirmaydi.

**Payout** — haftalik (Du–Ya, Asia/Tashkent), admin sozlamasi `payout.weekday` kuni 04:00 da o'tgan hafta
uchun. Shifokorning payout'ga kirmagan, `eligible_at` davr oxirigacha bo'lgan BARCHA yozuvlari yig'iladi
(payout'dan keyin kelgan refund keyingi payout'dan ayiriladi). Sof summa `payout.min_amount_tiyin` dan kam
yoki ≤ 0 — keyingi haftaga o'tadi. `UNIQUE(doctor_id, period_start, period_end)` + SKIP LOCKED.
`net = gross − platform_fee − provider_fee − refunds`.

## Endpointlar
Pul — tiyin (butun son).

| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | Standart |
| GET | `/v1/doctor/earnings?period=today\|week\|month` yoki `?from=&to=` (≤ 366 kun) | shifokor JWT (`doctor_id`) | Jami + bron bo'yicha qatorlar (`transactions`), `paid_out` / `awaiting_payout` |
| GET | `/v1/doctor/payouts?limit=&offset=` | shifokor | Payout'lar |
| GET | `/v1/doctor/payouts/{id}/statement` | shifokor | CSV (UTF-8 BOM, `;`) — bemor ma'lumotisiz |
| GET / PUT | `/v1/doctor/payout-account` | shifokor | Rekvizitlar; javobda faqat `masked_number`; PUT auditga |
| GET | `/v1/clinic/{clinic_id}/earnings?period=…` | klinika admini (catalog `/internal/v1/access`, `role=clinic_admin`, Redis 60 s) | Klinika bronlari; begona — 404 `CLINIC_NOT_FOUND` |
| GET | `/v1/admin/payouts?status=&doctor_id=&period_end=` | admin `payouts.manage` | Ro'yxat |
| POST | `/v1/admin/payouts/build` `{period_end?}` | admin `payouts.manage` | Qo'lda hisoblash (idempotent), audit |
| POST | `/v1/admin/payouts/{id}/mark-paid` `{bank_reference}` | admin `payouts.manage` | Idempotent → `payout.paid` + audit |
| GET | `/v1/admin/payouts/{id}/statement` | admin `payouts.manage` | CSV |

Internal API yo'q (billing'ni hech kim chaqirmaydi) — `/internal/v1/billing/*` hammaga 403.

## Eventlar
Manba: `contracts/events/catalog.yaml`.

| Publish | Consume |
|---|---|
| `payout.calculated`, `payout.paid`, `audit.recorded` | `admin.settings_changed`, `booking.cancelled`, `booking.completed`, `booking.no_show`, `payment.reversed`, `refund.succeeded` |

## Sinxron bog'liqliklar
Chaqiradi: `catalog-service` (klinika a'zoligi), `admin-service` (global sozlamalar). Chaqiruvchilar: —.

## Konteynerlar
`billing-service` (API), `-outbox-relay`, `-event-consumer`, `-scheduler` (haftalik payout), `-migrate` (profil).

## Ishga tushirish
```bash
uv sync                                   # .venv (libs/py-common editable)
uv run pytest                             # hammasi (integratsiya — Docker/testcontainers)
uv run pytest -m "not integration"        # Docker'siz
uv run ruff check . && uv run mypy app

cp .env.example .env
docker compose up -d --build
docker compose --profile migrate run --rm billing-service-migrate
curl localhost:8111/health/ready
```

## Migratsiyalar
```bash
uv run alembic revision --autogenerate -m "..."   # keyin faylni ko'zdan kechiring
```
Migratsiya deploy'ning alohida qadami — container CMD'da emas (BLUEPRINT 9).

## Ma'lumot o'sishi
| Jadval | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `earning_entries` | har to'langan bron + har refund | saqlanadi (moliyaviy hisobot, ≥ 5 yil) |
| `payouts` | shifokor × hafta | saqlanadi |
| `outbox_events` | har event | relay: `published` 7 kundan keyin |
| `processed_events` | har qabul qilingan event | event-consumer: 30 kundan keyin (soatiga bir) |
