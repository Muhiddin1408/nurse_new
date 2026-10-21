# ADR-0010: Obyekt ombori — S3 API, dev/self-hosted'da SeaweedFS

- **Holat:** Qabul qilingan (2-bosqich) · 2026-10-21

## Kontekst
Reestr va ARCHITECTURE file-service uchun MinIO'ni ko'zda tutgan edi. 2-bosqichda ma'lum bo'ldi:
MinIO community image'lari endi tarqatilmaydi (`minio/minio`, `quay.io/minio/minio` — pull rad etiladi,
faqat manba kod). Image'ni o'zimiz yig'ish = har xavfsizlik yamog'ini o'zimiz kuzatish.

## Qaror
- file-service **faqat S3 API** bilan ishlaydi (`boto3`, SigV4, path-style). Provayderga xos chaqiruv yo'q.
- Lokal/dev va self-hosted prod uchun — **SeaweedFS 4.48** (Apache-2.0, `chrislusf/seaweedfs`),
  konteyner `medbron-s3`. Bucketlar va kirish huquqlari `backend/infra/s3/entrypoint.sh` da:
  - `public-media` — anonim o'qish (avatar, shifokor/klinika rasmi);
  - `private-documents` — faqat qisqa muddatli presigned URL (diplom, litsenziya, nizo dalili);
  - file-service o'z kaliti bilan faqat shu ikki bucketga yoza/o'qiy oladi, bucket yarata olmaydi;
  - admin kaliti — faqat ops.
- Sirlar faqat muhitdan; `s3.json` konteyner ichida har ishga tushishda generatsiya qilinadi.
- Managed S3 (AWS, Yandex Object Storage) yoki MinIO Enterprise'ga o'tish — faqat file-service
  `.env` dagi `S3_ENDPOINT_URL` / `S3_PUBLIC_ENDPOINT_URL` / kalitlar.

## Tekshirilgan
Presigned PUT (Content-Type imzoga kiradi — boshqa tur bilan yuklash 403), anonim GET public'da 200 va
private'da 403, presigned GET private'da 200, Range GET, file-service kaliti bilan `CreateBucket` — 403.

## Oqibatlar
- (+) Vendor lock-in yo'q; dev va prod bir xil API.
- (−) SeaweedFS konsoli MinIO'nikidek qulay emas (ops `weed shell` ishlatadi).
- (−) Bitta tugunli dev konfiguratsiya; prod'da replikatsiya (`-defaultReplication`) yoki managed S3.
