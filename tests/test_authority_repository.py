"""Integration tests for authority_repository against an in-memory SQLite DB."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base, Knowledge, Relation
from mnemo.repository import authority_repository as ar


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _mk(session: AsyncSession, title: str) -> Knowledge:
    k = Knowledge(title=title, summary=title, content=title, tags="[]")
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


async def _rel(
    session: AsyncSession,
    src: int,
    tgt: int,
    rtype: str,
) -> None:
    session.add(Relation(source_id=src, target_id=tgt, relation_type=rtype))
    await session.commit()


async def test_incoming_counts_only_typed(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    d = await _mk(session, "D")
    # A receives 2 supersedes, 1 refines, 1 derived_from — and 3 noise
    await _rel(session, b.id, a.id, "supersedes")
    await _rel(session, c.id, a.id, "supersedes")
    await _rel(session, d.id, a.id, "refines")
    await _rel(session, b.id, a.id, "derived_from")
    await _rel(session, c.id, a.id, "related")  # noise
    await _rel(session, d.id, a.id, "wikilink")  # noise
    await _rel(session, b.id, a.id, "depends_on")  # noise (not in formula)

    counts = await ar.incoming_counts_by_type(session, a.id)
    assert counts == {"supersedes": 2, "refines": 1, "derived_from": 1}


async def test_batch_incoming_counts(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    await _rel(session, b.id, a.id, "supersedes")
    await _rel(session, c.id, a.id, "refines")
    await _rel(session, a.id, b.id, "refines")

    batch = await ar.batch_incoming_counts(session, [a.id, b.id, c.id])
    assert batch[a.id] == {"supersedes": 1, "refines": 1}
    assert batch[b.id] == {"refines": 1}
    assert batch[c.id] == {}


async def test_batch_empty_input(session: AsyncSession) -> None:
    assert await ar.batch_incoming_counts(session, []) == {}
    assert await ar.batch_contradiction_flags(session, []) == {}
    assert await ar.batch_stored_authority(session, []) == {}


async def test_contradiction_flag_both_directions(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    d = await _mk(session, "D")
    await _rel(session, a.id, b.id, "contradicts")  # a is source
    await _rel(session, d.id, c.id, "contradicts")  # c is target

    assert await ar.has_contradiction(session, a.id) is True
    assert await ar.has_contradiction(session, b.id) is True
    assert await ar.has_contradiction(session, c.id) is True
    assert await ar.has_contradiction(session, d.id) is True


async def test_contradiction_false_when_only_other_types(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    await _rel(session, a.id, b.id, "related")
    await _rel(session, a.id, b.id, "refines")
    assert await ar.has_contradiction(session, a.id) is False
    assert await ar.has_contradiction(session, b.id) is False


async def test_batch_contradiction_flags(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    await _rel(session, a.id, b.id, "contradicts")

    flags = await ar.batch_contradiction_flags(session, [a.id, b.id, c.id])
    assert flags == {a.id: True, b.id: True, c.id: False}


async def test_recompute_and_store_authority(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    await _rel(session, b.id, a.id, "refines")
    # one refines -> log(1 + 1.5) = log(2.5)
    score = await ar.recompute_and_store_authority(session, a.id)
    await session.commit()
    assert math.isclose(score, math.log(2.5))

    read = await ar.get_stored_authority(session, a.id)
    assert read is not None
    assert math.isclose(read, math.log(2.5))


async def test_recompute_updates_existing_meta_row(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    await _rel(session, b.id, a.id, "refines")
    await ar.recompute_and_store_authority(session, a.id)
    await session.commit()

    # Add one more refines — should update, not duplicate
    await _rel(session, c.id, a.id, "refines")
    new_score = await ar.recompute_and_store_authority(session, a.id)
    await session.commit()
    # log(1 + 3.0) = log(4.0)
    assert math.isclose(new_score, math.log(4.0))

    # Only one meta row should exist
    from mnemo.models.knowledge import KnowledgeMeta
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(KnowledgeMeta).where(
                KnowledgeMeta.knowledge_id == a.id,
                KnowledgeMeta.key == ar.AUTHORITY_META_KEY,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_get_stored_authority_missing_returns_none(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    assert await ar.get_stored_authority(session, a.id) is None


async def test_batch_stored_authority(session: AsyncSession) -> None:
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    d = await _mk(session, "D")
    await _rel(session, b.id, a.id, "refines")
    await _rel(session, c.id, b.id, "supersedes")
    await ar.recompute_and_store_authority(session, a.id)
    await ar.recompute_and_store_authority(session, b.id)
    await session.commit()

    batch = await ar.batch_stored_authority(session, [a.id, b.id, c.id, d.id])
    assert set(batch.keys()) == {a.id, b.id}
    assert math.isclose(batch[a.id], math.log(2.5))
    assert math.isclose(batch[b.id], math.log(3.0))  # log(1 + 2*1)


async def test_batch_authority_and_contradiction(session: AsyncSession) -> None:
    """M4 task #5 combined batch — must return same values as the two separate
    calls, in one round-trip."""
    a = await _mk(session, "A")
    b = await _mk(session, "B")
    c = await _mk(session, "C")
    d = await _mk(session, "D")
    await _rel(session, b.id, a.id, "refines")  # a gets authority
    await _rel(session, a.id, c.id, "contradicts")  # a and c get contradiction
    await ar.recompute_and_store_authority(session, a.id)
    await session.commit()

    ids = [a.id, b.id, c.id, d.id]
    auth, contra = await ar.batch_authority_and_contradiction(session, ids)

    # authority matches batch_stored_authority exactly
    solo_auth = await ar.batch_stored_authority(session, ids)
    assert auth == solo_auth

    # contradiction matches batch_contradiction_flags exactly
    solo_contra = await ar.batch_contradiction_flags(session, ids)
    assert contra == solo_contra

    # Sanity: a and c flagged, b and d not
    assert contra[a.id] is True
    assert contra[c.id] is True
    assert contra[b.id] is False
    assert contra[d.id] is False


async def test_batch_authority_and_contradiction_empty(session: AsyncSession) -> None:
    auth, contra = await ar.batch_authority_and_contradiction(session, [])
    assert auth == {}
    assert contra == {}
