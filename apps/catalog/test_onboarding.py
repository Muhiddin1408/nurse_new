"""Sprint 2.1 — rollar (D1), onboarding va moderatsiya (D2), A9, E7 (katalog hodisalari)."""

from datetime import date, timedelta
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from api.account.services import issue_tokens
from apps.catalog.models import ClinicMembership, Doctor, DoctorDocument
from apps.utils.models import OutboxEvent

PDF = b"%PDF-1.4\n%fake pdf body\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


@pytest.fixture(autouse=True)
def private_media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private"
    return settings.PRIVATE_MEDIA_ROOT


@pytest.fixture
def user(db):
    from apps.account.models import User

    return User.objects.create_user(phone="+998907770001", full_name="Dr. Karimova")


@pytest.fixture
def admin(db):
    from apps.account.models import User

    return User.objects.create_user(phone="+998907770099", role=User.Role.PLATFORM_ADMIN)


def api_for(u):
    api = APIClient()
    api.force_authenticate(u)
    return api


def complete_onboarding(user, specialization, *, expires=None):
    api = api_for(user)
    api.post("/api/v1/doctor/onboarding/start")
    resp = api.patch("/api/v1/doctor/onboarding/profile", {
        "full_name": "Dr. Karimova", "specialization_ids": [str(specialization.id)],
        "experience_years": 12, "license_number": "LIC-777",
        "license_expires_at": (expires or date.today() + timedelta(days=365)).isoformat(),
        "languages": ["uz", "ru"], "accepts_home_visits": True,
    }, format="json")
    assert resp.status_code == 200, resp.content
    for kind in ("diploma", "license"):
        r = api.post("/api/v1/doctor/onboarding/documents",
                     {"kind": kind, "file": SimpleUploadedFile(f"{kind}.pdf", PDF, "application/pdf")},
                     format="multipart")
        assert r.status_code == 201, r.content
    return api


# --- D1 rollar --------------------------------------------------------------


@pytest.mark.django_db
def test_rollar_malumotdan_hisoblanadi(user, clinic):
    from api.roles import available_roles

    assert available_roles(user) == ["client"]
    Doctor.objects.create(user=user, status=Doctor.Status.DRAFT)
    ClinicMembership.objects.create(user=user, clinic=clinic)
    assert available_roles(user) == ["client", "doctor", "clinic_admin"]


@pytest.mark.django_db
def test_token_va_otp_javobida_rollar(user):
    from rest_framework_simplejwt.tokens import AccessToken

    Doctor.objects.create(user=user, status=Doctor.Status.APPROVED)
    tokens = issue_tokens(user)
    access = AccessToken(tokens["access"])
    assert access["available_roles"] == ["client", "doctor"]
    assert access["active_role"] == "client"


@pytest.mark.django_db
def test_switch_role_berilmagan_rolga_403_va_sessiya_saqlanadi(user):
    tokens = issue_tokens(user)
    api = api_for(user)
    resp = api.post("/api/v1/accounts/auth/switch-role", {"refresh": tokens["refresh"], "role": "doctor"}, format="json")
    assert resp.status_code == 403
    # eski refresh hali ishlaydi
    assert APIClient().post("/api/v1/accounts/auth/token/refresh", {"refresh": tokens["refresh"]},
                            format="json").status_code == 200


@pytest.mark.django_db
def test_switch_role_shifokorga(user):
    from rest_framework_simplejwt.tokens import AccessToken

    Doctor.objects.create(user=user, status=Doctor.Status.APPROVED)
    tokens = issue_tokens(user)
    resp = api_for(user).post("/api/v1/accounts/auth/switch-role",
                              {"refresh": tokens["refresh"], "role": "doctor"}, format="json")
    assert resp.status_code == 200
    assert AccessToken(resp.json()["access"])["active_role"] == "doctor"


# --- D2 onboarding ----------------------------------------------------------


@pytest.mark.django_db
def test_toliq_onboarding_va_tasdiqlash(user, admin, specialization):
    api = complete_onboarding(user, specialization)

    status_ = api.get("/api/v1/doctor/onboarding/status").json()
    assert status_["status"] == "draft" and status_["missing"] == []

    resp = api.post("/api/v1/doctor/onboarding/submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["sla_due_at"] is not None

    # moderatsiyadagi profilni tahrirlab bo'lmaydi
    assert api.patch("/api/v1/doctor/onboarding/profile", {"bio": "x"}, format="json").status_code == 400

    moderator = api_for(admin)
    queue = moderator.get("/api/v1/moderation/doctors").json()
    assert len(queue) == 1 and len(queue[0]["documents"]) == 2

    doctor_id = queue[0]["id"]
    resp = moderator.post(f"/api/v1/moderation/doctors/{doctor_id}/approve")
    assert resp.status_code == 200 and resp.json()["status"] == "approved"

    ev = OutboxEvent.objects.get(topic="catalog.events")
    assert ev.event_type == "DoctorApproved" and ev.key == doctor_id
    assert ev.payload["is_bookable"] is True

    # endi katalogda ko'rinadi (A9)
    assert APIClient().get(f"/api/v1/catalog/doctors/{doctor_id}").status_code == 200


@pytest.mark.django_db
def test_yetishmayotgan_maydonlar_bilan_submit_400(user):
    api = api_for(user)
    api.post("/api/v1/doctor/onboarding/start")
    resp = api.post("/api/v1/doctor/onboarding/submit")
    assert resp.status_code == 400
    missing = api.get("/api/v1/doctor/onboarding/status").json()["missing"]
    assert {"specialization_ids", "license_number", "document:diploma", "document:license"} <= set(missing)


@pytest.mark.django_db
def test_muddati_otgan_litsenziya_bilan_submit_qilinmaydi(user, specialization):
    api = complete_onboarding(user, specialization, expires=date.today() - timedelta(days=1))
    assert api.post("/api/v1/doctor/onboarding/submit").status_code == 400
    assert "license_expires_at:expired" in api.get("/api/v1/doctor/onboarding/status").json()["missing"]


@pytest.mark.django_db
def test_rad_etish_sabab_bilan_va_qayta_yuborish(user, admin, specialization):
    api = complete_onboarding(user, specialization)
    api.post("/api/v1/doctor/onboarding/submit")
    doctor = Doctor.objects.get(user=user)
    moderator = api_for(admin)

    assert moderator.post(f"/api/v1/moderation/doctors/{doctor.id}/reject", {}, format="json").status_code == 400
    resp = moderator.post(f"/api/v1/moderation/doctors/{doctor.id}/reject", {"reason": "Diplom o'qilmaydi"}, format="json")
    assert resp.json()["status"] == "rejected"

    st = api.get("/api/v1/doctor/onboarding/status").json()
    assert (st["status"], st["reason"]) == ("rejected", "Diplom o'qilmaydi")
    # tuzatib qayta yuboradi
    assert api.patch("/api/v1/doctor/onboarding/profile", {"bio": "Yangilandi"}, format="json").status_code == 200
    assert api.post("/api/v1/doctor/onboarding/submit").json()["status"] == "pending"


@pytest.mark.django_db
def test_notogri_holat_otishi_409(user, admin, specialization):
    complete_onboarding(user, specialization)
    doctor = Doctor.objects.get(user=user)  # draft
    resp = api_for(admin).post(f"/api/v1/moderation/doctors/{doctor.id}/approve")
    assert resp.status_code == 409


@pytest.mark.django_db
def test_tasdiqlanmagan_shifokor_katalogda_404(user):
    doctor = Doctor.objects.create(user=user, status=Doctor.Status.PENDING)
    assert APIClient().get(f"/api/v1/catalog/doctors/{doctor.id}").status_code == 404


@pytest.mark.django_db
def test_suspend_va_reinstate_hodisalari(doctor, admin):
    moderator = api_for(admin)
    assert moderator.post(f"/api/v1/moderation/doctors/{doctor.id}/suspend", {"reason": "Shikoyat"},
                          format="json").json()["status"] == "suspended"
    assert APIClient().get(f"/api/v1/catalog/doctors/{doctor.id}").status_code == 404
    assert moderator.post(f"/api/v1/moderation/doctors/{doctor.id}/reinstate").json()["status"] == "approved"
    assert list(OutboxEvent.objects.filter(topic="catalog.events").order_by("created_at")
                .values_list("event_type", flat=True)) == ["DoctorSuspended", "DoctorApproved"]


@pytest.mark.django_db
def test_mijoz_moderatsiyaga_kira_olmaydi(user):
    assert api_for(user).get("/api/v1/moderation/doctors").status_code == 403


# --- hujjatlar --------------------------------------------------------------


@pytest.mark.django_db
def test_soxta_kengaytma_rad_etiladi_va_hajm_cheklovi(user, settings):
    api = api_for(user)
    api.post("/api/v1/doctor/onboarding/start")
    fake = SimpleUploadedFile("virus.pdf", b"MZ\x90\x00 exe", "application/pdf")
    assert api.post("/api/v1/doctor/onboarding/documents", {"kind": "license", "file": fake},
                    format="multipart").status_code == 400

    settings.DOCUMENT_MAX_BYTES = 10
    big = SimpleUploadedFile("big.png", PNG, "image/png")
    assert api.post("/api/v1/doctor/onboarding/documents", {"kind": "license", "file": big},
                    format="multipart").status_code == 400


@pytest.mark.django_db
def test_hujjat_faqat_imzolangan_havola_bilan_va_muddatli(user, private_media, settings):
    api = api_for(user)
    api.post("/api/v1/doctor/onboarding/start")
    doc = api.post("/api/v1/doctor/onboarding/documents",
                   {"kind": "diploma", "file": SimpleUploadedFile("d.png", PNG, "image/png")},
                   format="multipart").json()

    stored = DoctorDocument.objects.get(id=doc["id"])
    assert stored.content_type == "image/png"
    assert "d.png" not in stored.file.name  # asl nom yo'lga tushmaydi
    assert str(private_media) in stored.file.path

    anon = APIClient()
    resp = anon.get(doc["download_url"])
    assert resp.status_code == 200
    assert b"".join(resp.streaming_content) == PNG
    assert resp["Cache-Control"] == "private, no-store"

    assert anon.get("/api/v1/doctor/documents/download/soxta-token").status_code == 404
    with mock.patch("django.core.signing.time.time", return_value=__import__("time").time() + 3600):
        assert anon.get(doc["download_url"]).status_code == 404


@pytest.mark.django_db
def test_begona_hujjatni_ochirib_bolmaydi(user, specialization):
    from apps.account.models import User

    api = complete_onboarding(user, specialization)
    doc_id = DoctorDocument.objects.first().id
    other = User.objects.create_user(phone="+998907770555")
    other_api = api_for(other)
    other_api.post("/api/v1/doctor/onboarding/start")
    assert other_api.delete(f"/api/v1/doctor/onboarding/documents/{doc_id}").status_code == 400
    assert DoctorDocument.objects.filter(id=doc_id).exists()


# --- litsenziya jobi --------------------------------------------------------


@pytest.mark.django_db
def test_litsenziya_eslatmasi_bir_marta_va_muddati_otganda_suspend(doctor):
    from django.core.management import call_command

    Doctor.objects.filter(id=doctor.id).update(license_expires_at=timezone.localdate() + timedelta(days=10))
    call_command("check_doctor_licenses")
    call_command("check_doctor_licenses")
    assert OutboxEvent.objects.filter(event_type="DoctorLicenseExpiring").count() == 1

    Doctor.objects.filter(id=doctor.id).update(license_expires_at=timezone.localdate())
    call_command("check_doctor_licenses")
    doctor.refresh_from_db()
    assert (doctor.status, doctor.status_reason) == ("suspended", "license_expired")
    assert OutboxEvent.objects.filter(event_type="DoctorSuspended").exists()
