import subprocess
import sys
from pathlib import Path

import pytest
from rest_framework.test import APIClient

BASE_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.django_db
def test_doctor_detail_og_ir_maydonlar_va_klinikalar_bilan(doctor, clinic):
    from apps.catalog.models import DoctorAffiliation

    doctor.bio = "20 yillik tajriba"
    doctor.license_number = "LIC-123"
    doctor.save()
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)

    resp = APIClient().get(f"/api/v1/catalog/doctors/{doctor.id}")

    assert resp.status_code == 200
    assert resp.data["bio"] == "20 yillik tajriba"
    assert resp.data["license_number"] == "LIC-123"
    assert [c["name"] for c in resp.data["clinics"]] == ["Shifo klinikasi"]


@pytest.mark.django_db
def test_doctor_list_da_og_ir_maydonlar_yoq(doctor):
    resp = APIClient().get("/api/v1/catalog/doctors")

    assert resp.status_code == 200
    assert "bio" not in resp.data[0]


def _import_settings(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=BASE_DIR,
        env={"DJANGO_SETTINGS_MODULE": "conf.settings", "SYSTEMROOT": "C:\\Windows", **env},
        capture_output=True,
        text=True,
    )


def test_prod_rejimida_majburiy_sozlamalarsiz_ishga_tushmaydi():
    result = _import_settings({"DJANGO_DEBUG": "0"})

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr
    assert "POSTGRES_DB" in result.stderr
