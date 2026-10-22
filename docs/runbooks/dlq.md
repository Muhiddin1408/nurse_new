# Runbook: DLQ bo'sh emas (`DeadLetterQueueNotEmpty`, `ConsumerDeadLettering`)

**Ma'nosi:** consumer eventni qayta ishlay olmadi va u `medbron.dlq` ga tushdi. Event yo'qolmagan,
lekin uning effekti (bron tasdig'i, refund, SMS...) **hali sodir bo'lmagan**. DLQ hech qachon
avtomatik tozalanmaydi — har xabar ko'rib chiqiladi.

## 1. Nima tushganini ko'rish
```bash
RABBITMQ_URL=amqp://USER:PASS@127.0.0.1:5672/ \
  libs/py-common/.venv/bin/python -m medbron_common.eventbus.dlq list
```
Natija: `queue` (qaysi consumer), `event_type`, `reason`, `count`. RabbitMQ UI: `http://127.0.0.1:15672` →
Queues → `medbron.dlq` → *Get messages* (`x-last-error` header'i — xato matni).

## 2. Sabab bo'yicha qaror
| `reason` | Ma'nosi | Harakat |
|---|---|---|
| `retries_exhausted` | Vaqtinchalik xato 3 marta takrorlandi (DB, tarmoq, bog'liq servis) | Bog'liqlikni tiklang → replay |
| `permanent_error` | Handler `PermanentError` berdi (biznes qoidasi) | Kod/ma'lumotni tuzating → replay yoki ataylab tashlab yuborish |
| `schema_mismatch` | Payload kontraktga mos emas | **Publisher'da bug.** Publisher tuzatilmaguncha replay foydasiz |
| `unknown_version` | Consumer bu `event_version` ni bilmaydi | Consumer'ni yangi versiyaga moslab deploy → replay |
| `invalid_envelope` | JSON/envelope buzilgan | Manbani toping (outbox'dan tashqari publish?) — replay qilinmaydi |
| `no_handler` | Navbatda binding bor, handler yo'q | Deploy nomuvofiqligi — handler'ni qaytaring yoki binding'ni olib tashlang |

## 3. Qayta yuborish
```bash
# avval nima tanlanishini ko'ring
python -m medbron_common.eventbus.dlq replay --queue booking-service.events --dry-run
# keyin
python -m medbron_common.eventbus.dlq replay --queue booking-service.events [--event-type payment.captured]
```
Xabar faqat o'z manba navbatiga qaytadi (boshqa consumerlar uni qayta olmaydi). Consumer idempotent —
allaqachon ishlangan event ikkinchi marta effekt bermaydi.

## 4. Pul bilan bog'liq eventlar
`payment.captured`, `booking.confirmation_failed`, `refund.*`, `dispute.resolved` DLQ da bo'lsa —
**darhol** moliya mas'uliga xabar bering: mijozdan pul yechilgan, lekin bron/refund holati yangilanmagan
bo'lishi mumkin. Replay'dan keyin `payment-service` reconciliation hisobotini tekshiring.
