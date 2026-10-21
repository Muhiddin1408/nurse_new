"""Fayl hayot sikli: presigned yuklash → complete (tekshiruv) → attach (egasi servis) → (release / tozalash).

Mijoz faylni to'g'ridan-to'g'ri S3'ga yuklaydi (servis orqali emas — katta fayllar API'ni band qilmaydi).
`complete` da servis o'zi tekshiradi: obyekt bormi, hajmi, haqiqiy turi (magic bytes); ommaviy rasm
qayta kodlanadi (EXIF/GPS yo'qoladi) va thumbnail yasaladi.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit

from app.core.config import get_settings
from app.core.purposes import EXTENSIONS, IMAGE_TYPES, PURPOSES, Purpose, sniff
from app.core.storage import Storage
from app.events import outbox
from app.metrics import FILES
from app.models import File
from app.repositories import files as repo
from app.services import images
from app.services.errors import (
    AttachNotAllowed,
    ContentTypeNotAllowed,
    FileNotFound,
    FileRejected,
    FileTooLarge,
    InvalidFileState,
    TooManyPendingUploads,
    UnknownPurpose,
    UploadMissing,
)

logger = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)


@dataclass(frozen=True)
class UploadTicket:
    file: File
    upload_url: str
    expires_in: int


@dataclass(frozen=True)
class SignedUrl:
    url: str
    expires_in: int | None  # None — ommaviy (doimiy) havola


def _purpose(name: str) -> Purpose:
    purpose = PURPOSES.get(name)
    if purpose is None:
        raise UnknownPurpose()
    return purpose


def safe_filename(name: str) -> str:
    """Faqat yuklab olishda ko'rsatiladigan nom: yo'l qismlari, `..` va maxsus belgilar olib tashlanadi."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"\.{2,}", ".", _SAFE_NAME.sub("_", base)).strip(" .")[:120]
    return cleaned or "file"


def urls_of(storage: Storage, file: File) -> tuple[str | None, str | None]:
    if file.visibility != "public" or file.status not in ("uploaded", "attached"):
        return None, None
    thumb = storage.public_url(file.thumbnail_key) if file.thumbnail_key else None
    return storage.public_url(file.object_key), thumb


async def create_upload(
    session: AsyncSession,
    storage: Storage,
    *,
    user_id: uuid.UUID,
    purpose_name: str,
    content_type: str,
    size: int,
    filename: str,
) -> UploadTicket:
    settings = get_settings()
    purpose = _purpose(purpose_name)
    if content_type not in purpose.content_types:
        raise ContentTypeNotAllowed(extra={"allowed": sorted(purpose.content_types)})
    if size <= 0 or size > purpose.max_bytes:
        raise FileTooLarge(extra={"max_bytes": purpose.max_bytes})
    if await repo.count_pending(session, user_id) >= settings.max_pending_uploads_per_user:
        raise TooManyPendingUploads()
    file_id = uuid.uuid4()
    now = datetime.now(UTC)
    # Kalitda foydalanuvchi ID'si YO'Q — ommaviy havoladan kimniki ekanini bilib bo'lmasin.
    key = f"{purpose.name}/{now:%Y/%m}/{file_id.hex}{EXTENSIONS[content_type]}"
    file = File(
        id=file_id,
        owner_user_id=user_id,
        purpose=purpose.name,
        visibility=purpose.visibility,
        bucket=storage.bucket(purpose.visibility),
        object_key=key,
        content_type=content_type,
        declared_size=size,
        original_name=safe_filename(filename),
        status="pending",
        created_at=now,
    )
    session.add(file)
    FILES.labels(purpose=purpose.name, result="requested").inc()
    url = storage.presign_put(file.bucket, key, content_type, settings.upload_url_ttl_seconds)
    return UploadTicket(file=file, upload_url=url, expires_in=settings.upload_url_ttl_seconds)


async def _owned(session: AsyncSession, file_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False) -> File:
    file = await repo.get(session, file_id, for_update=for_update)
    # Begona fayl — xuddi yo'qdek (mavjudligi oshkor qilinmaydi)
    if file is None or file.owner_user_id != user_id or file.status == "deleted":
        raise FileNotFound()
    return file


async def _reject(session: AsyncSession, storage: Storage, file: File, reason: str) -> None:
    await storage.delete(file.bucket, file.object_key)
    file.status = "rejected"
    file.reject_reason = reason
    file.deleted_at = datetime.now(UTC)
    await session.commit()
    FILES.labels(purpose=file.purpose, result=f"rejected_{reason}").inc()


async def complete(session: AsyncSession, storage: Storage, file_id: uuid.UUID, user_id: uuid.UUID) -> File:
    """Idempotent: allaqachon tekshirilgan faylga qayta `complete` — o'sha natija."""
    file = await _owned(session, file_id, user_id, for_update=True)
    if file.status in ("uploaded", "attached"):
        return file
    if file.status != "pending":
        raise InvalidFileState()
    purpose = PURPOSES[file.purpose]
    info = await storage.head(file.bucket, file.object_key)
    if info is None:
        raise UploadMissing()
    if info.size > purpose.max_bytes:
        await _reject(session, storage, file, "too_large")
        raise FileTooLarge(extra={"max_bytes": purpose.max_bytes})
    head = await storage.read(file.bucket, file.object_key, first_bytes=64)
    if sniff(head) != file.content_type:
        # Kengaytma/Content-Type yolg'on (masalan, .jpg nomli HTML yoki exe)
        await _reject(session, storage, file, "type_mismatch")
        raise FileRejected("Fayl mazmuni e'lon qilingan formatga mos emas")

    size = info.size
    if file.visibility == "public" and file.content_type in IMAGE_TYPES:
        try:
            processed = images.process(
                await storage.read(file.bucket, file.object_key), file.content_type, get_settings().thumbnail_size
            )
        except images.InvalidImage as exc:
            await _reject(session, storage, file, "bad_image")
            raise FileRejected("Rasmni o'qib bo'lmadi") from exc
        thumb_key = file.object_key.rsplit(".", 1)[0] + "_thumb.webp"
        await storage.put(file.bucket, file.object_key, processed.original, file.content_type)
        await storage.put(file.bucket, thumb_key, processed.thumbnail, "image/webp")
        file.thumbnail_key = thumb_key
        size = len(processed.original)
    file.size = size
    file.status = "uploaded"
    file.uploaded_at = datetime.now(UTC)
    await session.commit()
    FILES.labels(purpose=file.purpose, result="uploaded").inc()
    return file


async def get_owned(session: AsyncSession, file_id: uuid.UUID, user_id: uuid.UUID) -> File:
    return await _owned(session, file_id, user_id)


async def owner_url(
    session: AsyncSession, storage: Storage, file_id: uuid.UUID, user: Actor, *, ip: str | None
) -> SignedUrl:
    assert user.actor_id is not None
    file = await _owned(session, file_id, user.actor_id)
    return await _signed(session, storage, file, user, ttl=None, ip=ip)


async def _signed(
    session: AsyncSession, storage: Storage, file: File, actor: Actor, *, ttl: int | None, ip: str | None,
    viewer: dict[str, str] | None = None,
) -> SignedUrl:  # fmt: skip
    if file.status not in ("uploaded", "attached"):
        raise InvalidFileState()
    if file.visibility == "public":
        return SignedUrl(url=storage.public_url(file.object_key), expires_in=None)
    max_ttl = get_settings().private_url_max_ttl_seconds
    seconds = max(30, min(ttl or max_ttl, max_ttl))
    url = storage.presign_get(file.bucket, file.object_key, seconds, filename=file.original_name)
    # Maxfiy hujjatning HAR ochilishi auditga (ARCHITECTURE 11)
    record_audit(
        outbox,
        session,
        actor,
        action="file.view",
        object_type="file",
        object_id=file.id,
        after={"purpose": file.purpose, "ttl": seconds, **(viewer or {})},
        ip=ip,
    )
    await session.commit()
    return SignedUrl(url=url, expires_in=seconds)


async def admin_url(
    session: AsyncSession, storage: Storage, file_id: uuid.UUID, admin: Actor, *, ip: str | None
) -> SignedUrl:
    file = await repo.get(session, file_id)
    if file is None or file.status == "deleted":
        raise FileNotFound()
    return await _signed(session, storage, file, admin, ttl=None, ip=ip)


async def service_url(
    session: AsyncSession,
    storage: Storage,
    file_id: uuid.UUID,
    *,
    caller: str,
    ttl: int | None,
    viewer_type: str | None,
    viewer_id: str | None,
) -> SignedUrl:
    """Servis o'zi biriktirgan fayl uchun (masalan catalog — moderator ko'rishi uchun)."""
    file = await repo.get(session, file_id)
    if file is None or file.status == "deleted" or file.attached_service != caller:
        raise FileNotFound()
    viewer = {k: v for k, v in (("viewer_type", viewer_type), ("viewer_id", viewer_id)) if v}
    return await _signed(session, storage, file, Actor.system(caller), ttl=ttl, ip=None, viewer=viewer)


async def attach(
    session: AsyncSession,
    file_id: uuid.UUID,
    *,
    caller: str,
    owner_user_id: uuid.UUID,
    owner_ref: str,
    purpose_name: str | None,
) -> File:
    """Egasi servis faylni o'zlashtiradi. `owner_user_id` — faylni yuklagan user (begona fayl ID'sini
    biriktirib bo'lmasin). Takroriy chaqiruv (o'sha ref) — idempotent."""
    file = await repo.get(session, file_id, for_update=True)
    if file is None or file.owner_user_id != owner_user_id or file.status in ("deleted", "rejected"):
        raise FileNotFound()
    purpose = PURPOSES[file.purpose]
    if caller not in purpose.owner_services or (purpose_name is not None and purpose_name != file.purpose):
        raise AttachNotAllowed()
    if file.status == "attached":
        if file.attached_service == caller and file.attached_ref == owner_ref:
            return file
        raise InvalidFileState("Fayl boshqa obyektga biriktirilgan")
    if file.status != "uploaded":
        raise InvalidFileState("Fayl hali tekshirilmagan (complete)")
    file.status = "attached"
    file.attached_service = caller
    file.attached_ref = owner_ref
    file.attached_at = datetime.now(UTC)
    return file


async def release(session: AsyncSession, storage: Storage, file_id: uuid.UUID, *, caller: str, owner_ref: str) -> None:
    """Egasi servis endi faylga muhtoj emas (masalan, avatar almashtirildi) — obyekt o'chiriladi."""
    file = await repo.get(session, file_id, for_update=True)
    if file is None or file.status == "deleted":
        return
    if file.attached_service != caller or file.attached_ref != owner_ref:
        raise FileNotFound()
    await _delete_objects(storage, file)
    file.status = "deleted"
    file.deleted_at = datetime.now(UTC)


async def delete_own(session: AsyncSession, storage: Storage, file_id: uuid.UUID, user_id: uuid.UUID) -> None:
    file = await _owned(session, file_id, user_id, for_update=True)
    if file.status == "attached":
        raise InvalidFileState("Biriktirilgan faylni egasi servis orqali o'chiring")
    await _delete_objects(storage, file)
    file.status = "deleted"
    file.deleted_at = datetime.now(UTC)


async def _delete_objects(storage: Storage, file: File) -> None:
    await storage.delete(file.bucket, file.object_key)
    if file.thumbnail_key:
        await storage.delete(file.bucket, file.thumbnail_key)


async def purge_unattached(session: AsyncSession, storage: Storage, *, limit: int = 500) -> int:
    """24 soatda complete/attach qilinmagan fayllar (yetim yuklashlar) — obyekt va metadata."""
    before = datetime.now(UTC) - timedelta(hours=get_settings().unattached_ttl_hours)
    stale = await repo.stale_unattached(session, before, limit)
    now = datetime.now(UTC)
    for file in stale:
        await _delete_objects(storage, file)
        file.status = "deleted"
        file.deleted_at = now
    await session.commit()
    if stale:
        logger.info("unattached files purged", extra={"count": len(stale)})
    return len(stale)
