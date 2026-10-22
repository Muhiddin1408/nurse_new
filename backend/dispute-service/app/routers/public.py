"""/v1/disputes — mijoz (user JWT). Begona nizo/bron — 404 (A4)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser, get_current_user
from app.models import Dispute
from app.repositories import disputes as repo
from app.routers.deps import DepsDep, Session
from app.schemas.disputes import DisputeOut, EvidenceIn, EvidenceOut, OpenIn
from app.services import disputes

router = APIRouter(tags=["disputes"])

User = Annotated[CurrentUser, Depends(get_current_user)]


async def _out(session: Session, rows: list[Dispute]) -> list[DisputeOut]:
    evidence = await repo.evidence_of(session, [d.id for d in rows])
    out = []
    for d in rows:
        item = DisputeOut.model_validate(d)
        item.evidence = [EvidenceOut.model_validate(e) for e in evidence[d.id]]
        out.append(item)
    return out


@router.post("/v1/disputes", response_model=DisputeOut, status_code=status.HTTP_201_CREATED)
async def open_dispute(body: OpenIn, user: User, session: Session, deps: DepsDep) -> DisputeOut:
    """Bron `confirmed/in_progress/completed/no_show` va mijozniki bo'lishi shart. Bitta bronga bitta nizo (409)."""
    d = await disputes.open_dispute(
        session,
        deps,
        client_id=user.user_id,
        booking_id=body.booking_id,
        reason=body.reason,
        description=body.description,
    )
    return (await _out(session, [d]))[0]


@router.get("/v1/disputes/mine", response_model=list[DisputeOut])
async def mine(
    user: User,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DisputeOut]:
    return await _out(session, await repo.list_for_client(session, user.user_id, limit=limit, offset=offset))


@router.get("/v1/disputes/{dispute_id}", response_model=DisputeOut)
async def detail(dispute_id: UUID, user: User, session: Session) -> DisputeOut:
    return (await _out(session, [await disputes.owned(session, dispute_id, user.user_id)]))[0]


@router.post("/v1/disputes/{dispute_id}/evidence", response_model=DisputeOut)
async def add_evidence(dispute_id: UUID, body: EvidenceIn, user: User, session: Session, deps: DepsDep) -> DisputeOut:
    """Fayl avval `POST /v1/files/uploads` (`purpose=dispute_evidence`) + `complete` bilan yuklanadi. Idempotent."""
    d = await disputes.add_evidence(session, deps, dispute_id=dispute_id, client_id=user.user_id, file_id=body.file_id)
    return (await _out(session, [d]))[0]
