"""Rollar: ma'lumotdan hisoblanadi (monolit D1). `client` — har doim; `doctor` — tasdiqlangan shifokor
profili bor (to'xtatilganda ham rol saqlanadi, bron qila olishni catalog `bookability` hal qiladi);
`clinic_admin` — faol klinika a'zoligi bor.

Token claim'i — interfeys va servislar uchun ishora; resurs darajasidagi ruxsatni egasi tekshiradi.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import CLIENT, CLINIC_ADMIN, DOCTOR
from app.repositories import identity as repo


@dataclass(frozen=True)
class RoleSet:
    roles: list[str]
    doctor_id: uuid.UUID | None

    def default_active(self, preferred: str) -> str:
        return preferred if preferred in self.roles else CLIENT


async def compute_roles(session: AsyncSession, user_id: uuid.UUID) -> RoleSet:
    roles = [CLIENT]
    link = await repo.get_doctor_link(session, user_id)
    if link is not None:
        roles.append(DOCTOR)
    if await repo.has_active_membership(session, user_id):
        roles.append(CLINIC_ADMIN)
    return RoleSet(roles=roles, doctor_id=link.doctor_id if link else None)
