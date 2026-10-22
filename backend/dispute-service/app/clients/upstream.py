"""Sinxron bog'liqliklar (reestr: booking, file; admin — global sozlamalar) — contracts/openapi/*.internal.yaml.

Upstream biznes xatolari (404/409/403) dispute'ning o'z xatolariga tarjima qilinadi — ichki kodlar tashqariga
to'g'ridan-to'g'ri sizib chiqmaydi.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from medbron_common.http_client import UpstreamError

from app.clients.base import BaseClient
from app.core.config import get_settings


class BookingClient(BaseClient):
    target: ClassVar[str] = "booking-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().booking_service_url)

    async def summary(self, booking_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        """`{booking_id, number, status, client_id, doctor_id, start_at, paid_tiyin, total_tiyin}`.
        Bron yo'q yoki shu userniki emas — None (booking ikkalasiga ham 404 beradi, A4)."""
        try:
            data: dict[str, Any] = await self.http.get_json(
                f"/internal/v1/bookings/{booking_id}/summary", params={"user_id": str(user_id)}
            )
        except UpstreamError as exc:
            if exc.upstream_status == 404:
                return None
            raise
        return data


class FileRejected(Exception):
    """file-service faylni biriktirmadi (topilmadi / begona / tekshirilmagan / boshqa obyektda)."""

    def __init__(self, status: int, code: str | None) -> None:
        super().__init__(f"file-service: {status} {code}")
        self.status = status
        self.code = code


class FileClient(BaseClient):
    target: ClassVar[str] = "file-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().file_service_url)

    async def attach(self, file_id: UUID, *, owner_user_id: UUID, owner_ref: str) -> dict[str, Any]:
        """Idempotent (o'sha ref bilan takror — o'sha fayl). Begona fayl ID'si — FileRejected."""
        body = {"owner_user_id": str(owner_user_id), "owner_ref": owner_ref, "purpose": "dispute_evidence"}
        try:
            data: dict[str, Any] = await self.http.post_json(f"/internal/v1/files/{file_id}/attach", json=body)
        except UpstreamError as exc:
            if exc.upstream_status in (403, 404, 409, 422):
                raise FileRejected(exc.upstream_status, exc.upstream_code) from exc
            raise
        return data

    async def url(self, file_id: UUID, *, viewer_type: str, viewer_id: str, ttl: int) -> str:
        """Qisqa muddatli presigned URL; har ochilish file-service auditiga (`viewer_*`)."""
        params = {"ttl": ttl, "viewer_type": viewer_type, "viewer_id": viewer_id}
        data: dict[str, Any] = await self.http.get_json(f"/internal/v1/files/{file_id}/url", params=params)
        return str(data["url"])


class AdminClient(BaseClient):
    """Faqat global sozlamalar uchun (`PlatformSettingsClient` shu http client orqali)."""

    target: ClassVar[str] = "admin-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().admin_service_url)
