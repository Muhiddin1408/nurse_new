#!/usr/bin/env python3
"""Kontrakt tekshiruvi (BLUEPRINT 7.3, 8.6) — CI'da `contracts-check` bosqichi.

1. Katalog: nomlash, sxema fayllari mavjud va JSON Schema sifatida yaroqli, yetim sxema yo'q,
   publisher/consumer — reestrdagi servislar.
2. Reestr ↔ katalog: SERVICE_REGISTRY dagi Publishes/Consumes ustunlari katalogga mos.
3. Sinxron chaqiruvlar grafi: sikl yo'q (DAG), har servis ≤ 6 ta servisni chaqiradi.
4. Kod (backend/ da mavjud servislar uchun):
   - har `outbox.add_event(..., Events.X)` / `AddEvent(..., events.X)` — servis X ning publisher'i;
   - har `consumer.on(Events.X)` / `On(events.X)` — servis X ning consumer'i (katalogda);
   - katalogda consumer deb yozilgan, lekin kodda handler yo'q — xato ("publisher'siz consumer"ning teskarisi);
   - event nomi string bilan yozilmagan; servis kodida brokerga to'g'ridan-to'g'ri publish yo'q.

    python scripts/check-contracts.py           # hammasi
    python scripts/check-contracts.py --graph   # faqat graf
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _registry import ROOT, load_services  # noqa: E402

CONTRACTS = ROOT / "contracts" / "events"
BACKEND = ROOT / "backend"
NAME_RE = re.compile(r"^[a-z]+(_[a-z]+)*\.[a-z]+(_[a-z]+)*$")
MAX_SYNC_DEPS = 6

PY_PUBLISH = re.compile(r"add_event\(\s*[\w.]+\s*,\s*Events\.([A-Z0-9_]+)")
PY_CONSUME = re.compile(r"\.on\(\s*Events\.([A-Z0-9_]+)")
PY_RAW = re.compile(r"(add_event\(\s*[\w.]+\s*,\s*[\"'])|(\.on\(\s*[\"'][a-z_]+\.[a-z_]+)")
PY_DIRECT = re.compile(r"\bEventPublisher\(|\.publish\(\s*(build_envelope|envelope)")
GO_PUBLISH = re.compile(r"AddEvent\([^,]+,[^,]+,\s*events\.([A-Za-z0-9]+)")
GO_CONSUME = re.compile(r"\.(?:MustOn|On)\(\s*events\.([A-Za-z0-9]+)")
GO_RAW = re.compile(r"(AddEvent\([^,]+,[^,]+,\s*\")|(\.(?:MustOn|On)\(\s*\"[a-z_]+\.)")
GO_DIRECT = re.compile(r"\bNewPublisher\(")

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def publishers(spec: dict) -> list[str]:
    return list(spec.get("publishers") or [spec["publisher"]])


def check_catalog(catalog: dict, services: set[str]) -> None:
    referenced = set()
    for et, spec in catalog.items():
        if not NAME_RE.match(et):
            err(f"katalog: noto'g'ri nom {et!r}")
        schema_file = CONTRACTS / spec["schema"]
        referenced.add(spec["schema"])
        if spec["schema"] != f"{et}.v{spec['version']}.json":
            err(f"katalog: {et} sxema nomi {spec['schema']} — kutilgan {et}.v{spec['version']}.json")
        if not schema_file.is_file():
            err(f"katalog: {et} sxemasi yo'q: {spec['schema']}")
        else:
            try:
                schema = json.loads(schema_file.read_text())
                Draft202012Validator.check_schema(schema)
                if schema.get("properties", {}).get("event_type", {}).get("const") != et:
                    err(f"{spec['schema']}: properties.event_type.const != {et}")
            except Exception as exc:
                err(f"{spec['schema']}: yaroqsiz JSON Schema: {exc}")
        for svc in publishers(spec) + list(spec["consumers"]):
            if svc not in services:
                err(f"katalog: {et} — reestrda yo'q servis {svc!r}")
        if not spec["consumers"]:
            err(f"katalog: {et} ning consumer'i yo'q — keraksiz event")
    orphans = {f.name for f in CONTRACTS.glob("*.json")} - referenced - {"_envelope.v1.json"}
    for orphan in sorted(orphans):
        err(f"contracts/events/{orphan}: katalogda yo'q (yetim sxema)")


def check_registry_vs_catalog(catalog: dict, services: dict) -> None:
    pub_by: dict[str, set[str]] = defaultdict(set)
    con_by: dict[str, set[str]] = defaultdict(set)
    for et, spec in catalog.items():
        for p in publishers(spec):
            if et != "audit.recorded":
                pub_by[p].add(et)
        for c in spec["consumers"]:
            con_by[c].add(et)
    for name, svc in services.items():
        declared = svc.events_in(svc.publishes_cell)
        if declared is not None and declared != pub_by[name]:
            err(f"reestr: {name} Publishes katalogga mos emas: "
                f"ortiqcha={sorted(declared - pub_by[name])} yetishmaydi={sorted(pub_by[name] - declared)}")
        declared = svc.events_in(svc.consumes_cell)
        if declared is not None and declared != con_by[name]:
            err(f"reestr: {name} Consumes katalogga mos emas: "
                f"ortiqcha={sorted(declared - con_by[name])} yetishmaydi={sorted(con_by[name] - declared)}")


def check_graph(services: dict) -> None:
    graph = {name: svc.calls for name, svc in services.items()}
    for name, deps in graph.items():
        if len(deps) > MAX_SYNC_DEPS:
            err(f"graf: {name} {len(deps)} ta servisni sinxron chaqiradi (limit {MAX_SYNC_DEPS})")
        if name in deps:
            err(f"graf: {name} o'zini chaqiradi")
    state: dict[str, int] = {}  # 1 — stekda, 2 — tugagan

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 1:
            cycle = path[path.index(node):] + [node]
            err("graf: SIKL " + " → ".join(cycle))
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep, [*path, node])
        state[node] = 2

    for node in graph:
        visit(node, [])


def _source_files(svc_dir: Path, suffix: str) -> list[Path]:
    skip = {"tests", "migrations", ".venv", "vendor"}
    return [
        f for f in svc_dir.rglob(f"*{suffix}")
        if not skip.intersection(f.relative_to(svc_dir).parts) and not f.name.endswith("_test.go")
    ]


def strip_py_docs(text: str) -> str:
    """Izohlar va docstring'lar olib tashlanadi: ulardagi namunalar (`Events.X_HAPPENED`) kod emas.
    Oddiy string literallar QOLADI — xom event nomini (`"booking.created"`) ushlash uchun kerak."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            doc_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.start[0] in doc_lines:
            tok = tok._replace(string='""')
        kept.append(tok)
    return tokenize.untokenize(kept)


def check_code(catalog: dict, services: dict) -> int:
    py_names = {et.replace(".", "_").upper(): et for et in catalog}
    go_names = {"".join(p.capitalize() for p in re.split(r"[._]", et)): et for et in catalog}
    checked = 0
    for name in services:
        svc_dir = BACKEND / name
        if not svc_dir.is_dir():
            continue
        checked += 1
        published: set[str] = set()
        consumed: set[str] = set()
        for f in _source_files(svc_dir, ".py"):
            text = strip_py_docs(f.read_text())
            rel = f.relative_to(ROOT)
            published |= {py_names.get(m, f"?{m}") for m in PY_PUBLISH.findall(text)}
            consumed |= {py_names.get(m, f"?{m}") for m in PY_CONSUME.findall(text)}
            if PY_RAW.search(text):
                err(f"{rel}: event nomi string bilan — Events.* konstantasidan foydalaning")
            if PY_DIRECT.search(text) and "outbox_relay" not in f.name:
                err(f"{rel}: brokerga to'g'ridan-to'g'ri publish taqiqlangan — outbox.add_event() ishlating")
        for f in _source_files(svc_dir, ".go"):
            text = f.read_text()
            rel = f.relative_to(ROOT)
            published |= {go_names.get(m, f"?{m}") for m in GO_PUBLISH.findall(text)}
            consumed |= {go_names.get(m, f"?{m}") for m in GO_CONSUME.findall(text)}
            if GO_RAW.search(text):
                err(f"{rel}: event nomi string bilan — events.* konstantasidan foydalaning")
            if GO_DIRECT.search(text) and "relay" not in str(rel):
                err(f"{rel}: brokerga to'g'ridan-to'g'ri publish taqiqlangan — outbox.AddEvent() ishlating")
        for et in sorted(published):
            if et.startswith("?"):
                err(f"{name}: noma'lum event konstantasi {et[1:]}")
            elif name not in publishers(catalog[et]):
                err(f"{name}: {et} ni publish qiladi, lekin katalogda publisher emas")
        for et in sorted(consumed):
            if et.startswith("?"):
                err(f"{name}: noma'lum event konstantasi {et[1:]}")
            elif name not in catalog[et]["consumers"]:
                err(f"{name}: {et} ga handler bor, lekin katalogda consumer emas (publisher'siz consumer xavfi)")
        expected = {et for et, spec in catalog.items() if name in spec["consumers"]}
        if name == "analytics-service":
            continue  # `#` ga bind qiladi, handler umumiy
        for et in sorted(expected - consumed):
            err(f"{name}: katalogda {et} consumer'i, lekin kodda handler yo'q")
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", action="store_true", help="faqat sinxron graf tekshiruvi")
    args = parser.parse_args()
    services = load_services()
    if args.graph:
        check_graph(services)
    else:
        catalog = yaml.safe_load((CONTRACTS / "catalog.yaml").read_text())
        check_catalog(catalog, set(services))
        check_registry_vs_catalog(catalog, services)
        check_graph(services)
        checked = check_code(catalog, services)
    if errors:
        for e in errors:
            print(f"XATO: {e}", file=sys.stderr)
        print(f"\n{len(errors)} ta xato", file=sys.stderr)
        return 1
    if args.graph:
        print(f"OK: graf DAG, {len(services)} servis")
    else:
        print(f"OK: {len(catalog)} event, {len(services)} servis, graf DAG, kod tekshirildi: {checked} servis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
