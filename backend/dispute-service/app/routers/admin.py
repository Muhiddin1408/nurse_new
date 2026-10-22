"""/v1/admin/disputes — admin JWT + `disputes.resolve`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from medbron_common.http_client import with_fallback

from app.core import permissions
from app.core.config import get_settings
from app.core.security import CurrentAdmin, require_permission
from app.repositories import disputes as repo
from app.routers.deps import DepsDep, Ip, Session
from app.schemas.disputes import AdminDisputeOut, EvidenceOut, ResolveIn
from app.services import disputes
from app.services.errors import DisputeNotFound

router = APIRouter(prefix="/v1/admin/disputes", tags=["admin"])

Resolver = Annotated[CurrentAdmin, Depends(require_permission(permissions.DISPUTES_RESOLVE))]


@router.get("", response_model=list[AdminDisputeOut])
async def queue(
    _: Resolver,
    session: Session,
    status: str | None = Query("open", description="open | resolved | closed; bo'sh — hammasi"),
    overdue: bool = Query(False, description="faqat SLA muddati o'tgan ochiq nizolar"),
    booking_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminDisputeOut]:
    """SLA bo'yicha tartiblangan navbat (eng shoshilinchi birinchi)."""
    rows = await repo.search(
        session,
        status=status or None,
        overdue_at=datetime.now(UTC) if overdue else None,
        booking_id=booking_id,
        limit=limit,
        offset=offset,
    )
    return [AdminDisputeOut.model_validate(d) for d in rows]


@router.get("/{dispute_id}", response_model=AdminDisputeOut)
async def detail(dispute_id: UUID, admin: Resolver, session: Session, deps: DepsDep) -> AdminDisputeOut:
    """Dalillar qisqa muddatli havola bilan (har ochilish file-service auditiga yoziladi)."""
    d = await repo.get(session, dispute_id)
    if d is None:
        raise DisputeNotFound()
    out = AdminDisputeOut.model_validate(d)
    ttl = get_settings().evidence_url_ttl_seconds
    items = []
    for e in (await repo.evidence_of(session, [d.id]))[d.id]:
        item = EvidenceOut.model_validate(e)
        item.url = await with_fallback(
            deps.files.url(e.file_id, viewer_type="admin", viewer_id=str(admin.admin_id), ttl=ttl), None
        )
        items.append(item)
    out.evidence = items
    return out


@router.post("/{dispute_id}/resolve", response_model=AdminDisputeOut)
async def resolve(dispute_id: UUID, body: ResolveIn, admin: Resolver, session: Session, ip: Ip) -> AdminDisputeOut:
    """`refund_full` / `refund_partial` → payment refund qiladi (`doctor_liable=false` — shifokor ulushi 0);
    `rejected` — darhol yopiladi. Idempotent (o'sha qaror bilan takror)."""
    d = await disputes.resolve(
        session,
        admin,
        dispute_id,
        disputes.Resolution(body.resolution, body.refund_tiyin, body.doctor_liable, body.note),
        ip=ip,
    )
    return AdminDisputeOut.model_validate(d)
