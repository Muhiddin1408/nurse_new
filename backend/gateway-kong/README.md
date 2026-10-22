# backend/gateway-kong

Kong 3.9 (DB-less, deklarativ). Strangler-fig: `/api/*` — monolit (`host.docker.internal:8099`), `/v1/*` —
yangi servislar (`medbron-net` ichida `http://<servis>:8080`). Frontend 3-bosqichdan `/v1/*` ga o'tadi;
undan oldin eski kirish yo'llarini auth-service'ga yo'naltirish — `docs/runbooks/identity-cutover.md`.

| Yo'l | Servis | Izoh |
|---|---|---|
| `/v1/auth/otp/` | auth-service | IP limiti (dev 30/daq; prod 5/daq, 20/soat) + servis ichida telefon limiti |
| `/v1/auth/`, `/v1/admin/users` | auth-service | |
| `/v1/users/`, `~/v1/admin/users/{uuid}/profile$` | user-service | regex route ustun (`regex_priority`) |
| `/v1/admin/auth/` | admin-service | IP bo'yicha 10/daq, 60/soat |
| `/v1/admin/{admins,roles,permissions,audit,settings}` | admin-service | |
| `/v1/files`, `/v1/admin/files/` | file-service | Fayl o'zi S3'ga to'g'ridan-to'g'ri (presigned) |
| `/v1/notifications/`, `/v1/telegram/`, `/v1/admin/notifications/` | notification-service | |
| `/v1/catalog/`, `/v1/doctor/*`, `~/v1/clinic/{id}/(profile\|rooms\|doctors\|services)` … | catalog / schedule / pricing / booking | 3-bosqich: `kong.dev.yml` dagi route'lar |
| `~/v1/payments/(payme/webhook\|click/(prepare\|complete))$` | payment-service | Provayder webhooklari: rate-limit YO'Q, tana ≤ 64 KB, himoya — imzo + IP allowlist |
| `/v1/payments`, `/v1/admin/{payments,refunds,ledger,reconciliation}` | payment-service | Checkout POST 30/daq |
| `/v1/doctor/{earnings,payouts,payout-account}`, `~/v1/clinic/{id}/earnings`, `/v1/admin/payouts` | billing-service | |
| `/v1/reviews`, `/v1/doctor/reviews`, `/v1/admin/reviews` | review-service | Sharh yuborish 10/daq |
| `/v1/disputes`, `/v1/admin/disputes` | dispute-service | |
| `/v1/search/`, `/v1/admin/search/` | search-service | 20/s; GET retry 1 |
| `/v1/admin/analytics` | analytics-service | |
| `~/ws$` | ws-gateway | WebSocket (Upgrade o'tadi), read/write timeout 120 s; token query'da — access log formatida query yozilmasin (prod) |
| `/api/*`, `/admin/`, `/static/` | monolit | OTP 5/daq, qolgan API 50/s; eski `/api/v1/payment/(payme\|click)/` — cutover'gacha |
| `/internal/*` | — | har doim 404 (request-termination), hatto xato bilan servis route'i qo'shilsa ham |

- Global: `correlation-id` (`X-Request-ID`), CORS, request-size 10 MB, Prometheus.
- **Mijoz IP'si:** `KONG_TRUSTED_IPS` (standart `127.0.0.1/32`) — faqat ishonchli proksining `X-Forwarded-For`
  i hisobga olinadi; servislar Kong qo'ygan `X-Real-IP` ni o'qiydi. `0.0.0.0/0` qo'yish = har kim soxta XFF
  bilan IP limitlarini chetlab o'tadi. Prod: LB/CDN CIDR'i.

```bash
cd ../infra && docker compose up -d && cd -   # medbron-net
docker compose up -d                          # proxy: :8000, admin: 127.0.0.1:8100
python manage.py runserver 0.0.0.0:8099       # monolit (repo ildizida)
```
Konfiguratsiyani tekshirish: `docker run --rm -e KONG_DATABASE=off -v $PWD/kong.dev.yml:/k.yml kong:3.9.1 kong config parse /k.yml`.
`kong.prod.yml` (TLS, provayder IP allowlist, qat'iy limitlar) — 6-bosqich.
