"""Sof funksiyalar: so'rov tanasi, versiya, masofa."""

from __future__ import annotations

import uuid
from datetime import date

from app.services import queries, reindex


def test_doctor_body_filters() -> None:
    body = queries.doctor_body(
        queries.DoctorQuery(q="ali", place="home", lat=41.3, lng=69.2, available_on=date(2026, 10, 22))
    )
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"is_bookable": True}} in filters and {"term": {"places": "home"}} in filters
    assert any("script" in f for f in filters)  # uy — shifokorning o'z radiusi
    assert any(f.get("range", {}).get("next_available_at", {}).get("lt", "").startswith("2026-10-23") for f in filters)
    both = queries.doctor_body(queries.DoctorQuery(lat=41.3, lng=69.2))["query"]["bool"]["filter"][-1]
    assert both["bool"]["minimum_should_match"] == 1
    spec = uuid.uuid4()
    assert {"term": {"specialization_ids": str(spec)}} in queries.doctor_body(queries.DoctorQuery(specialization=spec))[
        "query"
    ]["bool"]["filter"]
    assert queries._doctor_sort(queries.DoctorQuery(sort="distance"))[0] == "_score"  # koordinatasiz — relevantlik
    assert "next_available_at" in queries._doctor_sort(queries.DoctorQuery(sort="availability"))[0]
    clinic = queries.clinic_body(queries.ClinicQuery(q="x", city="Toshkent", lat=1, lng=2, sort="distance"))
    assert "_geo_distance" in clinic["sort"][0]


def test_helpers() -> None:
    assert reindex._next_version(None) == 2 and reindex._next_version("t_doctors_v7") == 8
    assert (
        queries._distance([None], 41.0, 69.0) is None
        and queries._distance([{"lat": 41, "lon": 69}], None, None) is None
    )
    assert queries._distance([{"lat": 41.0, "lon": 69.0}, {"lat": 41.3, "lon": 69.2}], 41.0, 69.0) == 0.0
    assert queries._like("50%_a") == r"50\%\_a"
