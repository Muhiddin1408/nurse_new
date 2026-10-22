"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClinicMembership, DoctorLink, OtpCode, RefreshToken, User

# --- users -----------------------------------------------------------------------------------


async def get_user(session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> User | None:
    stmt = select(User).where(User.id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def get_or_create_user_by_phone(session: AsyncSession, phone: str, now: datetime) -> tuple[User, bool]:
    """Parallel ikki verify bir xil yangi raqam bilan kelsa ham faqat BITTA user yaratiladi."""
    created_id = (
        await session.execute(
            insert(User)
            .values(id=uuid.uuid4(), phone=phone, is_phone_verified=True, created_at=now, updated_at=now)
            .on_conflict_do_nothing(index_elements=[User.phone])
            .returning(User.id)
        )
    ).scalar_one_or_none()
    user = (await session.scalars(select(User).where(User.phone == phone).with_for_update())).one()
    return user, created_id is not None


def users_query(*, phone: str | None, user_id: uuid.UUID | None, is_active: bool | None) -> Select[User]:
    stmt = select(User)
    if phone:
        stmt = stmt.where(User.phone == phone)
    if user_id:
        stmt = stmt.where(User.id == user_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    return stmt


async def list_users(
    session: AsyncSession,
    *,
    phone: str | None,
    user_id: uuid.UUID | None,
    is_active: bool | None,
    limit: int,
    offset: int,
) -> tuple[Sequence[User], int]:
    base = users_query(phone=phone, user_id=user_id, is_active=is_active)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.scalars(base.order_by(User.created_at.desc(), User.id).limit(limit).offset(offset))).all()
    return rows, int(total)


# --- roles projection ------------------------------------------------------------------------


async def get_doctor_link(session: AsyncSession, user_id: uuid.UUID) -> DoctorLink | None:
    return await session.get(DoctorLink, user_id)


async def has_active_membership(session: AsyncSession, user_id: uuid.UUID) -> bool:
    stmt = select(ClinicMembership.clinic_id).where(
        ClinicMembership.user_id == user_id, ClinicMembership.is_active.is_(True)
    )
    return (await session.execute(stmt.limit(1))).first() is not None


async def upsert_doctor_link(
    session: AsyncSession, *, user_id: uuid.UUID, doctor_id: uuid.UUID, status: str, changed_at: datetime
) -> bool:
    """Faqat yangiroq event qo'llanadi. Qaytaradi: qator o'zgardimi."""
    stmt = (
        insert(DoctorLink)
        .values(user_id=user_id, doctor_id=doctor_id, status=status, changed_at=changed_at)
        .on_conflict_do_update(
            index_elements=[DoctorLink.user_id],
            set_={"doctor_id": doctor_id, "status": status, "changed_at": changed_at},
            where=DoctorLink.changed_at < changed_at,
        )
        .returning(DoctorLink.user_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def set_membership(
    session: AsyncSession, *, user_id: uuid.UUID, clinic_id: uuid.UUID, active: bool, changed_at: datetime
) -> bool:
    stmt = (
        insert(ClinicMembership)
        .values(user_id=user_id, clinic_id=clinic_id, is_active=active, changed_at=changed_at)
        .on_conflict_do_update(
            index_elements=[ClinicMembership.user_id, ClinicMembership.clinic_id],
            set_={"is_active": active, "changed_at": changed_at},
            where=ClinicMembership.changed_at < changed_at,
        )
        .returning(ClinicMembership.user_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


# --- otp -------------------------------------------------------------------------------------


async def invalidate_active_otps(session: AsyncSession, phone: str, now: datetime) -> None:
    """Bir vaqtda faqat bitta kod amal qiladi."""
    await session.execute(update(OtpCode).where(OtpCode.phone == phone, OtpCode.used_at.is_(None)).values(used_at=now))


async def latest_active_otp(session: AsyncSession, phone: str) -> OtpCode | None:
    stmt = (
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.used_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    return (await session.scalars(stmt)).one_or_none()


async def delete_otp(session: AsyncSession, otp_id: uuid.UUID) -> None:
    await session.execute(delete(OtpCode).where(OtpCode.id == otp_id))


async def purge_otps(session: AsyncSession, older_than: datetime) -> int:
    result = await session.execute(delete(OtpCode).where(OtpCode.created_at < older_than))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- refresh tokens --------------------------------------------------------------------------


async def get_refresh_by_hash(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, reason: str, now: datetime) -> int:
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID, reason: str, now: datetime) -> int:
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def purge_refresh_tokens(session: AsyncSession, expired_before: datetime) -> int:
    """Muddati o'tgan tokenlar (rotatsiya qilinganlari ham) — qayta ishlatishni aniqlash uchun ular
    faqat yaroqlilik muddatigacha kerak."""
    result = await session.execute(delete(RefreshToken).where(RefreshToken.expires_at < expired_before))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
