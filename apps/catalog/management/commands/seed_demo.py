"""
catalog/management/commands/seed_demo.py — lokal demo ma'lumotlar (Turon Clinic web frontend uchun)

Ishlatish:
    python manage.py seed_demo            # bir marta; qayta ishga tushirish xavfsiz
    python manage.py seed_demo --reset    # demo yozuvlarni o'chirib qaytadan

Yaratadi: mutaxassisliklar, klinikalar, tasdiqlangan shifokorlar (xizmat, paket,
ish jadvali, slotlar, sharhlar), demo mijoz, klinika admini, moderator va
moderatsiyani kutayotgan shifokor. Faqat DEBUG rejimida ishlaydi.

Demo kirish raqamlari (OTP kod lokal rejimda ekranda ko'rinadi):
    +998901234567  — mijoz
    +998900000001  — shifokor (Dr. Aziza Karimova)
    +998900000100  — klinika admini (Turon Clinic Chilonzor)
    +998900000999  — moderator (platform admin)
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from api.booking.reviews import recalculate_doctor_rating
from api.schedule.services import LOCAL_TZ, generate_slots
from apps.account.models import Address, Patient, User
from apps.booking.models import Booking, BookingItem, Review
from apps.catalog.models import (
    Clinic,
    ClinicMembership,
    Doctor,
    DoctorAffiliation,
    Room,
    Service,
    ServicePackage,
    Specialization,
)
from apps.schedule.models import TimeSlot, WorkingRule

DEMO_PREFIX = "+998900000"  # + 3 xona = +998 va 9 raqam

SPECIALIZATIONS = [
    ("Terapevt", "Терапевт", "terapevt", False),
    ("Kardiolog", "Кардиолог", "kardiolog", False),
    ("Pediatr", "Педиатр", "pediatr", True),
    ("Nevrolog", "Невролог", "nevrolog", False),
    ("Stomatolog", "Стоматолог", "stomatolog", False),
    ("Ginekolog", "Гинеколог", "ginekolog", False),
    ("Dermatolog", "Дерматолог", "dermatolog", False),
    ("Oftalmolog", "Офтальмолог", "oftalmolog", False),
    ("LOR", "ЛОР", "lor", False),
    ("Hamshira", "Медсестра", "hamshira", False),
]

CLINICS = [
    ("Turon Clinic Chilonzor", "Toshkent", "Chilonzor tumani, Bunyodkor ko'chasi 12", "41.285600", "69.203700", "+998712000101"),
    ("Turon Clinic Yunusobod", "Toshkent", "Yunusobod tumani, Amir Temur shoh ko'chasi 108", "41.364900", "69.288400", "+998712000102"),
    ("Turon Clinic Mirzo Ulug'bek", "Toshkent", "Mirzo Ulug'bek tumani, Buyuk Ipak Yo'li 45", "41.326500", "69.334800", "+998712000103"),
]

# (ism, mutaxassislik slug'lari, tajriba, uy chaqiruvi, klinika indekslari, bio, to'lov rejimi)
DOCTORS = [
    ("Aziza Karimova", ["terapevt"], 12, True, [0], "Umumiy amaliyot shifokori. Shamollash, bosim, qandli diabet va profilaktik ko'riklar bo'yicha 12 yillik tajriba.", "at_clinic"),
    ("Jamshid Rahimov", ["kardiolog"], 18, False, [0, 1], "Yurak-qon tomir kasalliklari, EKG va exokardiografiya. Toshkent tibbiyot akademiyasi dotsenti.", "at_clinic"),
    ("Malika Yusupova", ["pediatr"], 9, True, [1], "Bolalar shifokori: chaqaloqlar parvarishi, emlash jadvali, bolalar infeksiyalari.", "at_clinic"),
    ("Sardor Toshmatov", ["nevrolog"], 14, False, [2], "Bosh og'rig'i, uyqusizlik, bel va bo'yin og'riqlari. Zamonaviy diagnostika usullari.", "at_clinic"),
    ("Dilnoza Abdullayeva", ["stomatolog"], 7, False, [0, 2], "Estetik stomatologiya, plombalash, tish oqartirish va bolalar stomatologiyasi.", "at_clinic"),
    ("Nodira Xasanova", ["ginekolog"], 16, False, [1], "Ayollar salomatligi, homiladorlikni kuzatish, UZI diagnostika.", "deposit"),
    ("Bekzod Ergashev", ["dermatolog"], 6, False, [2], "Teri kasalliklari, akne, allergik toshmalar va kosmetologik muolajalar.", "at_clinic"),
    ("Kamola Nazarova", ["oftalmolog"], 11, False, [0], "Ko'rish qobiliyatini tekshirish, ko'zoynak tanlash, glaukoma va katarakta.", "at_clinic"),
    ("Otabek Saidov", ["lor"], 10, True, [1, 2], "Quloq, tomoq, burun kasalliklari. Bolalar va kattalar qabuli.", "at_clinic"),
    ("Gulnora Mirzayeva", ["hamshira", "terapevt"], 15, True, [0], "Tajribali hamshira: ukol, tomchi, perevyazka, qon olish — uyingizda.", "at_clinic"),
]

SERVICES = {
    "terapevt": [("Birlamchi konsultatsiya", 30, "150000"), ("Takroriy qabul", 20, "100000"), ("Profilaktik ko'rik", 30, "180000")],
    "kardiolog": [("Kardiolog konsultatsiyasi", 30, "250000"), ("EKG + xulosa", 20, "120000"), ("Exokardiografiya", 30, "300000")],
    "pediatr": [("Bolalar konsultatsiyasi", 30, "180000"), ("Chaqaloq ko'rigi", 30, "200000")],
    "nevrolog": [("Nevrolog konsultatsiyasi", 30, "220000"), ("Takroriy qabul", 20, "150000")],
    "stomatolog": [("Stomatolog ko'rigi", 30, "100000"), ("Plomba qo'yish", 60, "350000"), ("Professional tozalash", 60, "400000")],
    "ginekolog": [("Ginekolog konsultatsiyasi", 30, "230000"), ("UZI diagnostika", 30, "200000")],
    "dermatolog": [("Dermatolog konsultatsiyasi", 30, "200000"), ("Dermatoskopiya", 20, "150000")],
    "oftalmolog": [("Ko'z ko'rigi", 30, "170000"), ("Ko'z bosimini o'lchash", 20, "90000")],
    "lor": [("LOR konsultatsiyasi", 30, "190000"), ("Quloq yuvish", 20, "120000")],
    "hamshira": [("Ukol qilish", 20, "60000"), ("Qon olish", 20, "70000")],
}

HOME_SERVICES = {
    "terapevt": [("Uyga chaqiruv — terapevt", 60, "350000")],
    "pediatr": [("Uyga chaqiruv — pediatr", 60, "380000")],
    "lor": [("Uyga chaqiruv — LOR", 60, "360000")],
    "hamshira": [("Uyda tomchi (kapelnitsa)", 60, "200000"), ("Uyda ukol", 30, "120000")],
}

REVIEWS = [
    (5, "Juda e'tiborli shifokor, hamma narsani tushuntirib berdi. Rahmat!"),
    (5, "Vaqtida qabul qilishdi, navbat umuman yo'q. Tavsiya qilaman."),
    (4, "Yaxshi mutaxassis, faqat klinikani topish biroz qiyin bo'ldi."),
    (5, "Bolam bilan bordik — juda mehribon va sabrli. Katta rahmat!"),
    (5, "Onlayn bron qilish juda qulay ekan, 2 daqiqada yozildim."),
    (4, "Professional yondashuv, tavsiyalar aniq va tushunarli."),
    (5, "Uyga chaqirdik, o'z vaqtida kelishdi. Zo'r xizmat!"),
]


def _user(phone: str, name: str, role: str = User.Role.CLIENT) -> User:
    user, _ = User.objects.get_or_create(phone=phone, defaults={"full_name": name, "role": role, "is_phone_verified": True})
    if user.full_name != name or user.role != role:
        user.full_name, user.role = name, role
        user.save(update_fields=["full_name", "role"])
    return user


class Command(BaseCommand):
    help = "Lokal demo ma'lumotlar (shifokorlar, klinikalar, slotlar, demo foydalanuvchilar)"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Demo yozuvlarni o'chirib qaytadan yaratish")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo faqat DEBUG rejimida ishlaydi")
        random.seed(42)
        if options["reset"]:
            self._reset()
        with transaction.atomic():
            specs = self._specializations()
            clinics = self._clinics()
            doctors = self._doctors(specs, clinics)
            self._staff(clinics)
            client = self._client()
        total = sum(generate_slots(d.id, days=21) for d in doctors)
        with transaction.atomic():
            self._reviews(doctors, client)
        self.stdout.write(self.style.SUCCESS(
            f"Tayyor: {len(specs)} mutaxassislik, {len(clinics)} klinika, {len(doctors)} shifokor, {total} yangi slot"
        ))
        self.stdout.write("Kirish: mijoz +998901234567 | shifokor +998900000001 | klinika +998900000100 | moderator +998900000999")

    # ------------------------------------------------------------------
    def _reset(self):
        demo_users = User.objects.filter(phone__regex=r"^\+99890000\d{3,4}$") | User.objects.filter(phone="+998901234567")
        Review.objects.filter(client__in=demo_users).delete()
        BookingItem.objects.filter(booking__doctor__user__in=demo_users).delete()
        Booking.objects.filter(doctor__user__in=demo_users).delete()
        Booking.objects.filter(client__in=demo_users).delete()
        TimeSlot.objects.filter(doctor__user__in=demo_users).delete()
        WorkingRule.objects.filter(doctor__user__in=demo_users).delete()
        ServicePackage.objects.filter(doctor__user__in=demo_users).delete()
        Service.objects.filter(doctor__user__in=demo_users).delete()
        DoctorAffiliation.objects.filter(doctor__user__in=demo_users).delete()
        Doctor.objects.filter(user__in=demo_users).delete()
        Patient.objects.filter(owner__in=demo_users).delete()
        Address.objects.filter(user__in=demo_users).delete()
        ClinicMembership.objects.filter(user__in=demo_users).delete()
        Room.objects.filter(clinic__name__in=[c[0] for c in CLINICS]).delete()
        Service.objects.filter(clinic__name__in=[c[0] for c in CLINICS]).delete()
        Clinic.objects.filter(name__in=[c[0] for c in CLINICS]).delete()
        demo_users.delete()
        self.stdout.write("Demo yozuvlar o'chirildi")

    def _specializations(self):
        out = {}
        for name, name_ru, slug, pediatric in SPECIALIZATIONS:
            s, _ = Specialization.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "name_ru": name_ru, "is_pediatric": pediatric, "accepts_children": True},
            )
            out[slug] = s
        return out

    def _clinics(self):
        hours = {str(d): ["08:00", "20:00"] for d in range(6)} | {"6": ["09:00", "15:00"]}
        out = []
        for name, city, street, lat, lng, phone in CLINICS:
            c, _ = Clinic.objects.get_or_create(
                name=name,
                defaults={
                    "city": city, "street": street, "latitude": Decimal(lat), "longitude": Decimal(lng),
                    "phone": phone, "status": Clinic.Status.ACTIVE, "working_hours": hours,
                    "description": "Zamonaviy uskunalar, tajribali shifokorlar va navbatsiz qabul.",
                },
            )
            for i, (room, eq) in enumerate([("101-xona", ["EKG"]), ("102-xona", ["UZI"]), ("201-xona", [])]):
                Room.objects.get_or_create(clinic=c, name=room, defaults={"floor": str(1 + i // 2), "equipment": eq})
            out.append(c)
        return out

    def _doctors(self, specs, clinics):
        out = []
        today = timezone.now().astimezone(LOCAL_TZ).date()
        for i, (name, slugs, exp, home, clinic_idx, bio, pay_mode) in enumerate(DOCTORS, start=1):
            user = _user(f"{DEMO_PREFIX}{i:03d}", f"Dr. {name}", User.Role.DOCTOR)
            doctor, created = Doctor.objects.get_or_create(
                user=user,
                defaults={
                    "bio": bio, "experience_years": exp, "education": "Toshkent tibbiyot akademiyasi",
                    "languages": ["uz", "ru"], "license_number": f"LIC-{1000 + i}",
                    "license_expires_at": today + timedelta(days=700),
                    "status": Doctor.Status.APPROVED, "accepts_home_visits": home, "home_visit_radius_km": 15,
                    "home_base_latitude": Decimal("41.311100"), "home_base_longitude": Decimal("69.279700"),
                    "clinic_payment_mode": pay_mode, "submitted_at": timezone.now(), "moderated_at": timezone.now(),
                    "follow_up_discount_percent": 10 if i % 3 == 0 else 0,
                },
            )
            out.append(doctor)
            if not created:
                continue
            doctor.specializations.set([specs[s] for s in slugs])
            main_spec = specs[slugs[0]]

            for idx in clinic_idx:
                DoctorAffiliation.objects.get_or_create(
                    doctor=doctor, clinic=clinics[idx], defaults={"position": main_spec.name, "status": "active"}
                )

            first_service = None
            for slug in slugs:
                for sname, dur, price in SERVICES.get(slug, []):
                    s = Service.objects.create(
                        doctor=doctor, clinic=clinics[clinic_idx[0]], specialization=specs[slug],
                        name=sname, place=Service.Place.CLINIC, duration_minutes=dur, price=Decimal(price),
                    )
                    first_service = first_service or s
                if home:
                    for sname, dur, price in HOME_SERVICES.get(slug, []):
                        Service.objects.create(
                            doctor=doctor, specialization=specs[slug], name=sname,
                            place=Service.Place.HOME, duration_minutes=dur, price=Decimal(price),
                        )
            if first_service:
                ServicePackage.objects.create(
                    doctor=doctor, service=first_service, name=f"{first_service.name} — 5 seans",
                    sessions=5, discount_percent=15, validity_days=90,
                )

            # Klinika: Du–Ju 09:00–14:00 (+ shanba 10:00–14:00), uy: Du–Ju 15:00–19:00
            slot_minutes = 60 if "stomatolog" in slugs else 30
            for wd in range(5):
                WorkingRule.objects.create(
                    doctor=doctor, clinic=clinics[clinic_idx[wd % len(clinic_idx)]], weekday=wd,
                    start_time=time(9), end_time=time(14), slot_minutes=slot_minutes, valid_from=today,
                )
                if home:
                    WorkingRule.objects.create(
                        doctor=doctor, clinic=None, weekday=wd, start_time=time(15), end_time=time(19),
                        slot_minutes=60, valid_from=today,
                    )
            WorkingRule.objects.create(
                doctor=doctor, clinic=clinics[clinic_idx[0]], weekday=5,
                start_time=time(10), end_time=time(14), slot_minutes=slot_minutes, valid_from=today,
            )

        # Moderatsiya navbati uchun: hujjat yuborgan, tasdiqlanmagan shifokor
        pending_user = _user(f"{DEMO_PREFIX}050", "Dr. Ravshan Qodirov", User.Role.DOCTOR)
        pd, created = Doctor.objects.get_or_create(
            user=pending_user,
            defaults={
                "bio": "Endokrinolog, 8 yillik tajriba.", "experience_years": 8, "languages": ["uz", "ru"],
                "license_number": "LIC-2050", "status": Doctor.Status.PENDING, "submitted_at": timezone.now(),
                "education": "Samarqand tibbiyot universiteti",
            },
        )
        if created:
            pd.specializations.set([specs["terapevt"]])
        return out

    def _staff(self, clinics):
        admin = _user(f"{DEMO_PREFIX}100", "Laylo Aminova", User.Role.CLINIC_ADMIN)
        ClinicMembership.objects.get_or_create(user=admin, clinic=clinics[0], defaults={"is_active": True})
        _user(f"{DEMO_PREFIX}999", "Platforma moderatori", User.Role.PLATFORM_ADMIN)

    def _client(self):
        user = _user("+998901234567", "Shahzod Turonov")
        if not Patient.objects.filter(owner=user).exists():
            Patient.objects.create(owner=user, full_name="Shahzod Turonov", relation="O'zim", birth_date=date(1995, 4, 12), gender="male", weight_kg=74)
            Patient.objects.create(owner=user, full_name="Mohinur Turonova", relation="Qizim", birth_date=date(2019, 9, 3), gender="female", weight_kg=19)
        if not Address.objects.filter(user=user).exists():
            Address.objects.create(
                user=user, label="Uy", city="Toshkent", street="Chilonzor 9-kvartal, 14-uy", entrance="2", floor="4",
                apartment="38", latitude=Decimal("41.288000"), longitude=Decimal("69.205000"), is_default=True,
            )
        return user

    def _reviews(self, doctors, client):
        """Har shifokorga o'tgan, yakunlangan qabullar va ularning sharhlari — reyting ko'rinsin."""
        reviewers = [_user(f"{DEMO_PREFIX}{700 + k}", n) for k, n in enumerate(
            ["Aziz T.", "Nilufar S.", "Jasur M.", "Zarina K.", "Bobur A.", "Madina R.", "Sherzod X."]
        )]
        now = timezone.now()
        for d_idx, doctor in enumerate(doctors):
            if Review.objects.filter(doctor=doctor).exists():
                continue
            service = Service.objects.filter(doctor=doctor, place=Service.Place.CLINIC).first()
            clinic = DoctorAffiliation.objects.filter(doctor=doctor).first().clinic
            count = 5 + (d_idx % 3)
            for k in range(count):
                reviewer = reviewers[k % len(reviewers)]
                patient, _ = Patient.objects.get_or_create(
                    owner=reviewer, full_name=reviewer.full_name,
                    defaults={"relation": "O'zim", "birth_date": date(1990, 1, 1), "gender": "male"},
                )
                start = (now - timedelta(days=3 + k * 4)).replace(minute=0, second=0, microsecond=0)
                slot = TimeSlot.objects.create(
                    doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30),
                    status=TimeSlot.Status.BOOKED,
                )
                booking = Booking.objects.create(
                    number=f"TC-D{d_idx:02d}{k:02d}{random.randint(100, 999)}", client=reviewer, patient=patient,
                    doctor=doctor, slot=slot, status=Booking.Status.COMPLETED, total_price=service.price,
                    payment_mode="at_clinic", completed_at=start + timedelta(minutes=30),
                )
                BookingItem.objects.create(booking=booking, service=service, service_name=service.name,
                                           price=service.price, duration_minutes=service.duration_minutes)
                rating, text = REVIEWS[(k + d_idx) % len(REVIEWS)]
                if d_idx == 3 and k == 0:
                    rating = 3
                Review.objects.create(booking=booking, doctor=doctor, client=reviewer, rating=rating, comment=text,
                                      doctor_reply="Rahmat! Sog'ligingizni asrang." if k == 0 else "")
            recalculate_doctor_rating(doctor.id)
