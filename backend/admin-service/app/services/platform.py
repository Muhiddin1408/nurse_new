"""Global sozlamalar: o'qish (admin va internal), o'zgartirish (optimistic concurrency + event + audit)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.core.platform_settings import SECTIONS, PlatformSettings, merge
from app.events import outbox
from app.metrics import SETTINGS_CHANGED
from app.repositories import admin as repo
from app.services.errors import InvalidSettings, SettingsVersionConflict


@dataclass(frozen=True)
class SettingsSnapshot:
    version: int
    values: PlatformSettings


async def current(session: AsyncSession) -> SettingsSnapshot:
    stored, version = await repo.load_settings(session)
    return SettingsSnapshot(version=version, values=merge(stored))


async def update(
    session: AsyncSession,
    changes: dict[str, Any],
    *,
    expected_version: int,
    actor: Actor,
    ip: str | None,
) -> SettingsSnapshot:
    unknown = sorted(set(changes) - set(SECTIONS))
    if unknown:
        raise InvalidSettings(f"Noma'lum bo'lim: {', '.join(unknown)}")
    version = await repo.lock_settings_version(session)
    if version != expected_version:
        raise SettingsVersionConflict(extra={"current_version": version})
    stored, _ = await repo.load_settings(session)
    old = merge(stored)
    current_values = old.model_dump()
    # Bo'lim ichida faqat yuborilgan maydonlar o'zgaradi (qolganlari — joriy qiymat, standart EMAS)
    merged = {
        section: ({**current_values[section], **value} if isinstance(value, dict) else value)
        for section, value in changes.items()
    }
    try:
        new = PlatformSettings.model_validate({**current_values, **merged})
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise InvalidSettings("Sozlamalar noto'g'ri", extra={"errors": errors}) from exc

    old_dump, new_dump = old.model_dump(), new.model_dump()
    changed = [s for s in SECTIONS if old_dump[s] != new_dump[s]]
    if not changed:
        return SettingsSnapshot(version=version, values=old)
    now = datetime.now(UTC)
    assert actor.actor_id is not None
    for section in changed:
        await repo.save_section(session, section, new_dump[section], actor.actor_id, now)
    new_version = await repo.bump_version(session, now)
    outbox.add_event(
        session,
        Events.ADMIN_SETTINGS_CHANGED,
        aggregate_id="platform",
        data={"keys": changed, "version": new_version, "changed_by": actor.actor_id},
    )
    record_audit(
        outbox,
        session,
        actor,
        action="settings.update",
        object_type="settings",
        object_id="platform",
        before={s: old_dump[s] for s in changed},
        after={s: new_dump[s] for s in changed},
        ip=ip,
    )
    SETTINGS_CHANGED.inc()
    return SettingsSnapshot(version=new_version, values=new)


def etag(version: int) -> str:
    return f'"v{version}"'
