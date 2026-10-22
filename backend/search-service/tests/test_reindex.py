"""Reindex: admin navbati, catalog eksportidan yangi indeks, alias almashinuvi, ikki tomonlama yozish, xato holati."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, sample_data

from tests.conftest import es_run

pytestmark = pytest.mark.integration


class FakeCatalog:
    def __init__(self) -> None:
        self.spec = str(uuid.uuid4())
        self.clinic = str(uuid.uuid4())
        self.doctor_items: list[dict[str, Any]] = []
        self.fail = False

    def add_doctor(self, name: str, *, status: str = "approved", price: int = 10_000_000) -> str:
        doctor_id = str(uuid.uuid4())
        self.doctor_items.append({
            "doctor_id": doctor_id, "user_id": str(uuid.uuid4()), "full_name": name, "status": status,
            "specialization_ids": [self.spec], "experience_years": 5, "languages": ["uz"],
            "accepts_home_visits": False, "home_visit_radius_km": 10, "home_base": None, "photo_file_id": None,
            "rating": 4.0, "reviews_count": 3,
            "affiliations": [{"affiliation_id": str(uuid.uuid4()), "clinic_id": self.clinic, "status": "active"}],
            "services": [{
                "id": str(uuid.uuid4()), "doctor_id": doctor_id, "clinic_id": None, "specialization_id": self.spec,
                "name": "Ko'rik", "name_ru": "", "place": "clinic", "duration_minutes": 30, "price_tiyin": price,
            }],
        })  # fmt: skip
        return doctor_id

    async def _pages(self, items: list[dict[str, Any]], size: int) -> AsyncIterator[list[dict[str, Any]]]:
        if self.fail:
            raise RuntimeError("catalog-service down")
        for start in range(0, len(items), size):
            yield items[start : start + size]

    def doctors(self, page_size: int) -> AsyncIterator[list[dict[str, Any]]]:
        return self._pages(sorted(self.doctor_items, key=lambda d: d["doctor_id"]), page_size)

    def clinics(self, page_size: int) -> AsyncIterator[list[dict[str, Any]]]:
        clinic = {
            "clinic_id": self.clinic, "name": "Reindex Klinika", "city": "Toshkent", "street": "x",
            "latitude": 41.3, "longitude": 69.2, "status": "active", "working_hours": {}, "rating": None,
            "reviews_count": 0, "services": [],
        }  # fmt: skip
        return self._pages([clinic], page_size)

    async def specializations(self) -> list[dict[str, Any]]:
        return [{"id": self.spec, "name": "Nevrolog", "name_ru": "Невролог", "slug": "nevrolog"}]


def admin(admin_keys: Any, *perms: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_admin_token(admin_keys, permissions=list(perms))}"}


def indices(prefix: str) -> dict[str, list[str]]:
    async def go(es: Any) -> dict[str, list[str]]:
        found = await es.indices.get_alias(index=f"{prefix}*")
        return {name: sorted(info["aliases"]) for name, info in found.body.items()}

    return es_run(go)


def test_reindex_builds_new_index_and_swaps_alias(
    api: TestClient, run_handler, run_async, db, admin_keys, es_env: str
) -> None:
    from app.services import reindex

    catalog = FakeCatalog()
    kept = catalog.add_doctor("Nazarov Ulug'bek")
    catalog.add_doctor("Pending Doctor", status="suspended")
    # Proyeksiyada bor, lekin catalog'da endi yo'q (rad etilgan) — reindex'dan keyin qidiruvdan chiqadi
    ghost = str(uuid.uuid4())
    run_handler("doctor.approved", sample_data("doctor.approved", doctor_id=ghost, full_name="Ghost Doctor",
                                               specialization_ids=[catalog.spec], accepts_home_visits=False),
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC))  # fmt: skip

    h = admin(admin_keys, "search.manage")
    assert api.post("/v1/admin/search/reindex", headers=admin(admin_keys, "bookings.read")).status_code == 403
    r = api.post("/v1/admin/search/reindex", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "pending"
    again = api.post("/v1/admin/search/reindex", headers=h)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "REINDEX_RUNNING"

    assert run_async(lambda f: reindex.run_pending(f, catalog)) == "done"  # type: ignore[arg-type]
    assert run_async(lambda f: reindex.run_pending(f, catalog)) is None  # navbat bo'sh
    runs = api.get("/v1/admin/search/reindex", headers=h).json()
    assert runs[0]["status"] == "done" and runs[0]["doctors_count"] == 3 and runs[0]["clinics_count"] == 1
    assert indices(es_env) == {f"{es_env}doctors_v2": [f"{es_env}doctors"], f"{es_env}clinics_v2": [f"{es_env}clinics"]}

    body = api.get("/v1/search/doctors").json()
    assert [d["id"] for d in body["items"]] == [kept]
    assert body["items"][0]["specialization_names"] == ["Nevrolog", "Невролог"]
    assert db.scalar("SELECT status FROM doctors WHERE id = :id", id=uuid.UUID(ghost)) == "removed"
    clinics = api.get("/v1/search/clinics").json()["items"]
    assert [c["name"] for c in clinics] == ["Reindex Klinika"] and clinics[0]["doctors_count"] == 1

    # Ikkinchi reindex — v3, eski v2 o'chiriladi; ikkinchi so'rov endi ruxsat
    assert api.post("/v1/admin/search/reindex", headers=h).status_code == 202
    assert run_async(lambda f: reindex.run_pending(f, catalog)) == "done"  # type: ignore[arg-type]
    assert sorted(indices(es_env)) == [f"{es_env}clinics_v3", f"{es_env}doctors_v3"]


def test_events_during_reindex_are_written_to_both_indexes(
    api: TestClient, run_handler, run_async, db, admin_keys, es_env: str
) -> None:
    from app.services import reindex

    catalog = FakeCatalog()
    doctor_id = catalog.add_doctor("Usmonov Akmal")
    run = api.post("/v1/admin/search/reindex", headers=admin(admin_keys, "search.manage")).json()
    snap = run_async(lambda f: reindex._start(f, uuid.UUID(run["id"])))
    assert snap is not None
    # Reindex ishlayotganda event keldi → joriy (v1) va yangi (v2) indekslarga
    data = sample_data(
        "doctor.approved", doctor_id=doctor_id, full_name="Usmonov Akmal", specialization_ids=[catalog.spec],
        accepts_home_visits=False,
    )  # fmt: skip
    run_handler("doctor.approved", data)

    async def both(es: Any) -> list[bool]:
        return [
            bool((await es.exists(index=f"{es_env}doctors_v1", id=doctor_id)).body),
            bool((await es.exists(index=f"{es_env}doctors_v2", id=doctor_id)).body),
        ]

    assert es_run(both) == [True, True]


def test_failed_reindex_keeps_current_index(api: TestClient, run_async, db, admin_keys, es_env: str) -> None:
    from app.services import reindex

    catalog = FakeCatalog()
    catalog.fail = True
    h = admin(admin_keys, "search.manage")
    api.post("/v1/admin/search/reindex", headers=h)
    assert run_async(lambda f: reindex.run_pending(f, catalog)) == "failed"  # type: ignore[arg-type]
    runs = api.get("/v1/admin/search/reindex", headers=h).json()
    assert runs[0]["status"] == "failed" and "catalog-service down" in runs[0]["error"]
    assert sorted(indices(es_env)) == [f"{es_env}clinics_v1", f"{es_env}doctors_v1"]  # yangi indekslar o'chirildi
    # Yiqilgan (stale) running — yangi so'rovga yo'l ochiladi
    assert api.post("/v1/admin/search/reindex", headers=h).status_code == 202
    db.scalar(
        "UPDATE reindex_runs SET status = 'running', started_at = now() - interval '2 hours' WHERE status = 'pending'"
    )
    assert api.post("/v1/admin/search/reindex", headers=h).status_code == 202
    assert db.scalar("SELECT count(*) FROM reindex_runs WHERE error = 'stale (timeout)'") == 1
