"""C15.4 — shifokor qidiruvi: ES dvigateli va Postgres zaxirasi."""

from unittest import mock

import pytest
from rest_framework.test import APIClient

from api.search import service as search_service


@pytest.fixture
def api():
    return APIClient()


def _es_hit(ids, total=None):
    return {"total": total if total is not None else len(ids),
            "results": [{"id": str(i)} for i in ids]}


# ---------------------------------------------------------------------------
# Dvigatel tanlash va zaxira
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_es_ishlaganda_undan_oqiydi(doctor):
    with mock.patch("api.search.queries.search_doctors", return_value=_es_hit([doctor.id])) as es:
        r = search_service.search(text="aliyev")
    assert r.engine == "elasticsearch"
    assert [d.id for d in r.doctors] == [doctor.id]
    assert es.call_args.kwargs["text"] == "aliyev"


@pytest.mark.django_db
def test_es_yiqilsa_postgresga_tushadi_va_buni_aytadi(doctor):
    """Elasticsearch qulaylik, katalog esa mahsulotning o'zi — ES o'lsa
    qidiruv YO'QOLMASLIGI kerak."""
    with mock.patch("api.search.queries.search_doctors", side_effect=ConnectionError("ES yo'q")):
        r = search_service.search(text="Aliyev")
    assert r.engine == "postgres"
    assert [d.id for d in r.doctors] == [doctor.id]
    # Jim fallback eng yomon variant — degradatsiya javobda ko'rinadi
    assert r.total is None


@pytest.mark.django_db
def test_zaxira_yolda_mutaxassislik_nomi_boyicha_ham_topadi(doctor, specialization):
    with mock.patch("api.search.queries.search_doctors", side_effect=RuntimeError):
        r = search_service.search(text=specialization.name[:5])
    assert [d.id for d in r.doctors] == [doctor.id]


@pytest.mark.django_db
def test_zaxira_yolda_mos_kelmaydigan_matn_bosh_natija(doctor):
    with mock.patch("api.search.queries.search_doctors", side_effect=RuntimeError):
        r = search_service.search(text="zzzz-yoq-shifokor")
    assert r.doctors == []


# ---------------------------------------------------------------------------
# ES saralaydi, Postgres ko'rsatadi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_es_tartibi_saqlanadi(doctor, specialization):
    from apps.account.models import User
    from apps.catalog.models import Doctor

    second = Doctor.objects.create(
        user=User.objects.create_user(phone="+998901119999", full_name="Dr. Ikkinchi"),
        status=Doctor.Status.APPROVED, experience_years=3,
    )
    second.specializations.add(specialization)

    with mock.patch("api.search.queries.search_doctors", return_value=_es_hit([second.id, doctor.id])):
        r = search_service.search()
    assert [d.id for d in r.doctors] == [second.id, doctor.id]


@pytest.mark.django_db
def test_esda_bor_lekin_moderatsiyadan_chiqarilgan_shifokor_korsatilmaydi(doctor):
    """A9: projector orqada qolsa, ES'da eski hujjat qoladi. Ma'lumot
    Postgres'dan to'ldirilgani uchun bunday shifokor ro'yxatga tushmaydi."""
    from apps.catalog.models import Doctor

    Doctor.objects.filter(id=doctor.id).update(status=Doctor.Status.PENDING)
    with mock.patch("api.search.queries.search_doctors", return_value=_es_hit([doctor.id])):
        r = search_service.search()
    assert r.doctors == []
    assert r.total == 1  # ES shuncha topdi — raqam o'zgartirilmaydi


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoint_natija_va_dvigatelni_qaytaradi(api, doctor):
    with mock.patch("api.search.queries.search_doctors", return_value=_es_hit([doctor.id], total=7)):
        r = api.get("/api/v1/catalog/search?q=aliyev")
    assert r.status_code == 200
    assert r.data["engine"] == "elasticsearch" and r.data["total"] == 7
    assert [x["id"] for x in r.data["results"]] == [str(doctor.id)]


@pytest.mark.django_db
def test_endpoint_avtorizatsiyasiz_ishlaydi(api, doctor):
    with mock.patch("api.search.queries.search_doctors", return_value=_es_hit([])):
        assert api.get("/api/v1/catalog/search").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("query,xato", [
    ("?lat=41.3", "lat va lng birga beriladi"),
    ("?radius=0", "radius"),
    ("?radius=100", "radius"),
    ("?min_rating=9", "min_rating"),
    ("?min_rating=abc", "min_rating"),
    ("?sort=yoq", "sort noto'g'ri"),
    ("?sort=distance", "sort=distance uchun lat va lng kerak"),
])
def test_endpoint_notogri_parametrlar(api, query, xato):
    r = api.get(f"/api/v1/catalog/search{query}")
    assert r.status_code == 400 and xato in r.data["detail"]


@pytest.mark.django_db
def test_endpoint_es_yiqilganda_ham_200(api, doctor):
    """Qidiruv sahifasi ES o'lganda oq ekran ko'rsatmasligi kerak."""
    with mock.patch("api.search.queries.search_doctors", side_effect=ConnectionError):
        r = api.get("/api/v1/catalog/search?q=Aliyev")
    assert r.status_code == 200 and r.data["engine"] == "postgres"
    assert [x["id"] for x in r.data["results"]] == [str(doctor.id)]
