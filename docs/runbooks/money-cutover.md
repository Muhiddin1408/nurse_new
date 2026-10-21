# Runbook: pulga o'tish (4-bosqich cutover)

Maqsad: to'lovlar, refundlar, daftar (ledger), reconciliation farqlari va payout'lar monolitdan payment- va
billing-service'ga o'tadi (ADR-0012). Asosiy domen (3-bosqich) allaqachon ko'chgan bo'lishi shart — `booking_id`,
`doctor_id` o'sha UUID'lar.

Kutilgan to'xtash: **onlayn to'lov uchun ≈ 10 daqiqa**. Bron yaratish ishlaydi (to'lov "kutilmoqda" holatida
qoladi, muddat ichida to'lanadi). Xavf: pul ikki joyda yechilishi — shuning uchun webhook URL almashtirilishidan
OLDIN monolitga yangi checkout ochilmaydi va ochiq tranzaksiyalar yakunlanishi kutiladi.

## 0. Tayyorgarlik (oldindan, ish vaqtida)
1. payment- va billing-service deploy qilingan: `alembic upgrade head`, `/health/ready` = 200.
   **To'xtatilgan holda:** `payment-service-scheduler` (ko'chirilgan pending refundlar yuborilmasin) va ikkala
   servisning `event-consumer`'i (monolit eventlari aralashmasin). API va outbox relay — ishlaydi.
2. Sirlar (vault / server `.env`): `PAYME_MERCHANT_ID`, `PAYME_SECRET_KEY`, `PAYME_ALLOWED_IPS`, `CLICK_SERVICE_ID`,
   `CLICK_MERCHANT_ID`, `CLICK_MERCHANT_USER_ID`, `CLICK_SECRET_KEY`, `CLICK_ALLOWED_IPS` — monolitdagi bilan BIR XIL
   (kassa o'zgarmaydi, faqat URL). Kalit bo'sh — webhook fail-closed (hamma so'rov rad).
3. Admin sozlamalari: `fiscal.tin`, `fiscal.default_*` (Click chek), `payout.min_amount_tiyin`, `payout.weekday`.
4. Kong'da yangi yo'llar bor: `/v1/payments/payme/webhook`, `/v1/payments/click/(prepare|complete)`, `/v1/payments`,
   `/v1/admin/{payments,refunds,ledger,reconciliation,payouts}`, `/v1/doctor/{earnings,payouts,payout-account}`.
   Prod: provayder IP allowlist Kong'da ham (`kong.prod.yml`).
5. Dry-run:
   ```bash
   pip install -r scripts/migrate/requirements.txt
   python scripts/migrate/money.py --dry-run --source "$MONOLITH_DATABASE_URL"
   ```
   `skipped` bo'sh bo'lishi kerak; `notes` — kutilgan (pastdagi jadval).

## 1. Monolitda yangi to'lovni to'xtatish
1. Kong: `/api/v1/payment/bookings/*/checkout` → `request-termination` 503 + `Retry-After: 600`
   (frontend allaqachon `/v1/payments` da bo'lsa — bu yo'l bo'sh turadi).
2. **Webhook'lar (`/api/v1/payment/(payme|click)/`) OCHIQ qoladi** — boshlangan tranzaksiyalar monolitda yakunlansin.
3. Monolit cron'lari: `run_refunds`, `submit_fiscal_receipts`, `reconcile_payments`, `build_payouts` — to'xtating.
4. Kuting, toki monolitda ochiq tranzaksiya qolmaguncha (odatda ≤ 15 daqiqa — bron hold'i):
   ```sql
   SELECT count(*) FROM payment_payment WHERE status = 'processing';
   ```
   12 soatdan eski `processing` — Payme o'zi bekor qiladi (reason 4); ko'chirishga to'sqinlik qilmaydi.

## 2. Provayder kabinetida URL almashtirish
| Provayder | Eski | Yangi |
|---|---|---|
| Payme (Merchant API endpoint) | `https://<domen>/api/v1/payment/payme/webhook` | `https://<domen>/v1/payments/payme/webhook` |
| Click (Prepare URL) | `…/api/v1/payment/click/prepare` | `https://<domen>/v1/payments/click/prepare` |
| Click (Complete URL) | `…/api/v1/payment/click/complete` | `https://<domen>/v1/payments/click/complete` |

Almashtirishdan keyin monolit webhook yo'lini Kong'da 503 ga o'tkazing (provayder takrorlasa — yangisiga keladi,
chunki URL kabinetda).

## 3. Ma'lumot ko'chirish
```bash
python scripts/migrate/money.py --source "$MONOLITH_DATABASE_URL" \
  --payment "$PAYMENT_DATABASE_URL" --billing "$BILLING_DATABASE_URL" | tee money-cutover.json
```
- Idempotent (`ON CONFLICT DO NOTHING`; ledger yozuvlari faqat yangi tranzaksiyalar uchun). Qayta ishga tushirish
  xavfsiz — ikkinchi marta `inserted` nol.
- `inserted` == `plan` bo'lishi kerak. **`ledger_trial_balance` = 0 bo'lishi SHART** — aks holda skript exit 3
  qaytaradi: davom etmang, monolit daftarini (`payment_ledgerentry`) tekshiring.
- Monolit qiymatlari o'zgarmaydi: payout qatorlari va payout'ga kirmagan daromadlar monolit formulasi bilan
  (ADR-0012). Yangi formula faqat cutover'dan keyingi hodisalarga.

### `skipped` / `notes`
| Kalit | Ma'nosi | Nima qilish kerak |
|---|---|---|
| `payments: bron topilmadi` | Monolitda bron o'chirilgan | Qo'lda ko'rib chiqing (moliyaviy tarix) — to'lov qatori monolit zaxirasida qoladi |
| `payments: noma'lum holat` / `refunds: …` | Kutilmagan qiymat | Kod bilan solishtiring, kerak bo'lsa xarita kengaytiriladi |
| `notes` → `payme refunded + cancel_time → reversed` | Payme kabinetidan qaytarilgan (monolit `refunded` derdi) | Yo'q — `CheckTransaction` endi −2 qaytaradi |
| `notes` → `money: taqsimot yo'q/mos emas` | B8 dan oldingi bron | Yo'q — standart stavkalar (15% / 1%) |
| `notes` → `payout'ga kirmagan daromad` | Keyingi payout'ga kiradigan qabullar | Yo'q |

Ko'chmaydi: provayder so'rov jurnallari (`ProviderRequestLog`, `PaymeCallbackLog`) — nizoda dalil sifatida
monolit bazasi zaxirasida 1 yil saqlanadi.

## 4. Servislarni yoqish
1. `payment-service-event-consumer`, `billing-service-event-consumer`, keyin `payment-service-scheduler`
   (ko'chirilgan `pending` refundlar 1 daqiqada provayderga ketadi; Payme'nikilar — `manual_required` navbatiga).
2. `billing-service-scheduler` — keyingi payout hisoblash `payout.weekday` 04:00 da.
3. Admin panel: `/v1/admin/refunds?status=manual_required` — kabinetda qaytarilganlarini `complete` qiling.

## 5. Tekshiruv (cutover'dan keyin 15 daqiqa)
- `money-cutover.json`: `ledger_trial_balance` = 0, `skipped` bo'sh.
- Sonlar: `SELECT status, count(*) FROM payment_payment GROUP BY 1` ↔ `GET /v1/admin/payments?status=…`;
  `GET /v1/admin/ledger/balances` ↔ monolit `ledger.balance(...)` (so'm × 100).
- Sinov to'lovi (test kassa yoki kichik summa): checkout → Payme → bron `confirmed` (`payment_succeeded_total` o'sdi).
- Grafana: `payment_webhook_received_total{result="error"}` o'smayapti, `reconciliation_discrepancies_total` = 0, DLQ bo'sh.
- Ertasi kuni 03:00 ichki reconciliation farq topmagan (`GET /v1/admin/reconciliation`).
- e2e (stage): `tests/e2e/test_money_flow.py`.

## Orqaga qaytish
Kabinetda URL'larni eskisiga qaytaring, Kong'da monolit checkout/webhook yo'llarini oching, monolit cron'larini
yoqing. Cutover'dan keyin yangi servisda yechilgan to'lovlar (`GET /v1/admin/payments?status=succeeded`, `created_at`
> cutover) monolitga qo'lda kiritiladi — shuning uchun 5-qadamdagi sinov to'lovini muzlatish olib tashlanguncha bajaring.
