# Runbook: identity servislariga o'tish (2-bosqich cutover)

Maqsad: kirish (OTP → JWT) monolitdan auth-service'ga o'tadi; profil, bemorlar, manzillar, bildirishnoma
sozlamalari, adminlar va audit — yangi servislarga. Monolit bron/to'lov/katalogni o'zi bajarishda davom
etadi va yangi RS256 tokenni qabul qiladi (`api/auth_bridge.py`). ARCHITECTURE 12.

Kutilgan to'xtash: **yo'q** (kirish bir necha daqiqa ikki joyda ishlaydi; ko'chirish idempotent).

## 0. Tayyorgarlik (oldindan, ish vaqtida)
1. `backend/infra` (RabbitMQ, S3) va beshala servis deploy qilingan, `alembic upgrade head`, `/health/ready` = 200.
2. Kalitlar: `scripts/gen-jwt-keys.sh` (serverda) — `secrets/public/*.pem`, `secrets/private/<servis>.env`.
3. Birinchi admin: `docker compose run --rm admin-service python -m app.cli create-admin --login <login> --role superadmin`
   — chiqqan `otpauth://` URI'ni autentifikator ilovaga qo'shing.
4. Eskiz: OTP va bildirishnoma matnlari Eskiz kabinetida tasdiqlangan (matnlar monolitdagi bilan bir xil).
5. Telegram: `setWebhook` yangi URL (`/v1/telegram/webhook`, `secret_token=TELEGRAM_WEBHOOK_SECRET`) —
   **monolitdagi bot webhook'i shu bilan almashadi**, shuning uchun 3-qadamdan keyin bajaring.

## 1. Monolitga ko'prik
```bash
# monolit .env
MEDBRON_JWT_PUBLIC_KEY_FILE=/opt/medbron/secrets/public/jwt-public.pem
```
Deploy → tekshiruv: auth-service tokeni bilan `GET /api/v1/patient/patients/` → 200
(`pytest apps/account/test_auth_bridge.py` — CI'da).

## 2. Ma'lumot ko'chirish
```bash
pip install -r scripts/migrate/requirements.txt
python scripts/migrate/identity.py --dry-run --source "$MONOLITH_DATABASE_URL"     # sonlarni ko'zdan kechiring
python scripts/migrate/identity.py --source "$MONOLITH_DATABASE_URL" \
  --auth "$AUTH_DATABASE_URL" --user "$USER_DATABASE_URL" \
  --notification "$NOTIFICATION_DATABASE_URL" --admin "$ADMIN_DATABASE_URL" \
  --totp-key "$TOTP_ENCRYPTION_KEY"
```
- O'sha UUID'lar; o'chirilgan akkauntlar (`deleted-*`) — telefon NULL, profil anonim.
- `admin_followup` ro'yxatidagi adminlar uchun `app.cli reset-totp` (TOTP'si bo'lmaganlar).
- Rejalashtirilgan eslatmalar (pending) notification-service navbatiga ko'chadi; monolit scheduler'ini
  shu payt **to'xtating** (`run_due_notifications` cron), aks holda eslatma ikki marta ketadi.

## 3. Kong: eski kirish yo'llari → auth-service
Frontend 3-bosqichda `/v1/auth/*` ga o'tadi; ungacha eski yo'llar qayta yoziladi. `auth-service` javobi monolit
shakli bilan mos (`tokens`, `is_new_user`, `user.available_roles`, `user.role`). `kong.<env>.yml` ga:
```yaml
  - name: auth-service-legacy
    url: http://auth-service:8080
    retries: 0
    routes:
      - {name: legacy-otp-request, paths: ["/api/v1/accounts/auth/otp/request"], strip_path: false,
         plugins: [{name: request-transformer, config: {replace: {uri: /v1/auth/otp/request}}},
                   {name: rate-limiting, config: {minute: 5, hour: 20, policy: local, limit_by: ip}}]}
      - {name: legacy-otp-verify, paths: ["/api/v1/accounts/auth/otp/verify"], strip_path: false,
         plugins: [{name: request-transformer, config: {replace: {uri: /v1/auth/otp/verify}}}]}
      - {name: legacy-refresh, paths: ["/api/v1/accounts/auth/token/refresh"], strip_path: false,
         plugins: [{name: request-transformer, config: {replace: {uri: /v1/auth/token/refresh}}}]}
      - {name: legacy-logout, paths: ["/api/v1/accounts/auth/logout"], strip_path: false,
         plugins: [{name: request-transformer, config: {replace: {uri: /v1/auth/logout}}}]}
      - {name: legacy-logout-all, paths: ["/api/v1/accounts/auth/logout/all"], strip_path: false,
         plugins: [{name: request-transformer, config: {replace: {uri: /v1/auth/logout/all}}}]}
      - {name: legacy-switch-role, paths: ["/api/v1/accounts/auth/switch-role"], strip_path: false,
         plugins: [{name: request-transformer, config: {replace: {uri: /v1/auth/switch-role}}}]}
```
Kong'ni qayta yuklang (`docker compose restart kong` yoki `kong reload`).

Eski HS256 refresh tokenlar auth-service'da yo'q — mijoz keyingi yangilashda 401 oladi va qayta kiradi (OTP).
Access tokenlar (15 daq) monolitda ishlashda davom etadi.

## 4. Delta va tekshiruv
1. 2-qadamni **qayta** ishga tushiring — ko'chirish va Kong o'rtasida monolitda ro'yxatdan o'tganlar qo'shiladi.
2. Smoke: yangi raqam bilan kirish → `/v1/users/me`, eski raqam bilan kirish → o'sha `user.id`,
   monolit endpointi yangi token bilan 200. `scripts/e2e.sh` dagi testlar staging'da.
3. Metrikalar: `otp_requests_total{result="sent"}` o'syapti, `sms_failed` ≈ 0, `outbox_pending` 0 ga qaytadi,
   DLQ bo'sh (`docs/runbooks/dlq.md`).

## Orqaga qaytarish
Kong'dan `auth-service-legacy` ni olib tashlang → kirish yana monolitda. Ko'prik (1-qadam) yoqiq qoladi:
auth-service'da kirganlarning tokenlari ishlashda davom etadi, auth-service'da yaratilgan yangi userlar
monolitga birinchi so'rovda avtomatik qo'shiladi (o'sha UUID). Ma'lumot yo'qolmaydi — yangi servislardagi
o'zgarishlar (profil, manzillar) keyingi urinishda qayta ko'chirish bilan birlashtiriladi.
