# Umumiy test vektorlari (Python ↔ Go)

`libs/py-common` va `libs/go-common` bir xil kontraktni ikki tilda amalga oshiradi. Bu yerdagi
fayllar ikkala test to'plamida o'qiladi — biri o'zgarib, ikkinchisi eskirib qolsa CI yiqiladi.

| Fayl | Nimani tekshiradi |
|---|---|
| `kid-vector.json` | JWT `kid` hisoblash (`key_id` / `auth.KeyID`) bir xil |
| `envelope-vector.json` | Python chiqargan envelope Go'da o'qiladi va sxemadan o'tadi |

Faqat public key va soxta ma'lumot — sir yo'q.
