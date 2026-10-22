"""scripts/check-*.py xatolarni haqiqatan ushlashini isbotlaydi (salbiy testlar).

    pip install -r scripts/requirements.txt pytest && pytest scripts/tests
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.errors.clear()
    return module


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo'ning minimal nusxasi: docs/SERVICE_REGISTRY.md + contracts/ + bo'sh backend/."""
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / "docs" / "SERVICE_REGISTRY.md", tmp_path / "docs" / "SERVICE_REGISTRY.md")
    (tmp_path / "backend").mkdir()
    import _registry

    monkeypatch.setattr(_registry, "ROOT", tmp_path)
    monkeypatch.setattr(_registry, "REGISTRY", tmp_path / "docs" / "SERVICE_REGISTRY.md")
    return tmp_path


def run_contracts(root: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[int, list[str]]:
    mod = load("check-contracts")
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "CONTRACTS", root / "contracts" / "events")
    monkeypatch.setattr(mod, "BACKEND", root / "backend")
    monkeypatch.setattr(sys, "argv", ["check-contracts", *argv])
    return mod.main(), list(mod.errors)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def test_real_repo_passes(fake_root, monkeypatch) -> None:
    code, errs = run_contracts(fake_root, monkeypatch)
    assert code == 0, errs


def test_sync_cycle_detected(fake_root, monkeypatch) -> None:
    # catalog → file mavjud; file → catalog qo'shsak sikl.
    edit(fake_root / "docs/SERVICE_REGISTRY.md",
         "| 04 | file-service | Py | 8080 | 8104 | 5404 | — | — | — | — |",
         "| 04 | file-service | Py | 8080 | 8104 | 5404 | — | — | — | catalog |")
    code, errs = run_contracts(fake_root, monkeypatch, "--graph")
    assert code == 1
    assert any("SIKL" in e and "file-service" in e and "catalog-service" in e for e in errs)


def test_too_many_sync_deps(fake_root, monkeypatch) -> None:
    edit(fake_root / "docs/SERVICE_REGISTRY.md", "| catalog, schedule, pricing, user, admin |",
         "| catalog, schedule, pricing, user, admin, file, notification |")
    code, errs = run_contracts(fake_root, monkeypatch, "--graph")
    assert code == 1 and any("limit 6" in e for e in errs)


def test_registry_catalog_mismatch(fake_root, monkeypatch) -> None:
    edit(fake_root / "docs/SERVICE_REGISTRY.md", "`review.submitted`, `review.published`,", "`review.published`,")
    code, errs = run_contracts(fake_root, monkeypatch)
    assert code == 1 and any("review-service Publishes" in e and "review.submitted" in e for e in errs)


def test_unknown_service_and_orphan_schema(fake_root, monkeypatch) -> None:
    edit(fake_root / "contracts/events/catalog.yaml",
         "consumers: [auth-service, booking-service, notification-service, pricing-service, analytics-service]",
         "consumers: [auth-service, booking-service, notification-service, pricing-service, analytics-service, ghost-service]")
    (fake_root / "contracts/events/rating.new.v1.json").write_text("{}")
    code, errs = run_contracts(fake_root, monkeypatch)
    assert code == 1
    assert any("ghost-service" in e for e in errs)
    assert any("rating.new.v1.json" in e and "yetim" in e for e in errs)


def make_service(root: Path, name: str, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / "backend" / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def test_code_checks(fake_root, monkeypatch) -> None:
    make_service(fake_root, "review-service", {
        "app/services/reviews.py": (
            "outbox.add_event(session, Events.REVIEW_PUBLISHED, aggregate_id=1, data={})\n"
            "outbox.add_event(session, Events.BOOKING_CONFIRMED, aggregate_id=1, data={})\n"  # publisher emas
            "outbox.add_event(session, \"review.rejected\", aggregate_id=1, data={})\n"  # string
        ),
        "app/workers/handlers.py": (
            "@consumer.on(Events.BOOKING_COMPLETED)\n"
            "@consumer.on(Events.USER_REGISTERED)\n"  # katalogda consumer emas
            # admin.settings_changed handler YO'Q
        ),
        "app/services/bad.py": "pub = EventPublisher(url)\n",
    })
    code, errs = run_contracts(fake_root, monkeypatch)
    assert code == 1
    joined = "\n".join(errs)
    assert "booking.confirmed ni publish qiladi, lekin katalogda publisher emas" in joined
    assert "string bilan" in joined
    assert "user.registered ga handler bor, lekin katalogda consumer emas" in joined
    assert "admin.settings_changed consumer'i, lekin kodda handler yo'q" in joined
    assert "to'g'ridan-to'g'ri publish taqiqlangan" in joined


def test_docstring_examples_are_not_code(fake_root, monkeypatch) -> None:
    make_service(fake_root, "review-service", {
        "app/events.py": (
            '"""Namuna:\n    outbox.add_event(session, Events.X_HAPPENED, aggregate_id=1, data={})\n"""\n'
            "# consumer.on(Events.BOOKING_CONFIRMED) — izoh, kod emas\n"
            "x = 1\n"
        ),
        "app/workers/handlers.py": (
            "def register(consumer):\n"
            "    @consumer.on(Events.BOOKING_COMPLETED)\n"
            "    async def a(s, e): ...\n"
            "    @consumer.on(Events.ADMIN_SETTINGS_CHANGED)\n"
            "    async def b(s, e): ...\n"
        ),
    })
    code, errs = run_contracts(fake_root, monkeypatch)
    joined = "\n".join(errs)
    assert "X_HAPPENED" not in joined and "booking.confirmed" not in joined, joined


def test_go_code_checks(fake_root, monkeypatch) -> None:
    make_service(fake_root, "schedule-service", {
        "internal/service/slots.go": 'ob.AddEvent(ctx, tx, events.BookingCreated, id, data)\n',
        "internal/eventbus/handlers.go": "c.MustOn(events.DoctorApproved, h)\n",
    })
    code, errs = run_contracts(fake_root, monkeypatch)
    joined = "\n".join(errs)
    assert "schedule-service: booking.created ni publish qiladi" in joined
    assert "schedule-service: katalogda booking.cancelled consumer'i, lekin kodda handler yo'q" in joined


def test_registry_port_checks(fake_root, monkeypatch) -> None:
    edit(fake_root / "docs/SERVICE_REGISTRY.md", "| 8112 | 5412 | 6312 |", "| 8111 | 5412 | 6312 |")
    make_service(fake_root, "auth-service", {".env.example": (
        "SERVICE_NAME=auth-service\nHOST_PORT=8199\nPOSTGRES_HOST_PORT=5401\nREDIS_HOST_PORT=6301\n"
        "RABBITMQ_URL=amqp://u:p@localhost:5673/\nPOSTGRES_PASSWORD=supersecret\n"
    )})
    mod = load("check-registry")
    monkeypatch.setattr(mod, "ROOT", fake_root)
    assert mod.main() == 1
    joined = "\n".join(mod.errors)
    assert "host_port=8111 2 marta" in joined
    assert "review-service host_port=8111" in joined
    assert "HOST_PORT='8199'" in joined
    assert "RABBITMQ_URL ichki manzil" in joined
    assert "POSTGRES_PASSWORD da qiymat bor" in joined
