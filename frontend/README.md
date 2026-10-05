# Turon Clinic — web frontend

React 19 + TypeScript + Vite + Tailwind CSS. TanStack Query, React Router, framer-motion, lucide-react.
Bitta ilovada 4 ta kabinet, rol bo'yicha guard bilan:

| Yo'l | Rol | Tarkib |
|---|---|---|
| `/`, `/doctors`, `/doctors/:id`, `/clinics` | ochiq | Landing, qidiruv + filtr, shifokor profili, klinikalar |
| `/login` | ochiq | Telefon → SMS kod → (yangi bo'lsa) profil → (bir nechta rol bo'lsa) rol tanlash |
| `/book/:doctorId` | mijoz | 4 qadamli bron: xizmat → vaqt → bemor/manzil → tasdiq (promo, narx preview, Idempotency-Key, 409 "band") |
| `/account/*` | mijoz | Bronlar (to'lov, bekor qilish preview, ko'chirish, sharh, nizo, qayta bron), bemorlar, manzillar, sevimlilar, paketlar, navbatlar, sozlamalar |
| `/doctor/*` | shifokor | Onboarding, Bugun, qabullar, jadval, xizmatlar, moliya, sharhlar, klinikalar, profil |
| `/clinic/*` | klinika admini | Boshqaruv, qabullar, jadval, shifokorlar, xonalar, qoidalar, xizmatlar, dam olish kunlari, hisobotlar, profil |
| `/admin/*` | moderator | Shifokorlar navbati + hujjatlar, sharhlar, to'lovlar, nizolar |

## Ishga tushirish (lokal)

```powershell
# 1) Backend (loyiha ildizida)
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py seed_demo          # demo ma'lumotlar (qayta: --reset)
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000

# 2) Frontend
cd frontend
npm install
npm run dev                                          # http://127.0.0.1:5173
```

Backend CORS bermaydi, shuning uchun `/api` so'rovlari Vite proxy orqali `127.0.0.1:8000` ga ketadi
(`vite.config.ts`, boshqa manzil uchun `VITE_BACKEND_URL`). Prod build: `npm run build` → `dist/`
(uni backend bilan bir domendan, `/api` ni Django'ga proxy qilib xizmat qiling).

## Demo kirish

DEBUG + `SMS_PROVIDER=console` rejimida SMS yuborilmaydi — kod login ekranida ko'rinadi (bir bosishda kiritiladi).

| Telefon | Kim |
|---|---|
| +998 90 123-45-67 | Mijoz (2 bemor, 1 manzil) |
| +998 90 000-00-01 | Shifokor — Dr. Aziza Karimova (mijoz rejimi ham bor) |
| +998 90 000-01-00 | Klinika admini — Turon Clinic Chilonzor |
| +998 90 000-09-99 | Moderator |

Bir raqamga 60 soniyada bitta kod, soatiga 5 ta (backend himoyasi).

## Frontend uchun backendga kiritilgan o'zgarishlar

- `BookingSerializer` — faqat o'qish uchun maydonlar: `doctor_id`, `doctor_name`, `patient_name`, `start_at`, `end_at`, `place`, `clinic_name`, `clinic_address`, `address_text`, `payment_mode`, `prepay_amount` (ro'yxat qo'shimcha so'rovsiz chiziladi).
- Slot javobiga `clinic_id` (null = uy chaqiruvi) — klinika va uy slotlarini ajratish uchun.
- `otp/request` javobida `debug_code` — **faqat** `DEBUG` va `SMS_PROVIDER=console` bo'lsa (prodda console taqiqlangan).
- DEBUG'da throttle limitlari yuqoriroq (barcha so'rovlar proxy orqali bitta IP'dan keladi). Prod qiymatlari o'zgarmagan.
- `manage.py seed_demo` — demo ma'lumotlar (faqat DEBUG).

## Ma'lum cheklovlar

- To'lov (Payme/Click) lokalda yakunlanmaydi — webhook uchun ochiq HTTPS manzil kerak (`QOLDA_BAJARILADIGAN.md`, 2-band). Demo shifokorlar asosan "klinikada to'lash" rejimida, shuning uchun bron darhol tasdiqlanadi; uy chaqiruvi va depozit rejimidagi bronlar "To'lov kutilmoqda" holatida qoladi.
- Tokenlar `localStorage` da saqlanadi (backend httpOnly cookie bermaydi).
- Ko'p klinikali admin uchun klinikalar ro'yxati endpointi yo'q — klinika ID'si qo'lda kiritiladi.
- Moderator nizolarda bron summasini ko'rmaydi (Dispute javobida faqat `booking_id`).
