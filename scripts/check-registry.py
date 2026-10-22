#!/usr/bin/env python3
"""SERVICE_REGISTRY ↔ servis konfiguratsiyasi (BLUEPRINT 11, Trasso #8/#9).

- reestr ichida: NN, host port, Postgres va Redis portlari takrorlanmaydi va NN sxemasiga mos
  (81NN / 54NN / 63NN), ichki port 8080;
- backend/<servis>/.env.example: SERVICE_NAME, HOST_PORT, POSTGRES_HOST_PORT, REDIS_HOST_PORT
  reestrga mos; RABBITMQ_URL ichki manzil `medbron-rabbitmq:5672`;
- .env.example da haqiqiy sirga o'xshash qiymat yo'q.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _registry import ROOT, load_services  # noqa: E402

errors: list[str] = []
SECRET_KEYS = re.compile(r"(PASSWORD|SECRET|TOKEN|_KEY$|DSN)", re.I)
# Sir EMAS — sozlama: muddat, uzunlik, urinishlar soni, yo'l (`ACCESS_TOKEN_TTL_SECONDS`, `PASSWORD_MIN_LENGTH`)
NOT_SECRET = re.compile(r"_(SECONDS|DAYS|HOURS|MINUTES|LENGTH|ATTEMPTS|PATH)$", re.I)


def parse_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def main() -> int:
    services = load_services()
    for column in ("nn", "host_port", "pg_port", "redis_port"):
        values = [getattr(s, column) for s in services.values() if getattr(s, column) not in {"—", "-"}]
        for value, count in Counter(values).items():
            if count > 1:
                errors.append(f"reestr: {column}={value} {count} marta takrorlangan")
    for s in services.values():
        if s.internal_port != "8080":
            errors.append(f"reestr: {s.name} ichki port {s.internal_port} (standart 8080)")
        expect = {"host_port": f"81{s.nn}", "pg_port": f"54{s.nn}", "redis_port": f"63{s.nn}"}
        for col, want in expect.items():
            got = getattr(s, col)
            if got not in {"—", "-"} and got != want:
                errors.append(f"reestr: {s.name} {col}={got}, NN={s.nn} bo'yicha {want} bo'lishi kerak")

        env_path = ROOT / "backend" / s.name / ".env.example"
        if not env_path.is_file():
            continue
        env = parse_env(env_path)
        rel = env_path.relative_to(ROOT)
        checks = {"SERVICE_NAME": s.name, "HOST_PORT": s.host_port}
        if s.pg_port not in {"—", "-"}:
            checks["POSTGRES_HOST_PORT"] = s.pg_port
        if s.redis_port not in {"—", "-"}:
            checks["REDIS_HOST_PORT"] = s.redis_port
        for key, want in checks.items():
            if env.get(key) != want:
                errors.append(f"{rel}: {key}={env.get(key)!r}, reestrda {want!r}")
        rabbit = env.get("RABBITMQ_URL", "")
        if rabbit and "@medbron-rabbitmq:5672/" not in rabbit:
            errors.append(f"{rel}: RABBITMQ_URL ichki manzil medbron-rabbitmq:5672 bo'lishi kerak")
        for key, value in env.items():
            if (
                SECRET_KEYS.search(key)
                and not NOT_SECRET.search(key)
                and value
                and not value.startswith(("change-me", "${", "<"))
            ):
                if key.endswith(("_PATH", "_ISSUER", "_AUDIENCE")):
                    continue
                errors.append(f"{rel}: {key} da qiymat bor — .env.example da sir bo'lmasligi kerak")
    if errors:
        for e in errors:
            print(f"XATO: {e}", file=sys.stderr)
        return 1
    present = sum((ROOT / "backend" / s.name).is_dir() for s in services.values())
    print(f"OK: reestr {len(services)} servis, portlar noyob; .env.example tekshirildi: {present} servis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
