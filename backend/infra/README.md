# backend/infra — umumiy infratuzilma

RabbitMQ (+ topologiya bootstrap), S3 obyekt ombori (SeaweedFS, ADR-0010), Jaeger, Prometheus (alertlar
bilan), Grafana va `medbron-net` tarmog'i.
Portlar — `docs/SERVICE_REGISTRY.md` 3-bo'lim (hammasi `127.0.0.1` ga bog'langan).

```bash
cp .env.example .env     # parollarni to'ldiring: openssl rand -hex 24
docker compose up -d --build
```

| Komponent | Manzil |
|---|---|
| RabbitMQ UI | http://127.0.0.1:15672 (`.env` dagi login; `guest` o'chirilgan) |
| S3 | http://127.0.0.1:9000 (ichkarida `http://medbron-s3:8333`), bucketlar `public-media`, `private-documents` |
| Jaeger | http://127.0.0.1:16686 (OTLP: `medbron-jaeger:4317`) |
| Prometheus | http://127.0.0.1:9090 (alertlar: `prometheus/alerts.yml`) |
| Grafana | http://127.0.0.1:3000 |

`dlx-bootstrap` bir marta ishlaydi (`restart: "no"`) va `libs/py-common` dagi `declare_topology()` ni
chaqiradi: `medbron.events` (topic), `medbron.dlx` → `medbron.dlq` (quorum), `medbron.retry` (headers) →
`medbron.retry.{5s,30s,5m}`. Healthcheck — TCP (`rabbitmq-diagnostics ping` emas, BLUEPRINT 8.1).
DLQ bilan ishlash: `docs/runbooks/dlq.md`.

S3 (`s3/entrypoint.sh`): sirlar faqat `.env` dan, `s3.json` konteyner ichida generatsiya qilinadi.
`public-media` — anonim o'qish, `private-documents` — faqat presigned URL; file-service kaliti faqat
shu ikki bucket bilan ishlaydi va bucket yarata olmaydi. Jarayon `seaweed` (root emas) foydalanuvchisida.
Bucketlar ro'yxati: `docker exec medbron-s3 sh -c 'echo s3.bucket.list | weed shell -master=127.0.0.1:9333'`.
