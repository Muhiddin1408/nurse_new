#!/usr/bin/env python3
"""LOKAL/DEV uchun `backend/<servis>/.env` ni `.env.example` dan yaratadi (mavjud `.env` ga tegmaydi).

    python scripts/dev-env.py                      # backend/ dagi barcha servislar
    python scripts/dev-env.py auth-service user-service
    python scripts/dev-env.py --force auth-service # qayta yaratish (parollar o'zgaradi — bazani ham qayta yarating!)

- `change-me` / bo'sh sirlar — tasodifiy (`secrets.token_hex`).
- RabbitMQ va S3 kalitlari — `backend/infra/.env` dan (bir xil bo'lishi shart).
- Internal tokenlar sinxron grafga mos: har servisning bitta chiquvchi tokeni (`INTERNAL_TOKEN`),
  qabul qiluvchida `INTERNAL_TOKEN_<CHAQIRUVCHI>` = chaqiruvchining tokeni. Tokenlar
  `secrets/dev-internal-tokens.json` da saqlanadi — keyin qo'shilgan servislar ham mos keladi.
- Lokal qulayliklar: auth — `OTP_DEBUG_ECHO=true` (SMS console), `ENVIRONMENT=local`; payment — tasodifiy
  Payme/Click kalitlari (e2e webhook'larni imzolaydi).

PROD UCHUN EMAS: prod sirlari serverda / vault'da yaratiladi.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from base64 import urlsafe_b64encode
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _registry import ROOT, load_services  # noqa: E402

BACKEND = ROOT / "backend"
TOKENS = ROOT / "secrets" / "dev-internal-tokens.json"
LINE = re.compile(r"^(?P<key>[A-Z0-9_]+)=(?P<value>.*)$")
GENERATED = {
    "POSTGRES_PASSWORD", "REDIS_PASSWORD", "OTP_HASH_SECRET", "TELEGRAM_LINK_SECRET", "TELEGRAM_WEBHOOK_SECRET",
    # Lokal provayder "kabineti" — e2e webhook imzolarini shu kalitlar bilan quradi (scripts/e2e.sh eksport qiladi)
    "PAYME_SECRET_KEY", "CLICK_SECRET_KEY",
}  # fmt: skip
LOCAL_OVERRIDES = {
    # Lokal e2e bitta IP'dan ko'p kiradi; prod qiymati .env.example da (20/soat)
    "auth-service": {"OTP_DEBUG_ECHO": "true", "SMS_PROVIDER": "console", "OTP_MAX_PER_IP_HOUR": "1000"},
    "notification-service": {"SMS_PROVIDER": "console", "PUSH_PROVIDER": "console"},
    # Click Merchant API (reversal) lokal bo'sh — refundlar `manual_required` ga tushadi (tashqi tarmoq yo'q)
    "payment-service": {"PAYME_MERCHANT_ID": "dev-merchant", "CLICK_SERVICE_ID": "777", "CLICK_MERCHANT_ID": "42"},
}


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text().splitlines():
            m = LINE.match(raw.strip())
            if m:
                out[m["key"]] = m["value"]
    return out


def load_tokens() -> dict[str, str]:
    return json.loads(TOKENS.read_text()) if TOKENS.is_file() else {}


def save_tokens(tokens: dict[str, str]) -> None:
    TOKENS.parent.mkdir(parents=True, exist_ok=True)
    TOKENS.write_text(json.dumps(tokens, indent=2, sort_keys=True) + "\n")
    TOKENS.chmod(0o600)


def render(service: str, example: str, infra: dict[str, str], tokens: dict[str, str]) -> str:
    out = []
    for raw in example.splitlines():
        m = LINE.match(raw)
        if not m:
            out.append(raw)
            continue
        key, value = m["key"], m["value"]
        if key == "RABBITMQ_USER":
            value = infra.get("RABBITMQ_USER", value)
        elif key == "RABBITMQ_PASSWORD":
            value = infra["RABBITMQ_PASSWORD"]
        elif key == "S3_ACCESS_KEY":
            value = infra.get("S3_FILE_SERVICE_ACCESS_KEY", value)
        elif key == "S3_SECRET_KEY":
            value = infra["S3_FILE_SERVICE_SECRET_KEY"]
        elif key == "INTERNAL_TOKEN":
            value = tokens.setdefault(service, secrets.token_hex(24))
        elif key.startswith("INTERNAL_TOKEN_"):
            caller = key.removeprefix("INTERNAL_TOKEN_").lower().replace("_", "-")
            value = tokens.setdefault(caller, secrets.token_hex(24))
        elif key == "TOTP_ENCRYPTION_KEY":
            value = urlsafe_b64encode(secrets.token_bytes(32)).decode()
        elif key in GENERATED or value == "change-me":
            value = secrets.token_hex(24)
        elif key == "DATABASE_URL" and "change-me" in raw:
            continue  # konteynerda DATABASE_URL_DOCKER; hostdan — qo'lda
        value = LOCAL_OVERRIDES.get(service, {}).get(key, value)
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("services", nargs="*")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    infra = read_env(BACKEND / "infra" / ".env")
    for required in ("RABBITMQ_PASSWORD", "S3_FILE_SERVICE_SECRET_KEY"):
        if not infra.get(required):
            print(f"XATO: backend/infra/.env da {required} yo'q (avval infra'ni sozlang)", file=sys.stderr)
            return 1
    known = load_services()
    targets = args.services or sorted(p.parent.name for p in BACKEND.glob("*/.env.example") if p.parent.name in known)
    tokens = load_tokens()
    for service in targets:
        example = BACKEND / service / ".env.example"
        target = BACKEND / service / ".env"
        if not example.is_file():
            print(f"o'tkazildi: {service} (.env.example yo'q)")
            continue
        if target.exists() and not args.force:
            print(f"mavjud, tegilmadi: {target.relative_to(ROOT)}")
            continue
        target.write_text(render(service, example.read_text(), infra, tokens))
        target.chmod(0o600)
        print(f"yaratildi: {target.relative_to(ROOT)}")
    save_tokens(tokens)
    return 0


if __name__ == "__main__":
    sys.exit(main())
