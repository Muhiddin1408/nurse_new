"""Testlar uchun umumiy fixture'lar.

Bu fayl loyiha ildizida turadi — shuning uchun undagi fixture'lar BARCHA
test fayllariga avtomatik ko'rinadi (`apps/booking/tests.py`,
`apps/payment/tests.py` va keyin qo'shiladiganlar).

QOIDA: fixture faqat MINIMAL kerakli grafni quradi. Har bir testga "to'liq
bo'yalgan" ma'lumot bermang — keyin test nimaga tayanayotganini o'qib
bo'lmaydi va bitta fixture o'zgarganda o'nta test sinadi.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


@pytest.fixture
def clinic(db):
    from apps.catalog.models import Clinic

    return Clinic.objects.create(
        name="Shifo klinikasi",
        phone="+998712000000",
        city="Toshkent",
        street="Amir Temur 1",
        latitude=Decimal("41.311081"),
        longitude=Decimal("69.240562"),
        status=Clinic.Status.ACTIVE,
    )


@pytest.fixture
def specialization(db):
    from apps.catalog.models import Specialization

    return Specialization.objects.create(name="Terapevt", slug="terapevt")


@pytest.fixture
def doctor(db, specialization):
    """Bron QABUL QILA OLADIGAN shifokor.

    `status=APPROVED` va `user.is_active=True` — `catalog_services.is_doctor_bookable`
    aynan shu ikki shartni tekshiradi. Fixture'ni "tasdiqlangan" holatda
    berishimiz ataylab: testlarning ko'pchiligi bandlik mantiqini sinaydi,
    moderatsiyani emas.
    """
    from apps.account.models import User
    from apps.catalog.models import Doctor

    user = User.objects.create_user(
        phone="+998901110001", full_name="Dr. Aliyev", role=User.Role.DOCTOR
    )
    doc = Doctor.objects.create(
        user=user,
        status=Doctor.Status.APPROVED,
        experience_years=10,
        accepts_home_visits=True,
    )
    doc.specializations.add(specialization)
    return doc


@pytest.fixture
def service(db, doctor, specialization):
    from apps.catalog.models import Service

    return Service.objects.create(
        doctor=doctor,
        specialization=specialization,
        name="Konsultatsiya",
        place=Service.Place.CLINIC,
        duration_minutes=30,
        price=Decimal("150000.00"),
    )


# ---------------------------------------------------------------------------
# Mijoz tomoni
# ---------------------------------------------------------------------------


@pytest.fixture
def client_user(db):
    from apps.account.models import User

    return User.objects.create_user(phone="+998901110002", full_name="Mijoz")


@pytest.fixture
def patient(db, client_user):
    from apps.account.models import Patient

    return Patient.objects.create(
        owner=client_user,
        full_name="Mijoz",
        relation="O'zim",
        birth_date="1990-01-01",
        gender=Patient.Gender.MALE,
    )


# ---------------------------------------------------------------------------
# To'lov testlari uchun
# ---------------------------------------------------------------------------


@pytest.fixture
def pending_booking(db, doctor, clinic, client_user, patient, service):
    """To'lov kutayotgan bron: Booking=PENDING_PAYMENT, Slot=HELD, Payment=CREATED.

    `api/payments/webhook.py` dagi `_perform` / `_cancel` aynan shu holatdan
    boshlanadi, shuning uchun fixture ham shu holatni beradi.

    ⚠️ Slot HELD bo'lishi SHART: `confirm_slot` compare-and-set bilan
    `HELD -> BOOKED` qiladi, FREE slotda u `SlotNotAvailable` ko'taradi.
    Ya'ni bu fixture'dagi "HELD" — dekoratsiya emas, testning ishlashi
    uchun zarur shart.
    """
    from apps.booking.models import Booking, BookingItem
    from apps.payment.models import Payment
    from apps.schedule.models import TimeSlot

    start = (timezone.now() + timedelta(hours=3)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(
        doctor=doctor,
        clinic=clinic,
        start_at=start,
        end_at=start + timedelta(minutes=service.duration_minutes),
        status=TimeSlot.Status.HELD,
        hold_expires_at=timezone.now() + timedelta(minutes=10),
    )

    booking = Booking.objects.create(
        number="MB-TEST-0001",
        client=client_user,
        patient=patient,
        doctor=doctor,
        slot=slot,
        status=Booking.Status.PENDING_PAYMENT,
        total_price=service.price,
        # B10: to'liq oldindan to'lash — onlayn summa to'liq narxga teng
        prepay_amount=service.price,
    )
    BookingItem.objects.create(
        booking=booking,
        service=service,
        service_name=service.name,
        price=service.price,
        duration_minutes=service.duration_minutes,
    )

    Payment.objects.create(
        booking=booking,
        provider=Payment.Provider.PAYME,
        amount=booking.total_price,
        status=Payment.Status.CREATED,
        idempotency_key=f"booking-payment-{booking.id}",
    )

    return booking


@pytest.fixture(autouse=True)
def _toza_kesh():
    """A7: throttle hisoblagichlari keshda — testlar bir-biriga limit "qarz" bermasin."""
    from django.core.cache import cache

    cache.clear()
    yield
