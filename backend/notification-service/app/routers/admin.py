"""/v1/admin/notifications — admin JWT + `notifications.manage`: shablonlar va yuborish jurnali."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from medbron_common.db import transaction
from medbron_common.errors import Unprocessable

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.models import Template
from app.repositories import notifications as repo
from app.routers.deps import Session
from app.schemas.notifications import LogOut, TemplateOut, TemplateUpdate
from app.services import templates

router = APIRouter(prefix="/v1/admin/notifications", tags=["admin"])

Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.NOTIFICATIONS_MANAGE))]
Lang = Annotated[str, Path(pattern="^(uz|ru)$")]


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(_: Manager, session: Session) -> list[TemplateOut]:
    overrides = {(t.key, t.language): t for t in (await session.scalars(select(Template))).all()}
    out = []
    for key, langs in sorted(templates.DEFAULTS.items()):
        for lang, text in sorted(langs.items()):
            override = overrides.get((key, lang))
            out.append(
                TemplateOut(
                    key=key,
                    language=lang,
                    default_text=text,
                    override_text=override.text if override else None,
                    is_active=override.is_active if override else True,
                    placeholders=sorted(templates.placeholders(text)),
                )
            )
    return out


@router.put("/templates/{key}/{language}", status_code=status.HTTP_204_NO_CONTENT)
async def put_template(key: str, language: Lang, body: TemplateUpdate, admin: Manager, session: Session) -> Response:
    """⚠️ SMS matni Eskiz'da moderatsiyadan o'tgan bo'lishi shart — o'zgartirishdan oldin tasdiqlating."""
    try:
        templates.validate_override(key, body.text)
    except templates.TemplateError as exc:
        raise Unprocessable(str(exc), code="INVALID_TEMPLATE") from exc
    values = {"text": body.text, "is_active": body.is_active, "updated_by": admin.admin_id}
    async with transaction(session):
        await session.execute(
            insert(Template)
            .values(key=key, language=language, **values)
            .on_conflict_do_update(index_elements=[Template.key, Template.language], set_=values)
        )
    templates.clear_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/templates/{key}/{language}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_template(key: str, language: Lang, _: Manager, session: Session) -> Response:
    async with transaction(session):
        await session.execute(delete(Template).where(Template.key == key, Template.language == language))
    templates.clear_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/logs", response_model=list[LogOut])
async def logs(
    _: Manager,
    session: Session,
    user_id: UUID | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    template: str | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[LogOut]:
    """Matn saqlanmaydi (shablon + parametrlar yetarli) — shaxsiy ma'lumot keraksiz ko'paymaydi."""
    rows = await repo.list_logs(session, user_id=user_id, status=status_, template=template, before=before, limit=limit)
    return [LogOut.model_validate(r) for r in rows]
