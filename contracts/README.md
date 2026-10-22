# contracts/ — servislararo kontraktlar

Kod yozishdan **oldin** shu yerda o'zgaradi (BLUEPRINT 1-bo'lim, 9-tamoyil).

| Papka | Mazmun |
|---|---|
| `events/catalog.yaml` | Har event: versiya, publisher, consumerlar, sxema, tavsif. **Yagona haqiqat manbai** |
| `events/_envelope.v1.json` | Barcha eventlar uchun umumiy konvert |
| `events/<event_type>.v<N>.json` | Har event `data` qismining JSON Schema'si (draft 2020-12) |
| `openapi/<servis>.internal.yaml` | `/internal/v1/*` endpointlari (har servis o'z bosqichida qo'shadi) |

## Yangi event qo'shish
1. `catalog.yaml` ga yozuv (publisher, consumerlar, tavsif).
2. `events/<type>.v1.json` — `allOf: [_envelope.v1.json]`, `data` uchun `additionalProperties: false`.
3. `libs/py-common` va `libs/go-common` dagi `Events` konstantalarini yangilash.
4. Publisher testida payload sxemaga qarshi tekshiriladi; CI `scripts/check-contracts.py`.

## Qoidalar
- Nom: `<domen>.<o'tgan_zamon_fe'l>` (`booking.confirmed`), routing key = `event_type`.
- Pul: `*_tiyin` butun son. Vaqt: RFC3339 UTC. ID: UUID.
- Buzuvchi o'zgarish = yangi versiya (`ARCHITECTURE.md` 8.2).
- Publisher'siz consumer va katalogda yo'q publish — CI xatosi.
