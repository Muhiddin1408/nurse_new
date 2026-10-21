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
| `/api/*`, `/admin/`, `/static/` | monolit | OTP 5/daq, qolgan API 50/s |
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
