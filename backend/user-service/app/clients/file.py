"""file-service internal API (`/internal/v1/files/*`): avatarni biriktirish va eskisini bo'shatish."""

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
    thumbnail_url: str | None


class FileNotAttachable(Exception):
    """file-service faylni rad etdi (begona, tekshirilmagan, noto'g'ri tur) — mijoz xatosi."""

    def __init__(self, code: str | None) -> None:
        super().__init__(code)
        self.code = code


class FileClient(BaseClient):
    target: ClassVar[str] = "file-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().file_service_url)

    async def attach_avatar(self, file_id: UUID, user_id: UUID) -> AttachedFile:
        ref = f"user:{user_id}:avatar"
        try:
            data = await self.http.post_json(
                f"/internal/v1/files/{file_id}/attach",
                json={"owner_user_id": str(user_id), "owner_ref": ref, "purpose": "avatar"},
                # attach o'sha ref bilan idempotent — tarmoq xatosida xavfsiz qayta urinish
                idempotency_key=f"attach:{file_id}:{ref}",
            )
        except UpstreamError as exc:
            if exc.upstream_status in (403, 404, 409, 422):
                raise FileNotAttachable(exc.upstream_code) from exc
            raise
        return AttachedFile(id=UUID(data["id"]), url=data.get("url"), thumbnail_url=data.get("thumbnail_url"))

    async def release_avatar(self, file_id: UUID, user_id: UUID) -> None:
        await self.http.post_json(
            f"/internal/v1/files/{file_id}/release",
            json={"owner_ref": f"user:{user_id}:avatar"},
            idempotency_key=f"release:{file_id}",
        )
