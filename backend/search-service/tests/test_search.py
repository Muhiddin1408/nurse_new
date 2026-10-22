"""Proyeksiya → Elasticsearch → ommaviy qidiruv: haqiqiy Postgres, Redis va Elasticsearch 8 (testcontainers).

Eventlar katalog sxemasi bo'yicha (`sample_data`) servis handlerlari orqali qo'llanadi; qidiruv — HTTP API orqali.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import sample_data

from tests.conftest import es_run

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
TASHKENT_CENTER = (41.311081, 69.240562)
CHILANZAR = (41.2750, 69.2040)  # markazdan ~5 km
YUNUSOBOD = (41.3650, 69.2850)  # markazdan ~7 km, Chilonzordan ~13 km
SAMARKAND = (39.6542, 66.9597)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


class World:
    def __init__(self, api: TestClient, run, db) -> None:  # type: ignore[no-untyped-def]
        self.api, self.run, self.db = api, run, db
        self.spec = uuid.uuid4()
        self.other_spec = uuid.uuid4()
        db.scalar(
            "INSERT INTO specializations (id, name, name_ru, slug) VALUES "
            "(:a, 'Kardiolog', 'Кардиолог', 'kardiolog'), (:b, 'Stomatolog', 'Стоматолог', 'stomatolog')",
            a=self.spec,
            b=self.other_spec,
        )

    def doctor(
        self, name: str = "Aliyev Botir", *, spec: uuid.UUID | None = None, home: bool = False, t: int = 0
    ) -> str:
        doctor_id = str(uuid.uuid4())
        self.run("doctor.approved", sample_data(
            "doctor.approved", doctor_id=doctor_id, full_name=name, specialization_ids=[str(spec or self.spec)],
            accepts_home_visits=home,
        ), occurred_at=at(t))  # fmt: skip
        return doctor_id

    def clinic(
        self, name: str = "Shifo Plus", point: tuple[float, float] = TASHKENT_CENTER, city: str = "Toshkent"
    ) -> str:
        clinic_id = str(uuid.uuid4())
        self.run("clinic.published", sample_data(
            "clinic.published", clinic_id=clinic_id, name=name, city=city, street="Amir Temur 1",
            latitude=point[0], longitude=point[1],
        ), occurred_at=at(1))  # fmt: skip
        return clinic_id

    def affiliate(self, doctor_id: str, clinic_id: str, *, event: str = "affiliation.accepted", t: int = 2) -> str:
        aff = str(uuid.uuid4())
        self.affiliation(aff, doctor_id, clinic_id, event=event, t=t)
        return aff

    def affiliation(self, aff: str, doctor_id: str, clinic_id: str, *, event: str, t: int) -> None:
        extra = {"ended_by": "clinic", "status": "ended"} if event == "affiliation.ended" else {}
        self.run(event, sample_data(event, affiliation_id=aff, doctor_id=doctor_id, clinic_id=clinic_id, **extra),
                 occurred_at=at(t))  # fmt: skip

    def service(
        self, *, doctor_id: str | None, clinic_id: str | None = None, place: str = "clinic", price: int = 15_000_000,
        name: str = "Konsultatsiya", t: int = 3, spec: uuid.UUID | None = None,
    ) -> str:  # fmt: skip
        service_id = str(uuid.uuid4())
        self.run("service.created", sample_data(
            "service.created", service_id=service_id, doctor_id=doctor_id, clinic_id=clinic_id,
            specialization_id=str(spec or self.spec), name=name, name_ru="", place=place, duration_minutes=30,
            price_tiyin=price, is_active=True,
        ), occurred_at=at(t))  # fmt: skip
        return service_id

    def bookable_clinic_doctor(self, name: str = "Aliyev Botir", **kw: Any) -> tuple[str, str]:
        doctor_id = self.doctor(name, **kw)
        clinic_id = self.clinic()
        self.affiliate(doctor_id, clinic_id)
        self.service(doctor_id=doctor_id)
        return doctor_id, clinic_id

    def search(self, path: str = "/v1/search/doctors", **params: Any) -> dict[str, Any]:
        r = self.api.get(path, params=params)
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        assert body["engine"] == "elasticsearch", body
        return body

    def ids(self, **params: Any) -> list[str]:
        return [d["id"] for d in self.search(**params)["items"]]


@pytest.fixture
def world(api: TestClient, run_handler, db) -> World:
    return World(api, run_handler, db)


def refresh(prefix: str) -> None:
    async def go(es: Any) -> None:
        await es.indices.refresh(index=f"{prefix}*")

    es_run(go)


def test_doctor_becomes_searchable_with_clinic_and_home_offerings(world: World, es_env: str) -> None:
    doctor_id = world.doctor("Aliyev Botir", home=True)
    refresh(es_env)
    assert world.ids() == []  # taklifi yo'q — bron qilib bo'lmaydi
    clinic_id = world.clinic()
    world.affiliate(doctor_id, clinic_id)
    world.service(doctor_id=doctor_id, price=15_000_000)
    world.service(doctor_id=doctor_id, place="home", price=30_000_000, name="Uyga chaqiruv")
    world.db.scalar("UPDATE doctors SET home_lat = :lat, home_lng = :lng", lat=YUNUSOBOD[0], lng=YUNUSOBOD[1])
    world.run("doctor.profile_updated", sample_data(
        "doctor.profile_updated", doctor_id=doctor_id, full_name="Aliyev Botir", specialization_ids=[str(world.spec)],
        experience_years=12, languages=["uz", "ru"], accepts_home_visits=True, home_visit_radius_km=10,
    ), occurred_at=at(5))  # fmt: skip
    refresh(es_env)

    card = world.search()["items"][0]
    assert card["id"] == doctor_id and card["places"] == ["clinic", "home"]
    assert card["min_price_tiyin"] == 15_000_000 and card["experience_years"] == 12
    assert card["clinics"] == [{"id": clinic_id, "name": "Shifo Plus", "city": "Toshkent"}]
    assert "Kardiolog" in card["specialization_names"] and "Кардиолог" in card["specialization_names"]

    assert world.ids(q="aliev") == [doctor_id]  # imlo xatosi kechiriladi (fuzziness)
    assert world.ids(q="kardiolog") == [doctor_id]  # mutaxassislik nomi
    assert world.ids(q="Иванов") == []
    assert world.ids(specialization=str(world.spec)) == [doctor_id]
    assert world.ids(specialization=str(world.other_spec)) == []
    assert world.ids(city="toshkent") == [doctor_id] and world.ids(city="Samarqand") == []
    # Geo: klinika 15 km ichida; uy chaqiruvi — shifokorning o'z radiusi (10 km) ichida
    near = world.search(lat=CHILANZAR[0], lng=CHILANZAR[1], place="clinic")["items"][0]
    assert near["distance_km"] is not None and 4 < near["distance_km"] < 7
    assert world.ids(lat=SAMARKAND[0], lng=SAMARKAND[1]) == []
    assert world.ids(lat=TASHKENT_CENTER[0], lng=TASHKENT_CENTER[1], place="home") == [doctor_id]  # ~7 km ≤ 10
    world.db.scalar("UPDATE doctors SET home_visit_radius_km = 2")
    world.run("schedule.availability_changed", sample_data(
        "schedule.availability_changed", doctor_id=doctor_id, dates=["2026-10-22"],
        next_available_at="2026-10-22T05:00:00Z",
    ), occurred_at=at(6))  # fmt: skip
    refresh(es_env)
    assert world.ids(lat=TASHKENT_CENTER[0], lng=TASHKENT_CENTER[1], place="home") == []  # ~7 km > 2
    assert world.ids(available_on="2026-10-22") == [doctor_id]
    assert world.ids(available_on="2026-10-21") == []
    assert world.search()["items"][0]["next_available_at"].startswith("2026-10-22T05:00")


def test_sorting_paging_and_validation(world: World, es_env: str) -> None:
    cheap, _ = world.bookable_clinic_doctor("Karimov Anvar")
    pricey = world.doctor("Rahimov Jasur")
    clinic = world.clinic("Medline", point=CHILANZAR)
    world.affiliate(pricey, clinic)
    world.service(doctor_id=pricey, price=50_000_000)
    world.run("rating.recalculated", sample_data(
        "rating.recalculated", doctor_id=pricey, rating=4.9, reviews_count=12, clinic_id=clinic, clinic_rating=4.5,
        clinic_reviews_count=30,
    ), occurred_at=at(10))  # fmt: skip
    refresh(es_env)
    assert world.ids(sort="price") == [cheap, pricey]
    assert world.ids(sort="rating") == [pricey, cheap]
    assert world.ids(sort="distance", lat=CHILANZAR[0], lng=CHILANZAR[1]) == [pricey, cheap]
    page = world.search(limit=1, offset=1)
    assert page["total"] == 2 and len(page["items"]) == 1
    assert world.api.get("/v1/search/doctors", params={"lat": 41.0}).status_code == 400
    assert world.api.get("/v1/search/doctors", params={"limit": 51}).status_code == 422
    assert world.api.get("/v1/search/doctors", params={"place": "moon"}).status_code == 422

    clinics = world.search("/v1/search/clinics", sort="rating")
    assert next(c["name"] for c in clinics["items"]) == "Medline"
    medline = clinics["items"][0]
    assert medline["rating"] == 4.5 and medline["doctors_count"] == 1 and medline["min_price_tiyin"] == 50_000_000
    assert [c["name"] for c in world.search("/v1/search/clinics", q="medlin")["items"]] == ["Medline"]
    near = world.search("/v1/search/clinics", lat=CHILANZAR[0], lng=CHILANZAR[1], radius_km=1)
    assert [c["name"] for c in near["items"]] == ["Medline"] and near["items"][0]["distance_km"] < 1


def test_suspend_reinstate_and_out_of_order_events(world: World, es_env: str) -> None:
    doctor_id, clinic_id = world.bookable_clinic_doctor()
    world.run("doctor.suspended", sample_data("doctor.suspended", doctor_id=doctor_id), occurred_at=at(20))
    refresh(es_env)
    assert world.ids() == []
    # Kechikib kelgan eski reinstated (t=15) yangi suspended'ni (t=20) bekor qilmaydi
    world.run("doctor.reinstated", sample_data("doctor.reinstated", doctor_id=doctor_id), occurred_at=at(15))
    refresh(es_env)
    assert world.ids() == []
    assert world.db.scalar("SELECT status FROM doctors WHERE id = :id", id=uuid.UUID(doctor_id)) == "suspended"
    world.run("doctor.reinstated", sample_data("doctor.reinstated", doctor_id=doctor_id), occurred_at=at(25))
    refresh(es_env)
    assert world.ids() == [doctor_id]

    # Eski profil eventi ismni qaytarmaydi
    world.run("doctor.profile_updated", sample_data(
        "doctor.profile_updated", doctor_id=doctor_id, full_name="Yangi Ism", specialization_ids=[str(world.spec)],
        accepts_home_visits=False,
    ), occurred_at=at(30))  # fmt: skip
    world.run("doctor.profile_updated", sample_data(
        "doctor.profile_updated", doctor_id=doctor_id, full_name="Eski Ism", specialization_ids=[str(world.spec)],
        accepts_home_visits=False,
    ), occurred_at=at(29))  # fmt: skip
    refresh(es_env)
    assert world.search()["items"][0]["full_name"] == "Yangi Ism"

    # Klinika to'xtatildi — klinikadagi taklif yo'qoladi (uy chaqiruvi yo'q) → bron qilib bo'lmaydi
    world.run("clinic.suspended", sample_data("clinic.suspended", clinic_id=clinic_id), occurred_at=at(40))
    refresh(es_env)
    assert world.ids() == [] and world.search("/v1/search/clinics")["items"] == []
    world.run("clinic.updated", sample_data(
        "clinic.updated", clinic_id=clinic_id, name="Shifo Plus 2", city="Toshkent", street="x",
        latitude=TASHKENT_CENTER[0], longitude=TASHKENT_CENTER[1], status="active",
    ), occurred_at=at(45))  # fmt: skip
    refresh(es_env)
    assert world.search()["items"][0]["clinics"][0]["name"] == "Shifo Plus 2"


def test_affiliation_and_service_changes_rebuild_documents(world: World, es_env: str) -> None:
    doctor_id = world.doctor()
    clinic_id = world.clinic()
    aff = world.affiliate(doctor_id, clinic_id)
    # Klinikaning o'z xizmati (shifokorsiz) — klinikadagi shifokorlarga taklif bo'ladi
    clinic_service = world.service(doctor_id=None, clinic_id=clinic_id, price=8_000_000, name="EKG")
    refresh(es_env)
    assert world.search()["items"][0]["min_price_tiyin"] == 8_000_000
    world.run("service.updated", sample_data(
        "service.updated", service_id=clinic_service, doctor_id=None, clinic_id=clinic_id,
        specialization_id=str(world.spec), name="EKG", name_ru="", place="clinic", duration_minutes=20,
        price_tiyin=9_000_000, previous_price_tiyin=8_000_000, is_active=True,
    ), occurred_at=at(10))  # fmt: skip
    refresh(es_env)
    assert world.search()["items"][0]["min_price_tiyin"] == 9_000_000
    world.affiliation(aff, doctor_id, clinic_id, event="affiliation.paused", t=11)
    refresh(es_env)
    assert world.ids() == []  # pauzadagi klinika — taklif yo'q
    world.affiliation(aff, doctor_id, clinic_id, event="affiliation.resumed", t=12)
    world.run("service.deactivated", sample_data(
        "service.deactivated", service_id=clinic_service, doctor_id=None, clinic_id=clinic_id,
    ), occurred_at=at(13))  # fmt: skip
    refresh(es_env)
    assert world.ids() == []  # yagona xizmat o'chdi
    world.service(doctor_id=doctor_id, price=12_000_000, t=14)
    refresh(es_env)
    assert world.ids() == [doctor_id]
    world.affiliation(aff, doctor_id, clinic_id, event="affiliation.ended", t=15)
    refresh(es_env)
    assert world.ids() == []  # klinikasiz klinika xizmati — taklif emas


def test_suggest_and_unknown_order_events(world: World, es_env: str) -> None:
    doctor_id, _ = world.bookable_clinic_doctor("Abdullayev Sardor")
    # Shifokor approved'dan OLDIN reyting/xizmat eventi — yo'qolmaydi, approved kelganda hujjatga tushadi
    early = str(uuid.uuid4())
    world.run("rating.recalculated", sample_data(
        "rating.recalculated", doctor_id=early, rating=4.2, reviews_count=5, clinic_id=None, clinic_rating=None,
        clinic_reviews_count=None,
    ), occurred_at=at(1))  # fmt: skip
    refresh(es_env)
    assert early not in world.ids()
    suggestions = world.api.get("/v1/search/suggest", params={"q": "abdu"}).json()
    assert [d["id"] for d in suggestions["doctors"]] == [doctor_id]
    assert world.api.get("/v1/search/suggest", params={"q": "shif"}).json()["clinics"][0]["name"] == "Shifo Plus"
    assert world.api.get("/v1/search/suggest", params={"q": "kard"}).json()["specializations"][0]["slug"] == "kardiolog"
    assert world.api.get("/v1/search/suggest", params={"q": "a"}).status_code == 422


def test_external_version_prevents_stale_overwrite(es_env: str) -> None:
    from app.clients import elastic

    async def scenario(es: Any) -> list[bool]:
        index = elastic.alias(elastic.CLINICS)
        doc = {"id": "c1", "name": "A", "is_active": True, "status": "active"}
        first = await elastic.put(es, index, "c1", doc, 5)
        stale = await elastic.put(es, index, "c1", {**doc, "name": "OLD"}, 4)
        await es.indices.refresh(index=index)
        got = await es.get(index=index, id="c1")
        assert got["_source"]["name"] == "A"
        return [first, stale]

    assert es_run(scenario) == [True, False]


def test_fallback_to_postgres_when_elasticsearch_down(world: World, es_env: str, monkeypatch) -> None:
    doctor_id, _ = world.bookable_clinic_doctor("Valiyev Olim")
    from app.clients import elastic

    monkeypatch.setenv("ELASTICSEARCH_URL", "http://127.0.0.1:1")
    elastic.get_es.cache_clear()
    from app.core.config import get_settings

    get_settings.cache_clear()
    r = world.api.get("/v1/search/doctors", params={"q": "Valiyev", "specialization": str(world.spec)})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "postgres" and body["degraded"] is True
    assert [d["id"] for d in body["items"]] == [doctor_id] and body["total"] is None
    assert body["items"][0]["specialization_names"] == ["Kardiolog"]
    clinics = world.api.get("/v1/search/clinics", params={"q": "shifo", "city": "toshkent"}).json()
    assert clinics["engine"] == "postgres" and clinics["items"][0]["name"] == "Shifo Plus"
    suggest = world.api.get("/v1/search/suggest", params={"q": "kard"}).json()
    assert suggest["engine"] == "postgres" and suggest["specializations"] and suggest["doctors"] == []
