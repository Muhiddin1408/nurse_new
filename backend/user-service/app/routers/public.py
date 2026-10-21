"""/v1/users/me — user JWT: profil, bemorlar, manzillar, sevimli shifokorlar, akkauntni o'chirish.

Har so'rovda egalik so'rov ichida (`WHERE user_id = :sub`): begona ID — 404 (IDOR, monolit A1/A4).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.core.security import CurrentUser, get_current_user
from app.models import Profile
from app.repositories import users as repo
from app.routers.deps import Files, Ip, Session
from app.schemas.users import (
    AddressIn,
    AddressOut,
    AddressUpdate,
    FavoriteOut,
    MeOut,
    MeUpdate,
    PatientIn,
    PatientOut,
    PatientUpdate,
)
from app.services import users
from app.services.errors import AddressNotFound, PatientNotFound

router = APIRouter(prefix="/v1/users/me", tags=["users"])

User = Annotated[CurrentUser, Depends(get_current_user)]
NO_CONTENT = status.HTTP_204_NO_CONTENT


def _me(user: CurrentUser, profile: Profile) -> MeOut:
    return MeOut(
        id=user.user_id,
        phone=user.phone,
        roles=sorted(user.roles),
        full_name=profile.full_name,
        preferred_language=profile.preferred_language,
        avatar_url=profile.avatar_url,
        avatar_thumbnail_url=profile.avatar_thumbnail_url,
        created_at=profile.created_at,
    )


@router.get("", response_model=MeOut)
async def me(user: User, session: Session) -> MeOut:
    return _me(user, await users.get_me(session, user.user_id))


@router.patch("", response_model=MeOut)
async def update_me(body: MeUpdate, user: User, session: Session, files: Files) -> MeOut:
    profile = await users.update_me(session, files, user.user_id, body.model_dump(exclude_unset=True))
    return _me(user, profile)


@router.delete("", status_code=NO_CONTENT)
async def delete_me(user: User, session: Session, files: Files, ip: Ip) -> Response:
    """Akkauntni o'chirish (anonimlashtirish). Faol bron bo'lsa — 409 `ACTIVE_BOOKINGS_EXIST`."""
    await users.request_deletion(session, files, user.user_id, roles=user.roles, ip=ip)
    return Response(status_code=NO_CONTENT)


# --- patients --------------------------------------------------------------------------------


@router.get("/patients", response_model=list[PatientOut])
async def list_patients(user: User, session: Session) -> list[PatientOut]:
    return [PatientOut.model_validate(p) for p in await repo.list_patients(session, user.user_id)]


@router.post("/patients", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def create_patient(body: PatientIn, user: User, session: Session) -> PatientOut:
    return PatientOut.model_validate(await users.create_patient(session, user.user_id, body.model_dump()))


@router.get("/patients/{patient_id}", response_model=PatientOut)
async def get_patient(patient_id: UUID, user: User, session: Session) -> PatientOut:
    patient = await repo.get_owned_patient(session, user.user_id, patient_id)
    if patient is None:
        raise PatientNotFound()
    return PatientOut.model_validate(patient)


@router.patch("/patients/{patient_id}", response_model=PatientOut)
async def update_patient(patient_id: UUID, body: PatientUpdate, user: User, session: Session) -> PatientOut:
    changes = body.model_dump(exclude_unset=True)
    return PatientOut.model_validate(await users.update_patient(session, user.user_id, patient_id, changes))


@router.delete("/patients/{patient_id}", status_code=NO_CONTENT)
async def delete_patient(patient_id: UUID, user: User, session: Session) -> Response:
    await users.delete_patient(session, user.user_id, patient_id)
    return Response(status_code=NO_CONTENT)


# --- addresses -------------------------------------------------------------------------------


@router.get("/addresses", response_model=list[AddressOut])
async def list_addresses(user: User, session: Session) -> list[AddressOut]:
    return [AddressOut.model_validate(a) for a in await repo.list_addresses(session, user.user_id)]


@router.post("/addresses", response_model=AddressOut, status_code=status.HTTP_201_CREATED)
async def create_address(body: AddressIn, user: User, session: Session) -> AddressOut:
    return AddressOut.model_validate(await users.create_address(session, user.user_id, body.model_dump()))


@router.get("/addresses/{address_id}", response_model=AddressOut)
async def get_address(address_id: UUID, user: User, session: Session) -> AddressOut:
    address = await repo.get_owned_address(session, user.user_id, address_id)
    if address is None:
        raise AddressNotFound()
    return AddressOut.model_validate(address)


@router.patch("/addresses/{address_id}", response_model=AddressOut)
async def update_address(address_id: UUID, body: AddressUpdate, user: User, session: Session) -> AddressOut:
    changes = body.model_dump(exclude_unset=True)
    return AddressOut.model_validate(await users.update_address(session, user.user_id, address_id, changes))


@router.delete("/addresses/{address_id}", status_code=NO_CONTENT)
async def delete_address(address_id: UUID, user: User, session: Session) -> Response:
    await users.delete_address(session, user.user_id, address_id)
    return Response(status_code=NO_CONTENT)


# --- favorites -------------------------------------------------------------------------------


@router.get("/favorites", response_model=list[FavoriteOut])
async def list_favorites(user: User, session: Session) -> list[FavoriteOut]:
    return [FavoriteOut.model_validate(f) for f in await repo.list_favorites(session, user.user_id)]


@router.put("/favorites/{doctor_id}", status_code=NO_CONTENT)
async def add_favorite(doctor_id: UUID, user: User, session: Session) -> Response:
    """Idempotent. Shifokor mavjudligi bu yerda tekshirilmaydi (catalog — boshqa servis); ro'yxatni
    ko'rsatishda frontend/search mavjud bo'lmaganlarini tashlab ketadi."""
    await users.add_favorite(session, user.user_id, doctor_id)
    return Response(status_code=NO_CONTENT)


@router.delete("/favorites/{doctor_id}", status_code=NO_CONTENT)
async def remove_favorite(doctor_id: UUID, user: User, session: Session) -> Response:
    await users.remove_favorite(session, user.user_id, doctor_id)
    return Response(status_code=NO_CONTENT)
