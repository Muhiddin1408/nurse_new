"""
catalog/services.py — Faza 1

Katalog modulining TASHQI INTERFEYSI.

Boshqa modullar (booking, schedule) catalog bilan faqat shu fayl orqali gaplashadi.
`from catalog.models import Doctor` — bu taqiqlangan.

NEGA DTO QAYTARAMIZ, model obyektini emas:
    Agar `Service` model obyektini qaytarsak, booking moduli bexosdan
    `service.clinic.name` deb yozib qo'yishi mumkin — va bu ishlaydi.
    Django ORM jimgina qo'shimcha so'rov qiladi, chegara buzilganini
    hech kim sezmaydi. Faza 4 da servisga ajratganda esa o'sha qator
    to'satdan yiqiladi va sababini topish qiyin bo'ladi.

    DTO buni oldini oladi: unda faqat kelishilgan maydonlar bor, boshqasiga
    yo'l yo'q. Va aynan shu dataclass keyinchalik `.proto` message'ga aylanadi.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db.models import Q

from api.geo import haversine_km
from api.i18n import localized
from apps.catalog.models import (
    Clinic,
    Doctor,
    DoctorAffiliation,
    PriceRule,
    Service,
    ServicePackage,
    Specialization,
)


# ---------------------------------------------------------------------------
# DTO — modullararo kontrakt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceDTO:
    id: UUID
    name: str
    place: str  # "clinic" | "home"
    price: Decimal
    duration_minutes: int
    specialization_id: UUID
    clinic_id: UUID | None
    doctor_id: UUID | None
    name_ru: str = ""  # C14
    # B12: fiskal rekvizitlar (bo'sh — settings.FISCAL default'i)
    mxik_code: str = ""
    package_code: str = ""
    vat_percent: int | None = None


@dataclass(frozen=True)
class DoctorDTO:
    id: UUID
    full_name: str
    experience_years: int
    rating: Decimal
    reviews_count: int
    specializations: tuple[str, ...]
    accepts_home_visits: bool
    is_bookable: bool
    # C15.5: `?lat=&lng=` bilan so'ralganda — eng yaqin klinikasigacha (km)
    distance_km: float | None = None


@dataclass(frozen=True)
class ServiceContextDTO:
    """Hodisa payload'ini DENORMALIZATSIYA qilish uchun kontekst.

    Nega kerak: `booking.events` hodisasini `analytics/consumer.py` o'qiydi va
    u ClickHouse'ga JOIN QILMASDAN yozadi (join analitikada qimmat). Ya'ni
    `specialization`, `place`, `city` kabi maydonlar hodisaning O'Z ICHIDA
    kelishi kerak.

    Nega bu funksiya `catalog` da, `booking` da emas: booking moduli
    `catalog.models` ni import qilmasligi kerak (fayl boshidagi qoida).
    Kerakli maydonlarni shu yerda yig'ib, DTO ko'rinishida beramiz.
    """

    place: str  # "clinic" | "home"
    specialization_name: str
    clinic_id: UUID | None
    clinic_city: str  # klinika bo'lmasa bo'sh — uy chaqiruvida shahar manzildan olinadi


@dataclass(frozen=True)
class ClinicDTO:
    id: UUID
    name: str
    city: str
    street: str
    latitude: Decimal
    longitude: Decimal
    rating: Decimal
    reviews_count: int


# ---------------------------------------------------------------------------
# booking moduli ishlatadigan funksiyalar
# ---------------------------------------------------------------------------


def is_doctor_bookable(doctor_id: UUID) -> bool:
    """Shifokor bron qabul qila oladimi.

    Bitta joyda turgan qoida: moderatsiyadan o'tgan va foydalanuvchisi faol.
    Bu shartni view'larga tarqatib yubormang — ertaga qoida o'zgarsa
    (masalan litsenziya muddati qo'shilsa), faqat shu funksiyani tuzatasiz.
    """
    return Doctor.objects.filter(
        id=doctor_id,
        status=Doctor.Status.APPROVED,
        user__is_active=True,
    ).exists()


@dataclass(frozen=True)
class HomeVisitAreaDTO:
    """C4: shifokor qayerdan va qancha radiusda uyga boradi."""

    base_latitude: Decimal | None
    base_longitude: Decimal | None
    radius_km: int

    def distance_km(self, lat, lng) -> float | None:
        """Bazagacha masofa. Baza kiritilmagan bo'lsa None (radius tekshirilmaydi)."""
        if self.base_latitude is None or self.base_longitude is None:
            return None
        return haversine_km(self.base_latitude, self.base_longitude, lat, lng)


def get_home_visit_area(doctor_id: UUID) -> HomeVisitAreaDTO | None:
    row = (
        Doctor.objects.filter(id=doctor_id)
        .values("home_base_latitude", "home_base_longitude", "home_visit_radius_km")
        .first()
    )
    if row is None:
        return None
    return HomeVisitAreaDTO(
        base_latitude=row["home_base_latitude"],
        base_longitude=row["home_base_longitude"],
        radius_km=row["home_visit_radius_km"],
    )


@dataclass(frozen=True)
class SpecializationRulesDTO:
    """C6: mutaxassislik qaysi bemorlarni qabul qiladi."""

    id: UUID
    name: str
    is_pediatric: bool
    accepts_children: bool
    min_patient_age: int | None
    max_patient_age: int | None
    allowed_gender: str  # "" = cheklov yo'q


def get_specialization_rules(specialization_ids) -> list[SpecializationRulesDTO]:
    return [
        SpecializationRulesDTO(
            id=s.id,
            name=s.name,
            is_pediatric=s.is_pediatric,
            accepts_children=s.accepts_children,
            min_patient_age=s.min_patient_age,
            max_patient_age=s.max_patient_age,
            allowed_gender=s.allowed_gender,
        )
        for s in Specialization.objects.filter(id__in=set(specialization_ids))
    ]


def set_doctor_rating(doctor_id: UUID, *, rating: Decimal, reviews_count: int) -> None:
    """C11: reyting sharhlardan hisoblanadi (booking/reviews.py). Qidiruv
    indeksi yangilanishi uchun `DoctorRatingChanged` (outbox) — chaqiruvchi tranzaksiyasida."""
    from api.catalog.events import publish_doctor_event

    Doctor.objects.filter(id=doctor_id).update(rating=rating, reviews_count=reviews_count)
    publish_doctor_event(doctor_id, "DoctorRatingChanged")


def get_follow_up_discount_percent(doctor_id: UUID) -> int:
    """C12: takroriy qabul chegirmasi (0–100)."""
    return Doctor.objects.filter(id=doctor_id).values_list("follow_up_discount_percent", flat=True).first() or 0


def get_price_adjust_percent(doctor_id: UUID, local_dt) -> int:
    """C12 dinamik narx: slot boshlanish vaqtiga (MAHALLIY) tegishli tuzatma foizi.

    Qoida topilmasa 0. Bir nechta qoida mos kelsa — `priority` eng kichigi;
    teng bo'lsa mijoz foydasiga (eng arzoni). Bu tanlov ataylab: narx
    qoidalari qo'lda yoziladi va ustma-ust tushib qolishi oddiy hol, bunda
    mijozga qimmatroq javob berish ishonchni yo'qotadi.
    """
    rules = [
        r for r in PriceRule.objects.filter(doctor_id=doctor_id, is_active=True).order_by("priority")
        if r.matches(local_dt)
    ]
    if not rules:
        return 0
    top = rules[0].priority
    return min(r.percent for r in rules if r.priority == top)


@dataclass(frozen=True)
class PackageDTO:
    """C12: shifokorning paket taklifi."""

    id: UUID
    doctor_id: UUID
    service_id: UUID
    service_name: str
    name: str
    sessions: int
    discount_percent: int
    validity_days: int
    unit_price: Decimal

    @property
    def total_saving(self) -> Decimal:
        return (self.unit_price * self.sessions * Decimal(self.discount_percent) / 100).quantize(Decimal("0.01"))


def _package_dto(p: ServicePackage) -> PackageDTO:
    return PackageDTO(
        id=p.id, doctor_id=p.doctor_id, service_id=p.service_id, service_name=p.service.name,
        name=p.name, sessions=p.sessions, discount_percent=p.discount_percent,
        validity_days=p.validity_days, unit_price=p.service.price,
    )


def get_packages(doctor_id: UUID) -> list[PackageDTO]:
    """Shifokorning faol paketlari (xizmati ham faol bo'lishi shart)."""
    qs = ServicePackage.objects.filter(
        doctor_id=doctor_id, is_active=True, service__is_active=True
    ).select_related("service")
    return [_package_dto(p) for p in qs]


def get_package(package_id: UUID) -> PackageDTO | None:
    p = (
        ServicePackage.objects.filter(id=package_id, is_active=True, service__is_active=True)
        .select_related("service")
        .first()
    )
    return _package_dto(p) if p else None


def get_clinic_payment_mode(doctor_id: UUID) -> str:
    """B10: shifokor klinikadagi qabul uchun qaysi to'lov rejimini qabul qiladi.

    Shifokor topilmasa — eng xavfsiz holat (`prepaid`). Bu yerda "topilmadi"
    bo'lishi mumkin emas (bron validatsiyasi allaqachon o'tgan), lekin
    default kafolat tomonga qaragani yaxshi."""
    return (
        Doctor.objects.filter(id=doctor_id)
        .values_list("clinic_payment_mode", flat=True)
        .first()
        or "prepaid"
    )


def get_services(service_ids: list[UUID]) -> list[ServiceDTO]:
    """Faol xizmatlarni DTO ko'rinishida qaytaradi.

    DIQQAT: topilmagan yoki faol bo'lmagan xizmatlar ro'yxatga TUSHMAYDI.
    Chaqiruvchi tomon uzunlikni solishtirib tekshiradi — booking/services.py
    dagi `_validate_and_load_services` aynan shuni qiladi.

    Bu ataylab shunday: bu funksiya "topilmadi" degan xatoni o'zi hal qilmaydi,
    chunki har bir chaqiruvchi uni boshqacha talqin qilishi mumkin.
    """
    if not service_ids:
        return []

    rows = Service.objects.filter(id__in=service_ids, is_active=True).only(
        "id",
        "name",
        "place",
        "price",
        "duration_minutes",
        "specialization_id",
        "clinic_id",
        "doctor_id",
        "mxik_code",
        "package_code",
        "vat_percent",
        "name_ru",
    )
    return [_to_service_dto(s) for s in rows]


def get_doctor_clinic_ids(doctor_id: UUID) -> set[UUID]:
    """Shifokor faol ishlaydigan (va o'zi faol) klinikalar."""
    return set(
        DoctorAffiliation.objects.filter(
            doctor_id=doctor_id, is_active=True, clinic__status=Clinic.Status.ACTIVE
        ).values_list("clinic_id", flat=True)
    )


def get_bookable_services(doctor_id: UUID, service_ids: list[UUID]) -> list[ServiceDTO]:
    """Shu shifokorga bron qilsa bo'ladigan xizmatlar (A3/A4 loader).

    Xizmat yo shifokorning SHAXSIY xizmati, yo u faol ishlaydigan klinikaning
    xizmati bo'lishi shart. Begona shifokorning arzon xizmati bilan qimmat
    shifokorga yozilib bo'lmaydi. Mos kelmaganlar ro'yxatga TUSHMAYDI —
    chaqiruvchi uzunlikni solishtiradi (`get_services` bilan bir xil kontrakt).
    """
    if not service_ids:
        return []
    clinic_ids = get_doctor_clinic_ids(doctor_id)
    rows = Service.objects.filter(id__in=service_ids, is_active=True).filter(
        Q(doctor_id=doctor_id) | Q(doctor__isnull=True, clinic_id__in=clinic_ids)
    )
    return [_to_service_dto(s) for s in rows]


def get_clinic_city(clinic_id: UUID) -> str:
    """Klinika shahri. Topilmasa bo'sh satr.

    Hodisa payload'ini denormalizatsiya qilishda ishlatiladi.
    """
    return (
        Clinic.objects.filter(id=clinic_id).values_list("city", flat=True).first() or ""
    )


def get_service_context(service_id: UUID) -> ServiceContextDTO | None:
    """Bitta xizmatning hodisa uchun kerakli konteksti.

    `select_related` — mutaxassislik va klinika bitta so'rovda keladi.
    Bu funksiya hodisa yozilayotgan tranzaksiya ichida chaqiriladi, shuning
    uchun qo'shimcha so'rovlar sonini minimal tutish muhim.
    """
    service = (
        Service.objects.filter(id=service_id)
        .select_related("specialization", "clinic")
        .first()
    )
    if service is None:
        return None

    return ServiceContextDTO(
        place=service.place,
        specialization_name=service.specialization.name,
        clinic_id=service.clinic_id,
        clinic_city=service.clinic.city if service.clinic_id else "",
    )


def get_doctor(doctor_id: UUID) -> DoctorDTO | None:
    doctor = (
        Doctor.objects.filter(id=doctor_id)
        .select_related("user")
        .prefetch_related("specializations")
        .first()
    )
    return _to_doctor_dto(doctor) if doctor else None


def get_doctors(doctor_ids) -> list[DoctorDTO]:
    """Faqat tasdiqlangan va faol (A9). Tartib saqlanmaydi."""
    qs = (
        Doctor.objects.filter(id__in=list(doctor_ids), status=Doctor.Status.APPROVED, user__is_active=True)
        .select_related("user")
        .prefetch_related("specializations")
    )
    return [_to_doctor_dto(d) for d in qs]


@dataclass(frozen=True)
class DoctorDetailDTO:
    """Shifokor kartochkasi — `DoctorDTO` + faqat detail'da kerak og'ir maydonlar."""

    id: UUID
    full_name: str
    experience_years: int
    rating: Decimal
    reviews_count: int
    specializations: tuple[str, ...]
    accepts_home_visits: bool
    is_bookable: bool
    bio: str
    license_number: str
    home_visit_radius_km: int
    clinics: tuple[ClinicDTO, ...]
    distance_km: float | None = None


def get_doctor_detail(doctor_id: UUID) -> DoctorDetailDTO | None:
    """A9: faqat tasdiqlangan va faol shifokor. Moderatsiyadagi, to'xtatilgan
    (litsenziya muddati, shikoyat) profil to'g'ridan havola bilan ham ochilmaydi — 404."""
    doctor = (
        Doctor.objects.filter(id=doctor_id, status=Doctor.Status.APPROVED, user__is_active=True)
        .select_related("user")
        .prefetch_related("specializations", "affiliations__clinic")
        .first()
    )
    if doctor is None:
        return None

    base = _to_doctor_dto(doctor)
    clinics = tuple(
        _to_clinic_dto(a.clinic)
        for a in doctor.affiliations.all()
        if a.is_active and a.clinic.status == Clinic.Status.ACTIVE
    )
    return DoctorDetailDTO(
        **{k: v for k, v in base.__dict__.items() if k != "distance_km"},
        bio=doctor.bio,
        license_number=doctor.license_number,
        home_visit_radius_km=doctor.home_visit_radius_km,
        clinics=clinics,
    )


# ---------------------------------------------------------------------------
# Katalogni ko'rish (mijoz ilovasidagi ro'yxatlar)
# ---------------------------------------------------------------------------


def list_doctors(
    *,
    specialization_id: UUID | None = None,
    clinic_id: UUID | None = None,
    home_visits_only: bool = False,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 5,
    text: str = "",
    min_rating: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[DoctorDTO]:
    """Shifokorlar ro'yxati — dizayndagi "Gastroenterolog" ekrani.

    `select_related` va `prefetch_related` shart: ularsiz 20 ta shifokor uchun
    41 ta so'rov ketadi (N+1). Faza 6 da `django-debug-toolbar` bilan
    buni o'z ko'zingiz bilan ko'rasiz.

    Faza 5 da bu funksiya Elasticsearch'ga ko'chadi — chunki bu yerga
    geo-masofa, matn qidiruvi va murakkab saralash qo'shilganda
    PostgreSQL sekinlashadi.
    """
    qs = (
        Doctor.objects.filter(status=Doctor.Status.APPROVED, user__is_active=True)
        .select_related("user")
        .prefetch_related("specializations")
    )

    if specialization_id:
        qs = qs.filter(specializations__id=specialization_id)
    if clinic_id:
        qs = qs.filter(affiliations__clinic_id=clinic_id, affiliations__is_active=True)
    if home_visits_only:
        qs = qs.filter(accepts_home_visits=True)
    if min_rating is not None:
        qs = qs.filter(rating__gte=min_rating)
    if text:
        # C15.4: Elasticsearch yiqilganda ishlaydigan ZAXIRA yo'li. Bu ES
        # emas — imlo xatosini kechirmaydi va relevantlik bo'yicha
        # saralamaydi. 200 shifokorgacha bu yetarli; undan keyin `pg_trgm`
        # (yoki ES tiklanishi) kerak.
        qs = qs.filter(Q(user__full_name__icontains=text) | Q(specializations__name__icontains=text))

    qs = qs.distinct().order_by("-rating", "-reviews_count")

    if home_visits_only and lat is not None and lng is not None:
        # C4: faqat shu manzilga YETIB BORADIGANLAR. Radius har shifokorda
        # har xil — SQL'da emas, Python'da (shifokorlar soni kichik; Faza 5
        # da ES geo_distance ga ko'chadi). Bazasi kiritilmaganlar ko'rsatilmaydi:
        # ular bu manzilga borishi-bormasligi noma'lum.
        reachable = [
            d for d in qs.exclude(home_base_latitude__isnull=True).exclude(home_base_longitude__isnull=True)
            if haversine_km(d.home_base_latitude, d.home_base_longitude, lat, lng) <= d.home_visit_radius_km
        ]
        return [_to_doctor_dto(d) for d in reachable[offset : offset + limit]]

    if lat is not None and lng is not None:
        return _nearby(qs, lat, lng, radius_km)[offset : offset + limit]

    return [_to_doctor_dto(d) for d in qs[offset : offset + limit]]


def _nearby(qs, lat: float, lng: float, radius_km: float) -> list[DoctorDTO]:
    """C15.5: faol klinikasi radius ichidagi shifokorlar, eng yaqini birinchi.

    Avval bounding box — SQL'da arzon indeksli filtr (Clinic lat/lng indeksi),
    keyin aniq Haversine Python'da. Faza 5 da ES `geo_distance` ga ko'chadi."""
    from dataclasses import replace

    from api.geo import bounding_box

    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    affiliations = DoctorAffiliation.objects.filter(
        is_active=True,
        clinic__status=Clinic.Status.ACTIVE,
        clinic__latitude__range=(min_lat, max_lat),
        clinic__longitude__range=(min_lng, max_lng),
        doctor__in=qs.values("id"),
    ).values_list("doctor_id", "clinic__latitude", "clinic__longitude")

    nearest: dict = {}
    for doctor_id, c_lat, c_lng in affiliations:
        km = haversine_km(lat, lng, c_lat, c_lng)
        if km <= radius_km and km < nearest.get(doctor_id, float("inf")):
            nearest[doctor_id] = km

    doctors = qs.filter(id__in=nearest.keys()).order_by()
    ranked = sorted(doctors, key=lambda d: (nearest[d.id], -d.rating))
    return [replace(_to_doctor_dto(d), distance_km=round(nearest[d.id], 2)) for d in ranked]


def list_clinics(
    *, city: str | None = None, limit: int = 20, offset: int = 0
) -> list[ClinicDTO]:
    qs = Clinic.objects.filter(status=Clinic.Status.ACTIVE)
    if city:
        qs = qs.filter(city=city)
    qs = qs.order_by("-rating", "-reviews_count")[offset : offset + limit]
    return [_to_clinic_dto(c) for c in qs]


def list_doctor_services(doctor_id: UUID, place: str | None = None) -> list[ServiceDTO]:
    """Shifokorning xizmatlari — bron oynasidagi "Shag 1. Vybor uslugi".

    Shaxsiy xizmatlar + u faol ishlaydigan klinikalarning xizmatlari (C15.3).
    `get_bookable_services` bilan AYNAN bir xil qoida — mijoz ko'rgan xizmatni
    bron qila olishi kerak.
    """
    qs = Service.objects.filter(is_active=True).filter(
        Q(doctor_id=doctor_id) | Q(doctor__isnull=True, clinic_id__in=get_doctor_clinic_ids(doctor_id))
    )
    if place:
        qs = qs.filter(place=place)
    return [_to_service_dto(s) for s in qs.order_by("price")]


# ---------------------------------------------------------------------------
# Konvertorlar
# ---------------------------------------------------------------------------


def _to_service_dto(s: Service) -> ServiceDTO:
    return ServiceDTO(
        id=s.id,
        name=s.name,
        place=s.place,
        price=s.price,
        duration_minutes=s.duration_minutes,
        specialization_id=s.specialization_id,
        clinic_id=s.clinic_id,
        doctor_id=s.doctor_id,
        name_ru=s.name_ru,
        mxik_code=s.mxik_code,
        package_code=s.package_code,
        vat_percent=s.vat_percent,
    )


def _to_doctor_dto(d: Doctor) -> DoctorDTO:
    return DoctorDTO(
        id=d.id,
        full_name=d.user.full_name,
        experience_years=d.experience_years,
        rating=d.rating,
        reviews_count=d.reviews_count,
        specializations=tuple(localized(sp, "name") for sp in d.specializations.all()),  # C14
        accepts_home_visits=d.accepts_home_visits,
        is_bookable=(d.status == Doctor.Status.APPROVED and d.user.is_active),
    )


def _to_clinic_dto(c: Clinic) -> ClinicDTO:
    return ClinicDTO(
        id=c.id,
        name=c.name,
        city=c.city,
        street=c.street,
        latitude=c.latitude,
        longitude=c.longitude,
        rating=c.rating,
        reviews_count=c.reviews_count,
    )