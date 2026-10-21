from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from medbron_common.db import transaction

from .conftest import Note

pytestmark = pytest.mark.integration


class KeepIt(Exception):
    pass


async def test_transaction_commits_on_listed_errors(session_factory) -> None:
    kept, dropped, ok = (f"tx-{uuid.uuid4().hex[:8]}" for _ in range(3))
    async with session_factory() as session:
        with pytest.raises(KeepIt):
            async with transaction(session, commit_on=(KeepIt,)):
                session.add(Note(id=kept))
                await session.flush()
                raise KeepIt()
        with pytest.raises(ValueError):
            async with transaction(session, commit_on=(KeepIt,)):
                session.add(Note(id=dropped))
                await session.flush()
                raise ValueError("rollback")
        async with transaction(session):
            session.add(Note(id=ok))
    async with session_factory() as session:
        ids = set((await session.scalars(select(Note.id).where(Note.id.in_([kept, dropped, ok])))).all())
    assert ids == {kept, ok}
