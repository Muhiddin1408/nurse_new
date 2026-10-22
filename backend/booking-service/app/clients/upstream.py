"""Sinxron bog'liqliklar (reestr: catalog, schedule, pricing, user, admin) — contracts/openapi/*.internal.yaml.

Har client — bitta `ServiceClient` (pool, timeout, retry faqat GET/idempotency kalitli so'rovlarda, circuit
breaker). Upstream biznes xatolari (404/409/422) booking'ning o'z xatolariga tarjima qilinadi — ichki kodlar
tashqariga to'g'ridan-to'g'ri sizib chiqmaydi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from medbron_common.http_client import UpstreamError

from app.clients.base import BaseClient
from app.core.config import get_settings


class UpstreamRejected(Exception):
    """Upstream so'rovni biznes sababi bilan rad etdi (4xx). `code` — upstream xato kodi."""

    def __init__(self, target: str, status: int, code: str | None, detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"{target}: {status} {code}")
        self.target = target
        self.status = status
        self.code = code
        self.detail = detail or {}


def _rejected(exc: UpstreamError, *statuses: int) -> UpstreamRejected | None:
    if exc.upstream_status in statuses:
        return UpstreamRejected(exc.target, exc.upstream_status, exc.upstream_code, exc.upstream_detail)
    return None


@dataclass(frozen=True)
class Slot:
    id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    place: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    status: str
    hold_ref: str | None
    hold_expires_at: datetime | None
    price_adjust_percent: int

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Slot:
        return cls(
            id=UUID(d["id"]),
            doctor_id=UUID(d["doctor_id"]),
            clinic_id=UUID(d["clinic_id"]) if d.get("clinic_id") else None,
            place=d["place"],
            start_at=datetime.fromisoformat(d["start_at"]),
            end_at=datetime.fromisoformat(d["end_at"]),
            duration_minutes=int(d["duration_minutes"]),
            status=d["status"],
            hold_ref=d.get("hold_ref"),
            hold_expires_at=datetime.fromisoformat(d["hold_expires_at"]) if d.get("hold_expires_at") else None,
            price_adjust_percent=int(d.get("price_adjust_percent", 0)),
        )


class CatalogClient(BaseClient):
    target: ClassVar[str] = "catalog-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().catalog_service_url)

    async def bookability(self, doctor_id: UUID) -> dict[str, Any] | None:
        try:
            data: dict[str, Any] = await self.http.get_json(f"/internal/v1/doctors/{doctor_id}/bookability")
        except UpstreamError as exc:
            if exc.upstream_status == 404:
                return None
            raise
        return data

    async def bookable_services(self, doctor_id: UUID, service_ids: list[UUID]) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            "/internal/v1/services",
            params={"ids": ",".join(map(str, service_ids)), "bookable_for": str(doctor_id)},
        )
        return list(data["items"])

    async def access(self, user_id: UUID, *, clinic_id: UUID | None = None) -> dict[str, Any]:
        params = {"user_id": str(user_id)}
        if clinic_id:
            params["clinic_id"] = str(clinic_id)
        data: dict[str, Any] = await self.http.get_json("/internal/v1/access", params=params)
        return data

    async def clinic(self, clinic_id: UUID) -> dict[str, Any] | None:
        try:
            data: dict[str, Any] = await self.http.get_json(f"/internal/v1/clinics/{clinic_id}")
        except UpstreamError as exc:
            if exc.upstream_status == 404:
                return None
            raise
        return data


class ScheduleClient(BaseClient):
    target: ClassVar[str] = "schedule-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().schedule_service_url)

    async def slot(self, slot_id: UUID) -> Slot | None:
        try:
            return Slot.parse(await self.http.get_json(f"/internal/v1/slots/{slot_id}"))
        except UpstreamError as exc:
            if exc.upstream_status == 404:
                return None
            raise

    async def hold(
        self, slot_id: UUID, *, hold_ref: UUID, doctor_id: UUID, ttl_seconds: int, travel_buffer_minutes: int | None
    ) -> Slot:
        body: dict[str, Any] = {"hold_ref": str(hold_ref), "doctor_id": str(doctor_id), "ttl_seconds": ttl_seconds}
        if travel_buffer_minutes is not None:
            body["travel_buffer_minutes"] = travel_buffer_minutes
        try:
            data = await self.http.post_json(
                f"/internal/v1/slots/{slot_id}/hold", json=body, idempotency_key=f"hold:{slot_id}:{hold_ref}"
            )
        except UpstreamError as exc:
            rejected = _rejected(exc, 404, 409)
            if rejected:
                raise rejected from exc
            raise
        return Slot.parse(data)

    async def confirm(self, slot_id: UUID, hold_ref: UUID) -> Slot:
        try:
            data = await self.http.post_json(
                f"/internal/v1/slots/{slot_id}/confirm",
                json={"hold_ref": str(hold_ref)},
                idempotency_key=f"confirm:{slot_id}:{hold_ref}",
            )
        except UpstreamError as exc:
            rejected = _rejected(exc, 404, 409)
            if rejected:
                raise rejected from exc
            raise
        return Slot.parse(data)

    async def extend(self, slot_id: UUID, hold_ref: UUID, minutes: int) -> Slot:
        try:
            data = await self.http.post_json(
                f"/internal/v1/slots/{slot_id}/extend",
                json={"hold_ref": str(hold_ref), "minutes": minutes},
                idempotency_key=f"extend:{slot_id}:{hold_ref}:{minutes}",
            )
        except UpstreamError as exc:
            rejected = _rejected(exc, 404, 409)
            if rejected:
                raise rejected from exc
            raise
        return Slot.parse(data)

    async def release(self, slot_id: UUID, hold_ref: UUID) -> None:
        await self.http.post_json(
            f"/internal/v1/slots/{slot_id}/release",
            json={"hold_ref": str(hold_ref)},
            idempotency_key=f"release:{slot_id}:{hold_ref}",
        )

    async def free_slots(
        self, doctor_id: UUID, *, start: datetime, end: datetime, place: str | None, min_minutes: int, limit: int
    ) -> list[Slot]:
        params: dict[str, Any] = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "min_minutes": min_minutes,
            "limit": limit,
        }
        if place:
            params["place"] = place
        data = await self.http.get_json(f"/internal/v1/doctors/{doctor_id}/free-slots", params=params)
        return [Slot.parse(item) for item in data["items"]]


class PricingClient(BaseClient):
    target: ClassVar[str] = "pricing-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().pricing_service_url)

    async def quote(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            # Quote yaratish yon ta'sirsiz (faqat Redis'da 10 daq) — tarmoq xatosida qayta urinish xavfsiz
            data: dict[str, Any] = await self.http.post_json(
                "/internal/v1/quotes", json=body, idempotency_key=f"quote:{body['client_id']}:{body['slot_start_at']}"
            )
        except UpstreamError as exc:
            rejected = _rejected(exc, 422)
            if rejected:
                raise rejected from exc
            raise
        return data

    async def commit(self, quote_id: str, *, booking_id: UUID, client_id: UUID) -> dict[str, Any]:
        try:
            data: dict[str, Any] = await self.http.post_json(
                f"/internal/v1/quotes/{quote_id}/commit",
                json={"booking_id": str(booking_id), "client_id": str(client_id)},
                idempotency_key=f"commit:{booking_id}",
            )
        except UpstreamError as exc:
            rejected = _rejected(exc, 404, 409)
            if rejected:
                raise rejected from exc
            raise
        return data


class UserClient(BaseClient):
    target: ClassVar[str] = "user-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().user_service_url)

    async def _owned(self, path: str) -> dict[str, Any] | None:
        try:
            data: dict[str, Any] = await self.http.get_json(path)
        except UpstreamError as exc:
            if exc.upstream_status == 404:
                return None
            raise
        return data

    async def patient(self, user_id: UUID, patient_id: UUID) -> dict[str, Any] | None:
        return await self._owned(f"/internal/v1/users/{user_id}/patients/{patient_id}")

    async def address(self, user_id: UUID, address_id: UUID) -> dict[str, Any] | None:
        return await self._owned(f"/internal/v1/users/{user_id}/addresses/{address_id}")


class AdminClient(BaseClient):
    target: ClassVar[str] = "admin-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().admin_service_url)
