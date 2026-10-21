# ADR-0004: OTP auth-service'ga tegishli, otp-service yo'q

- **Holat:** Qabul qilingan (2-bosqichda amalga oshirildi) · 2026-10-21

## Kontekst
BLUEPRINT katalogida alohida otp-service (Go) bor. Trasso'da "ikki xil OTP yo'li" xato bo'lgan
(BLUEPRINT 12-bo'lim, #11). MedBron'da OTP faqat login uchun ishlatiladi.

## Qaror
OTP generatsiya, saqlash (hash), rate-limit va SMS yuborish — auth-service. OTP SMS notification-service
orqali **emas**, to'g'ridan-to'g'ri SMS provayder adapteri (`libs/py-common/sms`) bilan yuboriladi:
login yo'lida asinxron navbat kechikishi bo'lmasligi kerak.

## Oqibatlar
- (+) Login uchun bitta egasi, bitta servis.
- (−) SMS provayder adapteri ikki joyda ishlatiladi (auth va notification) — shuning uchun u libs'da.
