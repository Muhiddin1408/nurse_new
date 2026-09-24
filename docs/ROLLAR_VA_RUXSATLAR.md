# Rollar va ruxsatlar matritsasi (D1)

Rol `User.role` ning bitta qiymati emas — ma'lumotdan hisoblanadi (`api/roles.py`):

| Rol | Qachon beriladi |
|---|---|
| `client` | Har doim |
| `doctor` | `Doctor` profili bor (ish endpointlari faqat `approved` holatda) |
| `clinic_admin` | Faol `ClinicMembership` bor |
| `platform_admin` | `User.role = platform_admin` yoki superuser |

Token'da `available_roles` va `active_role`. Almashtirish: `POST /api/v1/accounts/auth/switch-role`.
**Ruxsat sinflari holatni bazadan tekshiradi** — token claim'i faqat interfeys uchun ishora.

## Ruxsat sinflari (`api/permissions.py`)

| Sinf | Shart | Qo'shimcha |
|---|---|---|
| `IsDoctor` | Doctor profili bor | `request.doctor` |
| `IsApprovedDoctor` | + `status = approved` | `request.doctor` |
| `IsClinicAdmin` | faol `ClinicMembership` | `request.clinic_ids` |
| `IsPlatformAdmin` | `role = platform_admin` / superuser | — |

Obyekt darajasi: shifokor faqat `doctor=request.doctor` bo'yicha filtrlangan querysetni ko'radi (A4).
Begona obyekt — **404** (403 emas: mavjudligi oshkor bo'lmaydi).

## Matritsa

| Amal | client | doctor | clinic_admin | platform_admin |
|---|:---:|:---:|:---:|:---:|
| Bron yaratish | ✅ o'ziga | ❌ | ❌ | ✅ (qo'ng'iroq orqali) |
| Bronni ko'rish | ✅ o'zinikini | ✅ o'ziga tegishlini | ✅ klinikasinikini | ✅ hammasini |
| `completed` / `no_show` belgilash | ❌ | ✅ o'z qabulini | ✅ | ✅ |
| Qabulni bekor qilish | ✅ siyosat bo'yicha | ✅ har doim 100% refund | ✅ | ✅ |
| Jadval o'zgartirish | ❌ | ✅ o'zinikini | ✅ klinikasidagini | ✅ |
| Narx o'zgartirish | ❌ | ✅ shaxsiy xizmatini | ✅ klinika xizmatini | ✅ |
| Onboarding (profil, hujjat) | ✅ boshlash | ✅ draft/rejected da | ❌ | ❌ |
| Shifokorni tasdiqlash / to'xtatish | ❌ | ❌ | ❌ | ✅ |
| Hujjatlarni ko'rish | ❌ | ✅ o'zinikini | ❌ | ✅ |
| Daromad / payout | ❌ | ✅ o'zinikini | ✅ klinikasiniki | ✅ |
| Refund | ❌ | ❌ | ⚠️ so'rov yubora oladi | ✅ |
| Nizoni hal qilish | ❌ (ochadi) | ❌ | ❌ | ✅ |
