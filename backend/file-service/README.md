# file-service

> NN=04 · Python · ichki port 8080 · host 8104 · Postgres 5404 · Redis — yo'q

## Vazifasi
Barcha fayllar (avatar, shifokor/klinika rasmi, shifokor hujjatlari, nizo dalillari) — bitta nazorat
nuqtasi: metadata, S3 obyektlari (ADR-0010), presigned URL, tekshiruv, thumbnail, biriktirish, tozalash.

- Mijoz faylni **to'g'ridan-to'g'ri S3'ga** yuklaydi (presigned PUT, Content-Type imzoda) — katta fayl API'ni band qilmaydi.
- `complete`: servis o'zi tekshiradi — obyekt bormi, hajm limiti, **haqiqiy tur** (magic bytes; e'lon qilingan
  Content-Type'ga ishonilmaydi). Mos kelmasa obyekt o'chiriladi (`rejected`).
- Ommaviy rasmlar qayta kodlanadi: **EXIF (GPS, qurilma) olib tashlanadi**, orientatsiya qo'llanadi,
  256px WebP thumbnail; dekompressiya bombasiga qarshi piksel chegarasi.
- Ommaviy kalitlarda user ID yo'q (`avatar/2026/10/<uuid>.jpg`) — havoladan egasini bilib bo'lmaydi.
- Maxfiy hujjatlar (`private-documents`) — faqat ≤ 5 daqiqalik presigned GET; **har ochilish `audit.recorded`**.
- Egasi servis (`attach`) faylni o'zlashtirmaguncha fayl 24 soatda avtomatik o'chiriladi.

| Purpose | Ko'rinish | Formatlar | Max | Biriktira oladi |
|---|---|---|---|---|
| `avatar` | public | jpeg, png, webp | 5 MB | user-service |
| `doctor_photo` | public | jpeg, png, webp | 5 MB | catalog-service |
| `clinic_photo` | public | jpeg, png, webp | 10 MB | catalog-service |
| `doctor_document` | private | pdf, jpeg, png | 15 MB | catalog-service |
| `dispute_evidence` | private | pdf, jpeg, png, webp | 15 MB | dispute-service |

## Endpointlar
| Metod | Yo'l | Auth | Tavsif |
|---|---|---|---|
| GET | `/health/live`, `/health/ready`, `/metrics` | — | ready: Postgres + S3 |
| POST | `/v1/files/uploads` | user JWT | `{purpose, content_type, size, filename}` → `{file_id, upload_url, method, headers, expires_in}` |
| POST | `/v1/files/{id}/complete` | user JWT (egasi) | Tekshiruv → `{status, url, thumbnail_url}`; idempotent |
| GET | `/v1/files/{id}` | user JWT (egasi) | Metadata |
| GET | `/v1/files/{id}/url` | user JWT (egasi) | public — doimiy havola; private — ≤ 300 s (audit) |
| DELETE | `/v1/files/{id}` | user JWT (egasi) | Biriktirilmagan faylni o'chirish |
| GET | `/v1/admin/files/{id}`, `/v1/admin/files/{id}/url` | admin `files.read` | Moderatsiya: maxfiy hujjat (audit) |
| POST | `/internal/v1/files/{id}/attach` | internal: user, catalog, dispute | `{owner_user_id, owner_ref, purpose?}` — faqat shu user yuklagan, `complete` dan o'tgan va purpose shu servisga ruxsat etilgan fayl; o'sha `owner_ref` bilan idempotent |
| POST | `/internal/v1/files/{id}/release` | internal | `{owner_ref}` — egasi servis endi muhtoj emas (masalan, avatar almashdi) → obyekt o'chiriladi |
| GET | `/internal/v1/files/{id}/url?ttl=&viewer_type=&viewer_id=` | internal | Chaqiruvchi o'zi biriktirgan fayl; `viewer_*` auditga |

Xato kodlari: `UNKNOWN_PURPOSE`, `CONTENT_TYPE_NOT_ALLOWED`, `FILE_TOO_LARGE`, `FILE_REJECTED`, `UPLOAD_MISSING`,
`INVALID_FILE_STATE`, `ATTACH_NOT_ALLOWED`, `TOO_MANY_PENDING_UPLOADS`, `FILE_NOT_FOUND`, `STORAGE_UNAVAILABLE`.

## Eventlar
| Publish | Consume |
|---|---|
| `audit.recorded` (maxfiy fayl ochilishi) | — |

## Sinxron bog'liqliklar
Chaqiradi: — (faqat S3). Chaqiruvchilar (`/internal/v1`): user-service, catalog-service, dispute-service.

## Konteynerlar
`file-service` (API), `-outbox-relay` (audit eventlari), `-scheduler` (soatlik tozalash), `-migrate` (profil).
Event consumer va Redis yo'q.

## Ishga tushirish
```bash
uv sync
uv run pytest                             # unit + integratsiya (Docker: Postgres, SeaweedFS — infra entrypoint bilan)
uv run ruff check . && uv run mypy app

cp .env.example .env                      # S3_ACCESS_KEY/S3_SECRET_KEY = backend/infra/.env dagi S3_FILE_SERVICE_*
docker compose up -d --build
docker compose --profile migrate run --rm file-service-migrate
curl localhost:8104/health/ready
```

## Ma'lumot o'sishi
| Jadval / obyekt | Qancha o'sadi | Kim tozalaydi |
|---|---|---|
| `files` | har yuklash | scheduler: biriktirilmagan — 24 soatda `deleted` (qator audit uchun qoladi) |
| S3 obyektlari | har fayl (+ thumbnail) | scheduler (yetim), `release`, egasi `DELETE` |
| `outbox_events` | har audit | relay: `published` 7 kundan keyin |
