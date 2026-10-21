"""file-service internal API: shifokor hujjatlari va rasmlarni biriktirish, moderator uchun qisqa URL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from medbron_common.http_client import UpstreamError

from app.clients.base import BaseClient
from app.core.config import get_settings


@dataclass(frozen=True)
class AttachedFile:
    id: UUID
    url: str | None
    content_type: str


@dataclass(frozen=True)
class SignedUrl:
    url: str
    expires_in: int | None


class FileRejected(Exception):
    """file-service faylni rad etdi (begona, tekshirilmagan, noto'g'ri tur) — mijoz xatosi."""

    def __init__(self, code: str | None) -> None:
        super().__init__(code)
        self.code = code


class FileClient(BaseClient):
    target: ClassVar[str] = "file-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().file_service_url)

    async def attach(self, file_id: UUID, *, owner_user_id: UUID, owner_ref: str, purpose: str) -> AttachedFile:
        try:
            data = await self.http.post_json(
                f"/internal/v1/files/{file_id}/attach",
                json={"owner_user_id": str(owner_user_id), "owner_ref": owner_ref, "purpose": purpose},
                idempotency_key=f"attach:{file_id}:{owner_ref}",
            )
        except UpstreamError as exc:
            if exc.upstream_status in (403, 404, 409, 422):
                raise FileRejected(exc.upstream_code) from exc
            raise
        return AttachedFile(id=UUID(data["id"]), url=data.get("url"), content_type=data.get("content_type", ""))

    async def release(self, file_id: UUID, owner_ref: str) -> None:
        await self.http.post_json(
            f"/internal/v1/files/{file_id}/release", json={"owner_ref": owner_ref}, idempotency_key=f"release:{file_id}"
        )

    async def signed_url(self, file_id: UUID, *, viewer_type: str, viewer_id: UUID, ttl: int) -> SignedUrl:
        data = await self.http.get_json(
            f"/internal/v1/files/{file_id}/url",
            params={"ttl": ttl, "viewer_type": viewer_type, "viewer_id": str(viewer_id)},
        )
        return SignedUrl(url=data["url"], expires_in=data.get("expires_in"))
