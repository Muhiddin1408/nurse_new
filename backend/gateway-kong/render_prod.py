#!/usr/bin/env python3
"""kong.dev.yml → kong.prod.yml (BLUEPRINT 6-bosqich). Yagona manba — dev fayl; prod farqlari shu yerda.

Farqlar:
- monolit marshrutlari olib tashlanadi (cutover tugagan — docs/runbooks/*-cutover.md);
- rate-limiting `policy: redis` (bir nechta Kong instansiyasi umumiy hisoblagich bilan);
- Payme/Click webhook'lari alohida route + `ip-restriction` (provayder IP'lari);
- CORS faqat prod domen(lar)i.

Majburiy env (biri yo'q bo'lsa — xato, fayl yozilmaydi):
  KONG_PUBLIC_ORIGINS   vergul bilan: https://medbron.uz,https://www.medbron.uz
  KONG_REDIS_HOST       rate-limit Redis
  PAYME_WEBHOOK_IPS     vergul bilan IP/CIDR (Payme kabinetidagi ro'yxat)
  CLICK_WEBHOOK_IPS     vergul bilan IP/CIDR (Click hujjatidagi ro'yxat)
Ixtiyoriy: KONG_REDIS_PORT (6379), KONG_REDIS_DATABASE (1). Redis paroli — Kong env vault: KONG_REDIS_PASSWORD.

Ishlatish:  python3 render_prod.py > kong.prod.yml   (kong.prod.yml git'ga kirmaydi — env'ga bog'liq)
"""

from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
WEBHOOKS = {
    "payme": ("PAYME_WEBHOOK_IPS", "~/v1/payments/payme/webhook$"),
    "click": ("CLICK_WEBHOOK_IPS", "~/v1/payments/click/(prepare|complete)$"),
}


def env_list(name: str, errors: list[str], *, ips: bool = False) -> list[str]:
    items = [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]
    if not items:
        errors.append(f"{name} bo'sh")
    for x in items if ips else []:
        try:
            ipaddress.ip_network(x, strict=False)
        except ValueError:
            errors.append(f"{name}: noto'g'ri IP/CIDR {x!r}")
    return items


def render(cfg: dict, errors: list[str]) -> dict:
    origins = env_list("KONG_PUBLIC_ORIGINS", errors)
    if any(not o.startswith("https://") for o in origins):
        errors.append("KONG_PUBLIC_ORIGINS faqat https:// bo'lishi kerak")
    redis_host = os.environ.get("KONG_REDIS_HOST", "")
    if not redis_host:
        errors.append("KONG_REDIS_HOST bo'sh")
    redis = {
        "host": redis_host,
        "port": int(os.environ.get("KONG_REDIS_PORT", "6379")),
        "database": int(os.environ.get("KONG_REDIS_DATABASE", "1")),
        "password": "{vault://env/kong-redis-password}",
    }

    cfg["services"] = [s for s in cfg["services"] if s["name"] != "monolith"]

    def harden(plugins: list[dict]) -> None:
        for p in plugins:
            if p["name"] == "rate-limiting":
                p["config"].update(policy="redis", redis=redis)

    for svc in cfg["services"]:
        harden(svc.get("plugins", []))
        for route in svc.get("routes", []):
            harden(route.get("plugins", []))
        if svc["name"] == "payment-service":
            routes = svc["routes"]
            hook = next(r for r in routes if r["name"] == "payment-webhooks")
            routes.remove(hook)
            for provider, (env, path) in WEBHOOKS.items():
                routes.insert(0, {
                    **{k: v for k, v in hook.items() if k not in ("name", "paths", "plugins")},
                    "name": f"payment-webhooks-{provider}",
                    "paths": [path],
                    "plugins": [
                        *hook.get("plugins", []),
                        {"name": "ip-restriction", "config": {"allow": env_list(env, errors, ips=True), "status": 403}},
                    ],
                })

    for p in cfg["plugins"]:
        if p["name"] == "cors":
            p["config"]["origins"] = origins
    return cfg


def main() -> int:
    cfg = yaml.safe_load((HERE / "kong.dev.yml").read_text())
    errors: list[str] = []
    out = render(cfg, errors)
    if errors:
        print("kong.prod.yml yaratilmadi:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    sys.stdout.write("# AVTOGENERATSIYA: render_prod.py (manba — kong.dev.yml). Qo'lda tahrirlamang.\n")
    yaml.safe_dump(out, sys.stdout, sort_keys=False, allow_unicode=True, width=160)
    return 0


if __name__ == "__main__":
    sys.exit(main())
