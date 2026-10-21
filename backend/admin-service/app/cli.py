"""Admin bootstrap CLI (birinchi admin — API orqali yaratib bo'lmaydi, chunki hali hech kim kira olmaydi).

    docker compose run --rm admin-service python -m app.cli create-admin --login ops@medbron.uz \\
        --full-name "Ops" --role superadmin
    docker compose run --rm admin-service python -m app.cli reset-totp --login ops@medbron.uz

Parol stdin'dan so'raladi (buyruq tarixida va `ps` da ko'rinmasin). TOTP URI bir marta chiqariladi —
autentifikator ilovaga qo'shing (QR: `qrencode -t ansiutf8 '<uri>'`).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from medbron_common.audit import Actor
from medbron_common.db import transaction

from app.core.database import get_engine, get_session_factory
from app.repositories import admin as repo
from app.services import admins
from app.services.errors import AdminNotFound, AlreadyExists, RoleNotFound, WeakPassword
from app.services.login import normalize_login

SYSTEM = Actor.system("cli")


def _read_password(stream_password: bool) -> str:
    if stream_password:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Parol: ")
    if first != getpass.getpass("Parol (takror): "):
        raise SystemExit("Parollar mos emas")
    return first


async def create_admin(login: str, full_name: str, role: str, password: str) -> str:
    async with get_session_factory()() as session, transaction(session):
        found = await repo.get_role_by_name(session, role)
        if found is None:
            raise RoleNotFound(f"Rol topilmadi: {role}")
        enrollment = await admins.create_admin(
            session, login=login, full_name=full_name, password=password, role_ids=[found.id], actor=SYSTEM, ip=None
        )
    return enrollment.totp_uri


async def reset_totp(login: str) -> str:
    async with get_session_factory()() as session, transaction(session):
        admin = await repo.get_admin_by_login(session, normalize_login(login))
        if admin is None:
            raise AdminNotFound()
        enrollment = await admins.reset_totp(session, admin.id, actor=SYSTEM, ip=None)
    return enrollment.totp_uri


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin")
    create.add_argument("--login", required=True)
    create.add_argument("--full-name", default="")
    create.add_argument("--role", default="superadmin")
    create.add_argument("--password-stdin", action="store_true", help="parolni stdin'dan o'qish (avtomatlashtirish)")
    reset = sub.add_parser("reset-totp")
    reset.add_argument("--login", required=True)
    args = parser.parse_args(argv)

    async def run() -> str:
        try:
            if args.command == "create-admin":
                password = _read_password(args.password_stdin)
                return await create_admin(args.login, args.full_name, args.role, password)
            return await reset_totp(args.login)
        finally:
            await get_engine().dispose()

    try:
        uri = asyncio.run(run())
    except (AdminNotFound, RoleNotFound, WeakPassword, AlreadyExists) as exc:
        print(f"XATO: {exc.message}", file=sys.stderr)
        return 1
    print("TOTP URI (bir marta ko'rsatiladi, autentifikator ilovaga qo'shing):")
    print(uri)
    return 0


if __name__ == "__main__":
    sys.exit(main())
