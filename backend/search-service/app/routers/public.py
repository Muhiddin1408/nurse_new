"""/v1/search — tokensiz ommaviy qidiruv (faqat bron qilsa bo'ladigan shifokorlar, faol klinikalar)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from medbron_common.errors import BadRequest

from app.routers.deps import RedisDep, Session
from app.schemas.search import ClinicResults, DoctorResults, Suggestions
from app.services import queries

router = APIRouter(prefix="/v1/search", tags=["search"])

Q = Annotated[str, Query(max_length=100)]
Lat = Annotated[float | None, Query(ge=-90, le=90)]
Lng = Annotated[float | None, Query(ge=-180, le=180)]
Radius = Annotated[float, Query(gt=0, le=100)]
Limit = Annotated[int, Query(ge=1, le=50)]
# Chuqur sahifalash (ES from+size) qimmat — 1000 dan keyin filtrni toraytirish kerak
Offset = Annotated[int, Query(ge=0, le=1000)]


def _coords(lat: float | None, lng: float | None) -> None:
    if (lat is None) != (lng is None):
        raise BadRequest("lat va lng birga beriladi", code="INVALID_COORDINATES")


@router.get("/doctors", response_model=DoctorResults)
async def doctors(
    session: Session,
    redis: RedisDep,
    q: Q = "",
    specialization: UUID | None = None,
    city: Annotated[str, Query(max_length=100)] = "",
    place: Literal["clinic", "home"] | None = None,
    lat: Lat = None,
    lng: Lng = None,
    radius_km: Radius = 15,
    available_on: date | None = None,
    sort: Literal["relevance", "rating", "price", "distance", "availability"] = "relevance",
    limit: Limit = 20,
    offset: Offset = 0,
) -> DoctorResults:
    _coords(lat, lng)
    query = queries.DoctorQuery(
        q=q.strip(), specialization=specialization, city=city.strip(), place=place, lat=lat, lng=lng,
        radius_km=radius_km, available_on=available_on, sort=sort, limit=limit, offset=offset,
    )  # fmt: skip
    return DoctorResults.model_validate(await queries.search_doctors(session, redis, query))


@router.get("/clinics", response_model=ClinicResults)
async def clinics(
    session: Session,
    redis: RedisDep,
    q: Q = "",
    specialization: UUID | None = None,
    city: Annotated[str, Query(max_length=100)] = "",
    lat: Lat = None,
    lng: Lng = None,
    radius_km: Radius = 15,
    sort: Literal["relevance", "rating", "price", "distance"] = "relevance",
    limit: Limit = 20,
    offset: Offset = 0,
) -> ClinicResults:
    _coords(lat, lng)
    query = queries.ClinicQuery(
        q=q.strip(), specialization=specialization, city=city.strip(), lat=lat, lng=lng, radius_km=radius_km,
        sort=sort, limit=limit, offset=offset,
    )  # fmt: skip
    return ClinicResults.model_validate(await queries.search_clinics(session, redis, query))


@router.get("/suggest", response_model=Suggestions)
async def suggest(session: Session, q: Annotated[str, Query(min_length=2, max_length=50)]) -> Suggestions:
    return Suggestions.model_validate(await queries.suggest(session, q.strip()))
