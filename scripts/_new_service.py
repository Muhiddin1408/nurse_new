"""new-service.sh ning ishchi qismi: shablonni nusxalash va placeholder'larni to'ldirish.

Placeholder'lar (shablon fayllari va yo'llarida):
  __SERVICE_NAME__ booking-service    __SERVICE_SLUG__ booking_service    __SHORT__ booking
  __NN__ 09   __HOST_PORT__ 8109   __PG_PORT__ 5409   __REDIS_PORT__ 6309
  __ALLOWED_CALLERS__ / __GO_ALLOWED_CALLERS__  — sinxron grafdan: kim bu servisni chaqiradi
  __INBOUND_TOKENS__  — har chaqiruvchi uchun INTERNAL_TOKEN_<CALLER>=change-me
  __PUBLISHES__ __CONSUMES__ __CALLS__ __CALLERS__  — README uchun (katalog/reestrdan)

Ixtiyoriy bloklar (`# >>> <nom>` ... `# <<< <nom>`): reestrda Redis yo'q (`—`) bo'lsa `redis`,
katalogda servis hech narsa iste'mol qilmasa `consumer`, hech narsa publish qilmasa (audit ham) `relay`
bloki olib tashlanadi; aks holda faqat marker qatorlari o'chadi.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _registry import ROOT, load_services  # noqa: E402

TEMPLATES = ROOT / "service-templates"
SKIP_PARTS = {"__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache", ".venv", "bin", "uv.lock", "go.sum"}
TEXT_SUFFIXES = {".py", ".go", ".mod", ".toml", ".ini", ".yml", ".yaml", ".md", ".sql", ".mako", ".example", ""}


_BLOCK = re.compile(r"^# >>> (?P<name>[a-z]+)\n(?P<body>.*?)^# <<< (?P=name)\n", re.S | re.M)


def strip_blocks(text: str, keep: dict[str, bool]) -> str:
    return _BLOCK.sub(lambda m: m.group("body") if keep.get(m.group("name"), True) else "", text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--dest", type=Path)
    parser.add_argument("--lang", choices=["py", "go"])
    args = parser.parse_args()

    services = load_services()
    svc = services.get(args.name)
    if svc is None:
        print(f"XATO: {args.name} docs/SERVICE_REGISTRY.md da yo'q — avval reestrga qo'shing", file=sys.stderr)
        return 1
    lang = args.lang or ("go" if svc.lang.lower().startswith("go") else "py")
    template = TEMPLATES / ("go-service" if lang == "go" else "python-service")
    dest = args.dest or ROOT / "backend" / svc.name
    if dest.exists():
        print(f"XATO: {dest} allaqachon mavjud", file=sys.stderr)
        return 1

    catalog = yaml.safe_load((ROOT / "contracts" / "events" / "catalog.yaml").read_text())
    publishes = sorted(et for et, s in catalog.items()
                       if svc.name in (s.get("publishers") or [s.get("publisher")]) and et != "audit.recorded")
    consumes = sorted(et for et, s in catalog.items() if svc.name in s["consumers"])
    callers = sorted(name for name, other in services.items() if svc.name in other.calls)

    def fmt(items: list[str]) -> str:
        return ", ".join(f"`{i}`" for i in items) if items else "—"

    replacements = {
        "__SERVICE_NAME__": svc.name,
        "__SERVICE_SLUG__": svc.name.replace("-", "_"),
        "__SHORT__": svc.short,
        "__NN__": svc.nn,
        "__HOST_PORT__": svc.host_port,
        "__PG_PORT__": svc.pg_port,
        "__REDIS_PORT__": svc.redis_port,
        # To'liq tuple ifodasi (qavslar bilan) — formatter qavslarni olib tashlasa ham bo'sh holat `()` bo'ladi.
        "__ALLOWED_CALLERS__": "(" + "".join(f'"{c}", ' for c in callers).rstrip(" ") + ")",
        "__GO_ALLOWED_CALLERS__": ", ".join(f'"{c}"' for c in callers),
        "__INBOUND_TOKENS__": "\n".join(
            f"INTERNAL_TOKEN_{c.upper().replace('-', '_')}=change-me" for c in callers
        ) or "# (hech kim — bu servisning internal API'si yopiq)",
        "__PUBLISHES__": fmt(publishes),
        "__CONSUMES__": fmt(consumes),
        "__CALLS__": fmt(svc.calls),
        "__CALLERS__": fmt(callers),
    }

    publishes_any = any(svc.name in (s.get("publishers") or [s.get("publisher")]) for s in catalog.values())
    keep = {"redis": svc.redis_port not in {"—", "-"}, "consumer": bool(consumes), "relay": publishes_any}

    def substitute(text: str) -> str:
        text = strip_blocks(text, keep)
        for key in sorted(replacements, key=len, reverse=True):
            text = text.replace(key, replacements[key])
        return text

    for src in sorted(template.rglob("*")):
        if SKIP_PARTS.intersection(src.relative_to(template).parts):
            continue
        rel = Path(substitute(str(src.relative_to(template))))
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT_SUFFIXES or src.name.startswith("."):
            target.write_text(substitute(src.read_text()))
        else:
            shutil.copy2(src, target)
        target.chmod(src.stat().st_mode)

    if dest.resolve() == (ROOT / "backend" / svc.name).resolve():
        workflow = ROOT / ".github" / "workflows" / f"svc-{svc.name}.yml"
        if not workflow.exists():
            text = (ROOT / "ci-templates" / "service-workflow.yml").read_text()
            text = text.replace("__LIB_PATH__", "libs/go-common" if lang == "go" else "libs/py-common")
            text = text.replace("__REUSABLE__", "_service-go.yml" if lang == "go" else "_service-python.yml")
            workflow.write_text(substitute(text))
            print(f"  CI: {workflow.relative_to(ROOT)}")

    leftovers = [str(p) for p in dest.rglob("*") if p.is_file() and "__" in p.read_text(errors="ignore")
                 and any(k in p.read_text(errors="ignore") for k in replacements)]
    if leftovers:
        print(f"XATO: to'ldirilmagan placeholder: {leftovers}", file=sys.stderr)
        return 1
    print(f"{svc.name} ({lang}) → {dest}")
    print(f"  publish: {len(publishes)}, consume: {len(consumes)}, chaqiradi: {svc.calls or '—'}, chaqiruvchilar: {callers or '—'}")
    print(lang)
    return 0


if __name__ == "__main__":
    sys.exit(main())
