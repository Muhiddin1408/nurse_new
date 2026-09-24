# Qo'lda bajariladigan vazifalar

Bu vazifalarni kod bilan yopib bo'lmaydi: ular uchun kalit, parol, real muhit yoki qo'lda sinov kerak.
Loyiha oxirida bajariladi. Har biri bajarilgach, `new_task.md` dagi tegishli band ustiga chiziladi.

---

## Bosqich 0

### 1. `hold_slot` 100 tomonli parallel testi — PostgreSQL'da
**Nega:** SQLite parallel yozishni ko'tarmaydi. Asosiy invariant ("bitta slotni faqat bittasi band qiladi") faqat PostgreSQL'da isbotlanadi.

**Qadamlar:**
1. PostgreSQL 17 (kompyuterda ishlayapti) da test bazasi uchun ruxsat: `postgres` foydalanuvchisi paroli.
2. PowerShell'da:
   ```powershell
   $env:POSTGRES_DB="medbron_test"
   $env:POSTGRES_USER="postgres"
   $env:POSTGRES_PASSWORD="<parol>"
   venv\Scripts\python.exe -m pytest
   ```
3. **Kutilgan natija:** hamma test yashil, jumladan `apps/booking/tests.py::test_yuz_parallel_sorovdan_faqat_bittasi_band_qiladi`.

---

### 2. Payme sandbox (test.paycom.uz) sinovi
**Nega:** Payme sandbox testidan o'tmasa shartnoma imzolanmaydi. Hujjatda "muzokara qilinmaydigan shart".

**Kerak:**
- Payme test kassasi: `PAYME_MERCHANT_ID`, `PAYME_SECRET_KEY` (test kaliti)
- Webhook uchun ochiq HTTPS manzil (server yoki `ngrok http 8000`)

**Qadamlar:**
1. `.env` / muhitga: `PAYME_MERCHANT_ID`, `PAYME_SECRET_KEY`, `PAYME_CHECKOUT_URL=https://test.paycom.uz`
2. `venv\Scripts\python.exe manage.py migrate` va `runserver`
3. Payme kabinetida endpoint: `https://<manzil>/api/v1/payment/payme/webhook`
4. test.paycom.uz da barcha sandbox ssenariylarini ishga tushirish (to'g'ri/noto'g'ri summa, noma'lum buyurtma, takroriy so'rovlar, bekor qilish, auth xatosi).
5. Xato bo'lsa: `PaymeCallbackLog` jadvalidan so'rov va javobni olib, xato kodlarini rasmiy Payme hujjati bilan solishtirish.
6. Prodda `PAYME_ALLOWED_IPS` ga Payme serverlari IP'larini yozish.

**Kutilgan natija:** sandbox'dagi barcha testlar yashil.

---

### 3. Real telefon bilan to'liq oqim
**Nega:** Avtomat test (`apps/utils/test_e2e_flow.py`) tashqi tizimlarni soxtasi bilan almashtiradi. Real SMS va real to'lov sahifasini faqat qo'lda tekshirish mumkin.

**Kerak:** Eskiz login ma'lumotlari (`SMS_PROVIDER=eskiz`, `ESKIZ_EMAIL`, `ESKIZ_PASSWORD`), 2-banddagi Payme test sozlamalari, Kafka + `run_outbox_publisher` + `run_notification_consumer`.

**Tekshiruv ro'yxati:**
- [ ] Telefonga OTP SMS keldi, kod bilan kirildi
- [ ] Bemor qo'shildi, shifokor va bo'sh vaqt tanlandi, bron yaratildi
- [ ] `checkout_url` ochildi, Payme test kartasi bilan to'landi
- [ ] Bron `confirmed` bo'ldi
- [ ] "Bron tasdiqlandi" SMS keldi
- [ ] 15 daqiqadan keyin ilova qayta OTP so'ramadi (refresh ishladi)
- [ ] Logout'dan keyin refresh token ishlamadi

---

### 4. CI'ni yoqish
**Nega:** `.github/workflows/ci.yml` tayyor, lekin loyiha hali git repo emas.

**Qadamlar:** `git init` → GitHub'ga push → Actions'da `ci` workflow yashilligini tekshirish.

---

### 5. Prometheus alertlarini ulash
**Qadamlar:** `kubectl apply -f k8s/prometheus-rules.yaml` (prometheus-operator bo'lsa), keyin alert kanalini (Telegram/Slack) Alertmanager'da sozlash. Sinov: publisher/to'lovni vaqtincha to'xtatib, alert kelishini tekshirish.

---

## Bosqich 1

### 6. Payme bilan refund jarayonini kelishish
**Nega:** Payme Merchant API'da savdogar refund'ni o'zi boshlay olmaydi, qisman qaytarish ham yo'q. Hozir har bir refund `manual_required` navbatiga tushadi.

**Qadamlar:**
1. Payme bilan aniqlashtirish: avtomat refund uchun qaysi API (Business / Subscribe `receipts.cancel`) ochiladi, qisman qaytarish mumkinmi.
2. Ochilsa — kalitlarni bering, `PaymeProvider.refund` (`api/payments/providers.py`) yoziladi.
3. Ochilmasa — operator jarayoni: admin panelda `Refund(status=manual_required)` ro'yxati → Payme kabinetida tranzaksiyani bekor qilish → Payme `CancelTransaction` yuboradi va refund avtomat `succeeded` bo'ladi.
4. Test rejimida real refundni sinash (Bosqich 1 tayyorlik mezoni): to'lash → bekor qilish → kabinetda qaytarish → SMS "pul qaytarildi" keldi.

### 7. Bekor qilish siyosatini biznes bilan tasdiqlash
Default: >24 soat 100%, 2–24 soat 50%, <2 soat 0%. Boshqa bo'lsa `settings.CANCELLATION_POLICY` (`full_refund_hours`, `partial_refund_hours`, `partial_refund_percent`). Foydalanuvchi shartnomasi (oferta) matniga ham yozilishi kerak.

### 8. Komissiya stavkalarini tasdiqlash
Default: platforma 15% (`PLATFORM_COMMISSION_RATE`), Payme 1% (`PAYME_FEE_RATE`), Click 1% (`CLICK_FEE_RATE`). Haqiqiy shartnoma stavkalarini muhitga yozing — bron paytida muzlatiladi, keyin eski bronlar o'zgarmaydi.

### 9. Reconciliation: Payme reestri va 7 kunlik kuzatuv
1. Payme kabinetidan kunlik reestr qanday formatda olinishini aniqlash (CSV ustunlari). `api/payments/reconciliation.py::parse_payme_registry` kutadi: `id, amount (tiyin), state, booking_id, create_time, perform_time, cancel_time` — farq qilsa ayting, moslayman.
2. Prodda har kuni: `python manage.py reconcile_payments --file <kechagi_reestr.csv>` (yoki avtomat yuklab olish kelishilsa, CronJob'ga qo'shiladi).
3. **Bosqich 1 tayyorlik mezoni:** 7 kun ketma-ket 0 farq. `PaymentDiscrepancy(status=open)` bo'lsa — har birini ko'rib, sababini topish.

### 10. Bosqich 1 tayyorlik mezonlari (prod/staging'da)
- [ ] Reconciliation 7 kun ketma-ket 0 farq (9-band)
- [ ] Refund real pul bilan test rejimida sinaldi (6-band)
- [ ] Ledger balansi bazadagi summalar bilan mos: `reconcile_payments` ichki tekshiruvi `ledger_imbalance` bermaydi
- [ ] Publisher qo'lda o'ldirilganda alert keldi (12-band)

### 11. ClickHouse `fact_booking` jadvalini ko'chirish (E4)
**Nega:** sxema `MergeTree` → `ReplacingMergeTree` + `event_id` ustuni. `CREATE TABLE IF NOT EXISTS` mavjud jadvalni o'zgartirmaydi, dvigatelni ALTER bilan almashtirib bo'lmaydi.

**Qadamlar (ClickHouse'da):**
```sql
RENAME TABLE fact_booking TO fact_booking_old;
-- analytics-consumer'ni --init-schema bilan qayta ishga tushiring (yangi jadval yaratiladi)
INSERT INTO fact_booking
SELECT concat(toString(booking_id), ':', status) AS event_id, * FROM fact_booking_old;
-- natijani solishtirib bo'lgach:
DROP TABLE fact_booking_old;
```

### 12. Monitoring ulash va sinash
1. Prometheus scrape: har bir booking podining `http://<pod>:8000/healthz/metrics` manzili (ServiceMonitor/annotation). Ingress'da bu manzilni tashqariga **ochmang**.
2. gunicorn bir necha worker bilan ishlasa: `PROMETHEUS_MULTIPROC_DIR` sozlash (aks holda raqamlar "sakraydi").
3. Sinov: `kubectl scale deploy booking-outbox-publisher --replicas=0` → 5 daqiqada `MedbronOutboxPublisherDead` alert keldimi → qayta `--replicas=2`.
4. Kafka consumer lag uchun `kafka-exporter` o'rnatish (E2 qolgan qismi).
5. Prodda `LOG_FORMAT=json` (default) — log tizimida (Loki/ELK) `correlation_id` bo'yicha qidiruv ishlashini tekshirish.

---

## Bosqich 2

### 13. Shifokor hujjatlari uchun maxfiy ombor (S3/MinIO)
**Nega:** lokal diskdagi `private_media/` bir nechta pod bo'lganda ishlamaydi (har podda o'z disk).
1. Maxfiy bucket yarating (**public access o'chiq**), shifrlash yoqilgan.
2. `pip install django-storages boto3`, `settings.STORAGES` ga `"private": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {"bucket_name": ..., "default_acl": "private", "querystring_auth": True}}`.
3. Muhitga `PRIVATE_STORAGE_BACKEND=private` va S3 kalitlari.
4. Zaxira va saqlash muddati siyosati (hujjatlar shaxsiy ma'lumot — A10).

### 14. Birinchi platforma adminini yaratish
`python manage.py createsuperuser` (telefon + parol) — moderatsiya navbati `/api/v1/moderation/doctors` shu foydalanuvchi bilan ishlaydi.

### 15. Telegram botni ishga tushirish (D11)
1. @BotFather'da bot yarating → `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`.
2. Tasodifiy uzun sekret: `TELEGRAM_WEBHOOK_SECRET` (masalan `python -c "import secrets;print(secrets.token_urlsafe(32))"`).
3. Webhook o'rnatish:
   ```
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook" -d "url=https://<domen>/api/v1/telegram/webhook" -d "secret_token=<SEKRET>" -d 'allowed_updates=["message","callback_query"]'
   ```
4. BotFather'da buyruqlar: `today - Bugungi qabullar`, `tomorrow - Ertangi qabullar`, `help - Yordam`.
5. Sinov: shifokor kabinetida `GET /api/v1/doctor/telegram/link` → havolani ochish → `/today` → ✅ tugmasi.

### 16. Interfeys qarori (D11)
Backend API tayyor. Qaror kerak: mijoz — mobil ilova; shifokor — Telegram bot (tayyor) + veb (jadval sozlash); klinika — veb panel. Frontend jamoasi uchun Swagger: `/api/docs/` (teglar: "Shifokor: …", "Moderatsiya", "Klinika admini").

### 17. Payout jarayonini buxgalteriya bilan kelishish (D6)
1. Payout davri (hozir haftalik, dushanba 06:00) va minimal summa (`PAYOUT_MIN_AMOUNT`, default 50 000) — shartnomaga yozish.
2. Har dushanba: `GET /api/v1/moderation/payouts?status=pending` → bank orqali o'tkazish → `POST .../mark-paid {"bank_reference": "<to'lov topshiriqnomasi №>"}`.
3. Shifokor rekvizitlari (`/doctor/payout-account`) — karta yoki hisob raqami; soliq (YaTT/MChJ) masalasini yurist bilan aniqlash.
4. Bosqich 2 real sinovi: yangi shifokor ro'yxatdan o'tadi → moderatsiya → jadval → bron → qabul → payout.

---

## Bosqich 3

### 18. Eslatmalar cron'ini ishga tushirish va tekshirish (C9)
1. Kubernetes: `k8s/booking-cronjobs.yaml` dagi `notifications-scheduler` (har daqiqa). K8s'siz: `run_scheduler` uni avtomat bajaradi, yoki `python manage.py run_notification_scheduler --loop`.
2. Sozlamalar (ixtiyoriy): `REMINDER_FIRST_HOURS` (24), `REMINDER_SECOND_HOURS` (2), `REVIEW_REQUEST_AFTER_HOURS` (2).
3. Real telefon bilan sinov: bron tasdiqlansin → `ScheduledNotification` da 3 qator → qatorning `send_at` ini o'tgan vaqtga qo'yib SMS kelishini tekshirish.
4. Prodda kuzatish: `medbron_scheduled_notifications_backlog` > 100 bo'lsa cron ishlamayapti.

**Bosqich 3 mezoni:** no-show darajasi eslatmalar yoqilgunga qadargi ko'rsatkichdan past — kamida 2 hafta ma'lumot kerak.

### 19. To'lov rejimini biznes bilan kelishish (B10)
1. `DEPOSIT_PERCENT` (default 20) va `FORCE_PREPAID_AFTER_NO_SHOWS` (default 2) — biznes tasdiqlashi kerak.
2. Shifokorlarga `clinic_payment_mode` ni tushuntirish: `at_clinic` konversiyani 2–3 barobar oshiradi, lekin no-show riskini shifokor oladi.
3. Zaklad bilan qolgan summani klinikada yig'ish tartibi — klinika kassasi bilan kelishilsin (tizim qolgan qismni kuzatmaydi).

### 20. Sprint 3.2 ma'lumotlarini to'ldirish (C4, C6, C11)
**Nega:** yangi qoidalar default'lari xavfsiz tomonga qaragan, lekin real ma'lumotsiz to'liq ishlamaydi.

**Qadamlar:**
1. `venv\Scripts\python.exe manage.py migrate`
2. Admin'da har bir mutaxassislik uchun: bolalarni qabul qiladimi (`accepts_children` — default **yo'q**, ya'ni hozir 18 yoshdan kichik bemor kattalar shifokoriga yozila olmaydi), yosh chegaralari, jins cheklovi (masalan ginekolog → `female`). Qarorni tibbiy tomon bilan kelishish.
3. Uy chaqiruviga chiqadigan shifokorlarga profilda chiqish nuqtasini (`home_base_latitude/longitude`) kiritishni aytish — kiritilmaguncha radius tekshirilmaydi va ular `?home_only=1&lat=&lng=` qidiruvida ko'rinmaydi.
4. Qo'lda kiritilgan eski `rating`/`reviews_count` qiymatlari birinchi haqiqiy sharhda qayta hisoblanib yo'qoladi — kerak bo'lsa oldindan nolga tushirish.
5. Haqorat so'zlari ro'yxatini (`api/booking/reviews.py`, `_BAD_WORDS`) moderator bilan to'ldirish.

### 21. Sprint 3.3 xavfsizlik sozlamalari — prod muhitda (A7, A10–A14)
**Nega:** kod tayyor, lekin kalitlar, tarmoq va infratuzilma qarorlari kod bilan yopilmaydi. Prodda (`DJANGO_DEBUG=0`) quyidagilarsiz ilova **ishga tushmaydi**: `ANALYTICS_SALT`, tasodifiy `ADMIN_URL`, `ADMIN_ALLOWED_IPS`.

**Qadamlar:**
1. `venv\Scripts\python.exe manage.py migrate` (AuditLog, trigger, AdminTOTPDevice).
2. Secret'larga: `ANALYTICS_SALT` (`python -c "import secrets;print(secrets.token_hex(32))"`) — **bir marta tanlanadi, o'zgartirilsa analitika kohortalari uziladi**.
3. `ADMIN_URL` — tasodifiy prefiks (masalan `ops-7f3k2/`), `ADMIN_ALLOWED_IPS` — ofis/VPN CIDR'lari.
4. Har bir admin uchun: `manage.py admin_2fa_setup +998...` → URI'ni Google Authenticator'ga QR qilib kiritish → `/<ADMIN_URL>/login/` da kod bilan kirib ko'rish.
5. Kafka: `booking.events`, `payment.events`, `doctor.notifications` topic'lari uchun `retention.ms=604800000` (7 kun).
6. `purge_expired_data` CronJob'i ishlayotganini tekshirish (`k8s/booking-cronjobs.yaml`) va `DATA_RETENTION` muddatlarini yurist bilan kelishish (buxgalteriya yozuvlari o'chirilmaydi).
7. Throttle chegaralari (`THROTTLE_*`) — birinchi haftadagi real trafik bo'yicha sozlash; 429 lar Grafana'da ko'rinsin.
8. A13 infra: bazaga TLS + alohida (superuser bo'lmagan) foydalanuvchi; PITR zaxira yoqish va **tiklashni haqiqatan sinab ko'rish**; secret'lar Vault / K8s Secret'da, rotation rejasi; GitHub'da repo yaratilgach `security` CI job'i yashil ekanini tekshirish.
9. `MedbronOtpSpike` alerti qabul qiluvchisi (Telegram/telefon) sozlash.

### 22. Sprint 3.4 — mobil ilova bilan kelishish (C15)
1. `venv\Scripts\python.exe manage.py migrate` (`is_deleted`, `FavoriteDoctor`).
2. **Buzuvchi o'zgarish:** `GET /booking/bookings/mine` endi ro'yxat emas, `{next, previous, results}` qaytaradi — mobil ilova yangilanmaguncha "Mening bronlarim" ekrani bo'sh ko'rinadi. Ilova relizini backend deploy bilan birga chiqarish.
3. "Yaqinimdagi" radiusining default qiymati (5 km) va "Yana bron qilish" tugmasi joylashuvini dizayner bilan kelishish.

### 23. Bosqich 4 — To'lov bloki (B6, B12, C12)
1. `venv\Scripts\python.exe manage.py migrate`.
2. **Click:** kabinetdan `CLICK_SERVICE_ID`, `CLICK_MERCHANT_ID`, `CLICK_SECRET_KEY`, `CLICK_MERCHANT_USER_ID`; Click serverlari IP'lari -> `CLICK_ALLOWED_IPS`. Kabinetda Prepare URL `https://<domen>/api/v1/payment/click/prepare`, Complete URL `.../click/complete`. Click test muhitida: to'g'ri/noto'g'ri imzo, noto'g'ri summa, takroriy so'rovlar, Click tomonidan bekor qilish, reversal.
3. **Fiskal:** soliq maslahatchisi bilan tibbiy xizmatlar uchun MXIK (IKPU) kodi, o'lchov birligi kodi va QQS stavkasini tasdiqlash -> `FISCAL_DEFAULT_MXIK`, `FISCAL_DEFAULT_PACKAGE_CODE`, `FISCAL_DEFAULT_VAT_PERCENT`, `FISCAL_TIN`. Payme sandbox'da chek chiqishini tekshirish; Click'da `submit_fiscal_receipts` CronJob'i.
4. **Paketlar (C12):** biznes bilan — qaysi xizmatlar kurs sifatida sotiladi (fizioterapiya, massaj, surunkali kasallik nazorati), seanslar soni, chegirma foizi va amal qilish muddati. Django admin -> `Service package`. Chegirmani **shifokor ko'taradi** (payout'dan chiqadi) — shuning uchun har bir paketni shifokor/klinika bilan yozma kelishish kerak. Mijoz oldindan to'lamaydi: paketga yozilish pul talab qilmaydi, keyingi bronlar chegirmali bo'ladi. Ilovada: shifokor sahifasida paket kartochkasi + "Paketga yozilish" tugmasi (`GET /booking/doctors/{id}/packages`, `POST /booking/packages/{id}/enroll`) va profilda "Mening paketlarim" (`GET /booking/packages/mine`, qolgan seans va muddat ko'rsatilsin).
5. **Dinamik narx (C12):** biznes bilan — qaysi soatlarda narx pasaytiriladi. Django admin -> `Price rule` (shifokor, hafta kunlari `0`=dushanba…`6`=yakshanba, soat oralig'i, foiz: manfiy=arzon). Talab statistikasi (`analytics`) bo'yicha bo'sh qoladigan soatlarni aniqlab, avval 1–2 shifokorda sinab ko'ring. **Mobil ilova bilan kelishish shart:** slot ro'yxatida narx slotga qarab o'zgaradi, shuning uchun to'lovdan oldin `POST /booking/price-preview` ga `slot_id` yuborilishi kerak — aks holda mijoz katalogdagi narxni ko'rib, boshqa summani to'laydi. Shifokor bilan: tuzatma uning payout'iga to'g'ridan-to'g'ri ta'sir qiladi (narxning o'zi o'zgaradi, chegirma emas).
6. **Chegirmalar:** biznes bilan — birinchi bron chegirmasi foizi (`FIRST_BOOKING_DISCOUNT_PERCENT`, default 0 = o'chiq), platforma chegirmalari uchun marketing byudjeti (manfiy komissiya daftarda ko'rinadi). Buxgalteriya bilan: chegirmali bronlarda shifokor payout'i qanday aks etadi.

### 24. Bosqich 4 — B2B bloki (D7, D8)
1. `venv\Scripts\python.exe manage.py migrate` (eski faol bo'lmagan affiliatsiyalar avtomat `ended` bo'ladi).
2. Birinchi klinika adminini biriktirish: Django admin'da `ClinicMembership(user, clinic)` — keyin u `/api/v1/clinic/*` bilan ishlaydi.
3. Klinika admini paneli uchun frontend (veb) — API tayyor; dizayn va reception ish jarayonini (dam olish kunida bronlarni ko'chirish, klinikada to'lovni qabul qilish) klinika bilan kelishish.
4. Klinika fotosi: hozir `photo_url` (tashqi havola). Fayl yuklash kerak bo'lsa — S3 ombori (13-band) bilan birga.

### 25. Bosqich 4 — Bozor bloki (C13, C14)
1. `venv\Scripts\python.exe manage.py migrate`.
2. **Push (FCM):** Firebase loyihasi, service account JSON -> `FCM_PROJECT_ID`, `FCM_CREDENTIALS_FILE`, `PUSH_PROVIDER=fcm`, `pip install google-auth`. Mobil ilova: har ochilishda `POST /accounts/me/push-devices`, logout'da `DELETE`. Real telefonda push kelishini sinash.
3. **Mijoz Telegram'i:** ilovada "Telegram'ni ulash" tugmasi -> `GET /accounts/me/telegram/link`. Bot bitta (shifokorlar bilan umumiy).
4. **Ruscha matnlar:** `api/i18n.py` dagi tarjimalarni ona tili rus bo'lgan odam ko'rib chiqsin; admin'da mutaxassisliklar uchun `name_ru` ni to'ldirish. SMS/push matnlarini `NotificationTemplate` orqali marketing jamoasi tahrirlashi mumkin.
5. **Marketing roziligi:** ilovada rozilik matni va yuridik tekshiruv (shaxsiy ma'lumotlar qonuni).

### 26. Bosqich 4 — Ma'lumot bloki (E14, agregat jadvallar, C15.4 qidiruv)
1. `venv\Scripts\python.exe manage.py migrate` (sxema o'zgarishi yo'q, lekin sozlamalar yangilandi).
2. **Retention muddatlarini tasdiqlash.** Yangi sozlamalar: `RETENTION_TIME_SLOTS_DAYS` (default 90) va `RETENTION_WAITLIST_DAYS` (default 90). Yuridik tekshiruv: o'tgan **bo'sh** slotlar hech qanday shaxsiy ma'lumot saqlamaydi, lekin buni ma'lumotlar siyosatida yozib qo'yish kerak. Bronli slotlar o'chirilmaydi.
3. **Birinchi yurishni nazorat ostida bajaring.** Prodda `purge_expired_data` birinchi marta ishlaganda bir necha yillik backlog bo'lishi mumkin. Avval `--dry-run` yo'q, shuning uchun: (a) backup oling; (b) kam yuklangan soatda ishga tushiring; (c) `medbron_rows_purged_total` va replikatsiya lagini kuzating. Job bir yurishda ko'pi bilan 1M qator o'chiradi va qolganini ertaga davom ettiradi — ya'ni backlog bir necha kunda tozalanadi, bu normal.
4. **Alertlarni ulash:** `k8s/prometheus-rules.yaml` dagi `medbron-data-growth` guruhi — `MedbronPurgeStalled` (cron o'lgan) va `MedbronTimeSlotTableLarge` (partitioning haqida o'ylash vaqti).
5. **`ANALYZE` jadvalda ishlab tursin.** `medbron_table_rows_estimate` `pg_class.reltuples` ga tayanadi, uni autovacuum yangilaydi. Agar autovacuum sozlanmagan bo'lsa metrika qotib qoladi — DBA bilan tekshiring.
6. **ClickHouse agregati va TTL.** `ensure_schema` yangi `daily_booking_agg` jadvalini va `fact_booking` ga TTL'ni (730 kun) qo'yadi — consumer'ni qayta ishga tushirganda avtomat bajariladi. **Diqqat: TTL ORQAGA QARAB ISHLAYDI** — 2 yildan eski qatorlar birinchi merge'da o'chadi. Prodda qo'llashdan oldin: (a) `SELECT min(created_date) FROM fact_booking` bilan qancha ma'lumot yo'qolishini ko'ring; (b) kerak bo'lsa `FACT_TTL_DAYS` ni oshiring (`analytics/schema.py`); (c) avval agregatni to'ldiring: `python manage.py rebuild_analytics_aggregates --days 1000` — tarixiy tendensiya agregatda saqlanib qoladi. Keyin kunlik CronJob (`booking-analytics-aggregates`, 04:15 UTC) oxirgi 7 kunni qayta hisoblab turadi.
7. **Dashboard'ni agregatga ko'chirish.** Uzoq oynali panellar (oylik/yillik daromad, bekor qilish darajasi) `analytics/queries.py: AGG_QUERIES` dan o'qisin. Qisqa oynali (oxirgi 30 kun) panellar xom `QUERIES` da qolishi mumkin — ular hali arzon. BI vositasi (Metabase/Grafana) bilan ishlaydigan odam bilan kelishing.
8. **Elasticsearch qidiruvi (C15.4).** ES klasteri (`ELASTICSEARCH_URL`) va `search-projector` deployment'i ishlab turishi kerak. Birinchi ishga tushirishda indeksni to'ldiring: `venv\Scripts\python.exe manage.py reindex_doctors`. Keyin tekshiring: `GET /api/v1/catalog/search?q=<shifokor ismining bir qismi>` — javobdagi `engine` **`elasticsearch`** bo'lishi shart. `postgres` chiqsa — ES ulanmagan (bu ham ishlaydi, lekin imlo xatosini kechirmaydi va relevantlik yo'q). Alert: `MedbronSearchDegraded`. Mobil ilova bilan kelishish: qidiruv ekrani `/catalog/search` ga o'tsin (`/catalog/doctors` filtrlangan ro'yxat bo'lib qoladi), va `engine=postgres` bo'lsa "qidiruv cheklangan rejimda" deb ko'rsatish kerakmi — mahsulot qarori.
9. **Ruscha/o'zbekcha qidiruv sifati.** `name_analyzer` hozir faqat `lowercase` qiladi. Kirill/lotin transliteratsiyasi ("Юсуббаев" ↔ "Yusubbayev") va o'zbekcha morfologiya kerak bo'lsa — ES analyzer'ini til mutaxassisi bilan sozlang va `DOCTORS_INDEX` ni `doctors_v2` ga ko'chiring (indeks mapping'i o'zgarganda qayta yaratish shart).
10. **Partitioning (kelajakda, DBA).** Hozir KERAK EMAS — retention jadvalni barqaror hajmda ushlaydi. Chegara: `medbron_table_rows_estimate{table="schedule_timeslot"}` 10M dan oshsa yoki slot endpointi p95 > 100 ms bo'lsa. Reja va sabablar: `docs/MALUMOT_OSISHI.md`, 4-bo'lim. Avvalgi arzonroq qadam — `RETENTION_TIME_SLOTS_DAYS` ni qisqartirish.

---

## Keyingi bosqichlar
_(Yangi qo'lda bajariladigan vazifalar paydo bo'lganda shu yerga qo'shiladi.)_
