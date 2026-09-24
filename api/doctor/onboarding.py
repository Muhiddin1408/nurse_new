"""
doctor/onboarding.py — shifokorning o'z-o'zidan ro'yxatdan o'tishi va moderatsiya (D2).

    start -> profile -> documents -> submit -> [moderatsiya] -> approved | rejected
                                                              rejected -> tuzatib -> submit
    approved -> suspended (shikoyat / litsenziya muddati) -> approved

Tahrirlash faqat `draft` va `rejected` holatlarda: moderatsiyadagi yoki
tasdiqlangan profilni jimgina o'zgartirib, tekshiruvdan o'tmagan ma'lumotni
mijozlarga ko'rsatib bo'lmaydi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from api import audit
from django.utils import timezone

from api.catalog.events import publish_doctor_event
from api.schedule.services import today_local
from apps.catalog.models import Doctor, DoctorDocument, Specialization

logger = logging.getLogger(__name__)

MODERATION_SLA = timedelta(hours=48)
LICENSE_REMINDER_DAYS = 30
REQUIRED_DOCUMENTS = (DoctorDocument.Kind.DIPLOMA, DoctorDocument.Kind.LICENSE)
EDITABLE_STATUSES = (Doctor.Status.DRAFT, Doctor.Status.REJECTED)

ALLOWED_CONTENT = {
    # MIME -> fayl boshidagi "sehrli" baytlar. Kengaytmaga yoki klient
    # yuborgan Content-Type'ga ishonmaymiz — ular soxtalashtiriladi.
    "application/pdf": (b"%PDF",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


class OnboardingError(Exception):
    """400 — so'rov noto'g'ri yoki holat mos emas."""


@dataclass(frozen=True)
class OnboardingStatus:
    status: str
    reason: str
    missing: list[str]
    submitted_at: object
    sla_due_at: object


# ---------------------------------------------------------------------------
# Shifokor tomoni
# ---------------------------------------------------------------------------


def start(user) -> Doctor:
    """Idempotent: profil bor bo'lsa o'sha qaytadi."""
    existing = Doctor.objects.filter(user=user).first()
    if existing:
        return existing
    try:
        with transaction.atomic():
            return Doctor.objects.create(user=user, status=Doctor.Status.DRAFT)
    except IntegrityError:
        return Doctor.objects.get(user=user)


def _ensure_editable(doctor: Doctor) -> None:
    if doctor.status not in EDITABLE_STATUSES:
        raise OnboardingError("Profilni faqat qoralama yoki rad etilgan holatda tahrirlash mumkin")


PROFILE_FIELDS = (
    "bio", "experience_years", "education", "languages", "license_number",
    "license_expires_at", "accepts_home_visits", "home_visit_radius_km",
)


def update_profile(doctor: Doctor, data: dict) -> Doctor:
    _ensure_editable(doctor)
    with transaction.atomic():
        if "full_name" in data:
            doctor.user.full_name = data["full_name"]
            doctor.user.save(update_fields=["full_name", "updated_at"])
        for field in PROFILE_FIELDS:
            if field in data:
                setattr(doctor, field, data[field])
        doctor.save()
        if "specialization_ids" in data:
            specs = list(Specialization.objects.filter(id__in=data["specialization_ids"]))
            if len(specs) != len(set(data["specialization_ids"])):
                raise OnboardingError("Mutaxassislik topilmadi")
            doctor.specializations.set(specs)
    return doctor


def _sniff_content_type(head: bytes) -> str | None:
    for mime, signatures in ALLOWED_CONTENT.items():
        if any(head.startswith(sig) for sig in signatures):
            return mime
    return None


def add_document(doctor: Doctor, kind: str, uploaded_file) -> DoctorDocument:
    _ensure_editable(doctor)
    if kind not in DoctorDocument.Kind.values:
        raise OnboardingError("Hujjat turi noto'g'ri")
    if uploaded_file.size > settings.DOCUMENT_MAX_BYTES:
        raise OnboardingError(f"Fayl {settings.DOCUMENT_MAX_BYTES // (1024 * 1024)} MB dan katta")
    if uploaded_file.size == 0:
        raise OnboardingError("Fayl bo'sh")

    head = uploaded_file.read(16)
    uploaded_file.seek(0)
    content_type = _sniff_content_type(head)
    if content_type is None:
        raise OnboardingError("Faqat PDF, JPEG yoki PNG fayl qabul qilinadi")

    return DoctorDocument.objects.create(
        doctor=doctor,
        kind=kind,
        file=uploaded_file,
        original_name=uploaded_file.name[:255],
        content_type=content_type,
        size=uploaded_file.size,
    )


def delete_document(doctor: Doctor, document_id: UUID) -> None:
    _ensure_editable(doctor)
    doc = DoctorDocument.objects.filter(id=document_id, doctor=doctor).first()
    if doc is None:
        raise OnboardingError("Hujjat topilmadi")
    doc.file.delete(save=False)
    doc.delete()


def missing_requirements(doctor: Doctor) -> list[str]:
    missing = []
    if not doctor.user.full_name.strip():
        missing.append("full_name")
    if not doctor.specializations.exists():
        missing.append("specialization_ids")
    if not doctor.license_number:
        missing.append("license_number")
    if not doctor.license_expires_at:
        missing.append("license_expires_at")
    elif doctor.license_expires_at <= today_local():
        missing.append("license_expires_at:expired")
    kinds = set(doctor.documents.values_list("kind", flat=True))
    missing += [f"document:{k}" for k in REQUIRED_DOCUMENTS if k not in kinds]
    return missing


def submit(doctor: Doctor) -> Doctor:
    _ensure_editable(doctor)
    missing = missing_requirements(doctor)
    if missing:
        raise OnboardingError("To'ldirilmagan: " + ", ".join(missing))
    Doctor.objects.filter(id=doctor.id).update(
        status=Doctor.Status.PENDING, submitted_at=timezone.now(), status_reason="", updated_at=timezone.now()
    )
    doctor.refresh_from_db()
    logger.info("Shifokor moderatsiyaga yubordi: %s", doctor.id)
    return doctor


def get_status(doctor: Doctor) -> OnboardingStatus:
    return OnboardingStatus(
        status=doctor.status,
        reason=doctor.status_reason,
        missing=missing_requirements(doctor) if doctor.status in EDITABLE_STATUSES else [],
        submitted_at=doctor.submitted_at,
        sla_due_at=doctor.submitted_at + MODERATION_SLA
        if doctor.submitted_at and doctor.status == Doctor.Status.PENDING else None,
    )


# ---------------------------------------------------------------------------
# Imzolangan hujjat havolasi
# ---------------------------------------------------------------------------

_SIGNING_SALT = "doctor-document"


def sign_document(document_id: UUID) -> str:
    return signing.dumps(str(document_id), salt=_SIGNING_SALT)


def unsign_document(token: str) -> UUID:
    """Muddati (DOCUMENT_URL_TTL_SECONDS) o'tgan yoki soxta token -> OnboardingError."""
    try:
        return UUID(signing.loads(token, salt=_SIGNING_SALT, max_age=settings.DOCUMENT_URL_TTL_SECONDS))
    except (signing.BadSignature, ValueError) as exc:
        raise OnboardingError("Havola yaroqsiz yoki muddati o'tgan") from exc


# ---------------------------------------------------------------------------
# Platforma admini — moderatsiya
# ---------------------------------------------------------------------------


def _transition(doctor_id: UUID, *, from_statuses, to_status, admin, reason: str, event: str | None):
    with transaction.atomic():
        doctor = Doctor.objects.select_for_update().get(id=doctor_id)
        if doctor.status == to_status:
            return doctor  # idempotent
        if doctor.status not in from_statuses:
            raise OnboardingError(f"Holat {doctor.status} dan {to_status} ga o'tib bo'lmaydi")
        Doctor.objects.filter(id=doctor_id).update(
            status=to_status, status_reason=reason, moderated_by=admin,
            moderated_at=timezone.now(), updated_at=timezone.now(),
        )
        if event:
            publish_doctor_event(doctor_id, event, {"reason": reason})
        audit.record(f"doctor.status.{to_status}", obj=doctor, actor=admin,
                     before={"status": doctor.status}, after={"status": to_status, "reason": reason})
    doctor.refresh_from_db()
    logger.info("Shifokor %s: %s -> %s (%s)", doctor_id, from_statuses, to_status, reason)
    return doctor


def approve(doctor_id: UUID, admin) -> Doctor:
    doctor = Doctor.objects.get(id=doctor_id)
    if doctor.status == Doctor.Status.PENDING and doctor.license_expires_at and doctor.license_expires_at <= today_local():
        raise OnboardingError("Litsenziya muddati o'tgan — tasdiqlab bo'lmaydi")
    return _transition(doctor_id, from_statuses=(Doctor.Status.PENDING,), to_status=Doctor.Status.APPROVED,
                       admin=admin, reason="", event="DoctorApproved")


def reject(doctor_id: UUID, admin, reason: str) -> Doctor:
    if not reason.strip():
        raise OnboardingError("Rad etish sababi majburiy")
    return _transition(doctor_id, from_statuses=(Doctor.Status.PENDING,), to_status=Doctor.Status.REJECTED,
                       admin=admin, reason=reason, event=None)


def suspend(doctor_id: UUID, admin, reason: str) -> Doctor:
    if not reason.strip():
        raise OnboardingError("To'xtatish sababi majburiy")
    return _transition(doctor_id, from_statuses=(Doctor.Status.APPROVED,), to_status=Doctor.Status.SUSPENDED,
                       admin=admin, reason=reason, event="DoctorSuspended")


def reinstate(doctor_id: UUID, admin) -> Doctor:
    doctor = Doctor.objects.get(id=doctor_id)
    if doctor.license_expires_at and doctor.license_expires_at <= today_local():
        raise OnboardingError("Litsenziya muddati o'tgan — avval yangilang")
    return _transition(doctor_id, from_statuses=(Doctor.Status.SUSPENDED,), to_status=Doctor.Status.APPROVED,
                       admin=admin, reason="", event="DoctorApproved")


# ---------------------------------------------------------------------------
# Litsenziya muddati nazorati (kunlik job)
# ---------------------------------------------------------------------------


def check_licenses(today: date | None = None) -> tuple[int, int]:
    """(eslatma yuborilgan, to'xtatilgan)."""
    today = today or today_local()
    reminded = suspended = 0

    for doctor in Doctor.objects.filter(
        status=Doctor.Status.APPROVED,
        license_expires_at__isnull=False,
        license_expires_at__lte=today,
    ):
        _transition(doctor.id, from_statuses=(Doctor.Status.APPROVED,), to_status=Doctor.Status.SUSPENDED,
                    admin=None, reason="license_expired", event="DoctorSuspended")
        suspended += 1

    for doctor in Doctor.objects.filter(
        status=Doctor.Status.APPROVED,
        license_expires_at__gt=today,
        license_expires_at__lte=today + timedelta(days=LICENSE_REMINDER_DAYS),
        license_reminder_sent_at__isnull=True,
    ).select_related("user"):
        with transaction.atomic():
            updated = Doctor.objects.filter(id=doctor.id, license_reminder_sent_at__isnull=True).update(
                license_reminder_sent_at=timezone.now()
            )
            if updated:
                from api.events import services as events

                events.publish(
                    topic="doctor.notifications", key=str(doctor.id), event_type="DoctorLicenseExpiring",
                    payload={"doctor_id": str(doctor.id), "phone": doctor.user.phone,
                             "expires_at": doctor.license_expires_at.isoformat()},
                )
                reminded += 1
    return reminded, suspended
