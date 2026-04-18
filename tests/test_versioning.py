"""Integration tests for duplicate detection and immutable version chains.

Everything runs against a real per-test SQLite database — no mocks — so that
FTS5 wiring, unique constraints, and commit boundaries are all exercised as
they will be in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base
from mnemo.repository import (
    knowledge_repository as kr,
    relation_repository as rr,
    search_repository as sr,
)
from mnemo.services.knowledge_service import KnowledgeService


@pytest_asyncio.fixture
async def engine_and_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[object, async_sessionmaker[AsyncSession]]]:
    db_path = tmp_path / "mnemo.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def service(
    engine_and_factory,
) -> AsyncIterator[KnowledgeService]:
    _, factory = engine_and_factory
    yield KnowledgeService(session_factory=factory)


# ---------------------------------------------------------------------------
# update → version bump
# ---------------------------------------------------------------------------

async def test_update_creates_new_version_and_supersedes_old(
    service: KnowledgeService,
) -> None:
    created = await service.create_knowledge(
        title="Versioned", summary="s", content="first draft"
    )
    assert created["version"] == 1
    assert created["status"] == "active"

    updated = await service.update_knowledge(
        created["id"], content="second draft"
    )
    assert updated["id"] != created["id"]
    assert updated["version"] == 2
    assert updated["status"] == "active"
    assert updated["content"] == "second draft"
    assert updated["supersedes_id"] == created["id"]

    old = await service.get_knowledge(created["id"])
    assert old is not None
    assert old["status"] == "superseded"
    assert old["content"] == "first draft"
    assert old["version"] == 1
    assert old["superseded_by"] == {
        "id": updated["id"],
        "title": updated["title"],
    }


async def test_update_auto_creates_supersedes_relation(
    engine_and_factory,
) -> None:
    _, factory = engine_and_factory
    svc = KnowledgeService(session_factory=factory)

    created = await svc.create_knowledge(
        title="T", summary="s", content="v1"
    )
    updated = await svc.update_knowledge(created["id"], content="v2")

    async with factory() as session:
        successor = await rr.find_successor(session, created["id"])
        assert successor is not None
        assert successor.id == updated["id"]

        # Relation table should have exactly one supersedes edge new -> old
        supersedes_edges = await session.execute(
            text(
                "SELECT source_id, target_id FROM relation "
                "WHERE relation_type = 'supersedes'"
            )
        )
        edges = list(supersedes_edges.all())
        assert edges == [(updated["id"], created["id"])]


async def test_search_excludes_superseded_by_default(
    service: KnowledgeService,
) -> None:
    created = await service.create_knowledge(
        title="Searchable", summary="s", content="uniqueFTStoken"
    )
    await service.update_knowledge(
        created["id"], content="totallyDifferentToken"
    )

    old_hits = await service.search("uniqueFTStoken")
    assert old_hits == []

    new_hits = await service.search("totallyDifferentToken")
    assert len(new_hits) == 1
    assert new_hits[0]["status"] == "active"
    assert new_hits[0]["version"] == 2


async def test_list_knowledge_excludes_superseded(
    service: KnowledgeService,
) -> None:
    a = await service.create_knowledge(title="A", summary="s", content="c")
    await service.create_knowledge(title="B", summary="s", content="c")
    await service.update_knowledge(a["id"], content="c2")

    rows = await service.list_knowledge()
    # A@v1 is superseded, so only its new version + B show.
    assert {r["title"] for r in rows} == {"A", "B"}
    for r in rows:
        assert r["status"] == "active"


async def test_get_by_id_still_returns_superseded(
    service: KnowledgeService,
) -> None:
    created = await service.create_knowledge(
        title="Historic", summary="s", content="v1"
    )
    await service.update_knowledge(created["id"], content="v2")

    fetched = await service.get_knowledge(created["id"])
    assert fetched is not None
    assert fetched["status"] == "superseded"
    assert fetched["content"] == "v1"


async def test_get_by_title_returns_active_version_only(
    service: KnowledgeService,
) -> None:
    created = await service.create_knowledge(
        title="Doc", summary="s", content="v1"
    )
    updated = await service.update_knowledge(created["id"], content="v2")

    fetched = await service.get_knowledge("Doc")
    assert fetched is not None
    assert fetched["id"] == updated["id"]
    assert fetched["version"] == 2


# ---------------------------------------------------------------------------
# duplicate detection
# ---------------------------------------------------------------------------

async def test_duplicate_content_hash_warns_but_does_not_block(
    service: KnowledgeService,
) -> None:
    first = await service.create_knowledge(
        title="First", summary="s", content="identical payload"
    )
    assert "duplicate_warning" not in first

    second = await service.create_knowledge(
        title="Second", summary="s", content="identical payload"
    )
    # Both entries persist — agent may intentionally keep both.
    assert second["id"] != first["id"]
    assert second["duplicate_warning"] == {
        "id": first["id"],
        "title": "First",
    }

    # Distinct content → no warning.
    third = await service.create_knowledge(
        title="Third", summary="s", content="different payload"
    )
    assert "duplicate_warning" not in third


async def test_duplicate_warning_ignores_superseded_siblings(
    service: KnowledgeService,
) -> None:
    # Create then update so an inactive row with hash H exists.
    target = await service.create_knowledge(
        title="Stale", summary="s", content="payloadX"
    )
    await service.update_knowledge(target["id"], content="payloadY")

    # Another knowledge written with payloadX — the old superseded row shares
    # that hash but should not trigger a warning.
    fresh = await service.create_knowledge(
        title="Brand new", summary="s", content="payloadX"
    )
    assert "duplicate_warning" not in fresh


# ---------------------------------------------------------------------------
# same-title writes → version chain
# ---------------------------------------------------------------------------

async def test_same_title_write_triggers_version_chain(
    service: KnowledgeService,
) -> None:
    v1 = await service.create_knowledge(
        title="TitleChain", summary="s1", content="c1"
    )
    v2 = await service.create_knowledge(
        title="TitleChain", summary="s2", content="c2"
    )
    assert v2["id"] != v1["id"]
    assert v2["version"] == 2
    assert v2["supersedes_id"] == v1["id"]
    assert v2["summary"] == "s2"

    old = await service.get_knowledge(v1["id"])
    assert old is not None
    assert old["status"] == "superseded"

    active = await service.get_knowledge("TitleChain")
    assert active is not None
    assert active["id"] == v2["id"]


async def test_same_title_respects_scope(service: KnowledgeService) -> None:
    # Same title under different (scope, project) combos is not a version
    # bump — each is its own node.
    g = await service.create_knowledge(
        title="shared", summary="s", content="g", scope="global"
    )
    p = await service.create_knowledge(
        title="shared",
        summary="s",
        content="p",
        scope="project",
        project_name="mnemo",
    )
    assert p["id"] != g["id"]
    assert "supersedes_id" not in p

    # Meanwhile, writing "shared" into global again does supersede g.
    g2 = await service.create_knowledge(
        title="shared", summary="s", content="g2", scope="global"
    )
    assert g2["supersedes_id"] == g["id"]


# ---------------------------------------------------------------------------
# multi-step version chains
# ---------------------------------------------------------------------------

async def test_three_step_version_chain_is_fully_linked(
    service: KnowledgeService,
) -> None:
    v1 = await service.create_knowledge(
        title="Chain", summary="s", content="one"
    )
    v2 = await service.update_knowledge(v1["id"], content="two")
    v3 = await service.update_knowledge(v2["id"], content="three")

    assert v1["id"] != v2["id"] != v3["id"]
    assert v1["id"] != v3["id"]
    assert v3["version"] == 3
    assert v3["supersedes_id"] == v2["id"]
    assert v2["supersedes_id"] == v1["id"]

    # Only v3 is active.
    active = await service.get_knowledge("Chain")
    assert active is not None
    assert active["id"] == v3["id"]
    assert active["version"] == 3

    # All intermediate versions remain individually addressable.
    for old_id, expected_content in [
        (v1["id"], "one"),
        (v2["id"], "two"),
    ]:
        row = await service.get_knowledge(old_id)
        assert row is not None
        assert row["status"] == "superseded"
        assert row["content"] == expected_content


async def test_chain_successor_pointers_cover_every_hop(
    engine_and_factory,
) -> None:
    _, factory = engine_and_factory
    svc = KnowledgeService(session_factory=factory)

    v1 = await svc.create_knowledge(title="Hops", summary="s", content="a")
    v2 = await svc.update_knowledge(v1["id"], content="b")
    v3 = await svc.update_knowledge(v2["id"], content="c")

    async with factory() as session:
        # v1 -> v2
        nxt1 = await rr.find_successor(session, v1["id"])
        assert nxt1 is not None and nxt1.id == v2["id"]
        # v2 -> v3
        nxt2 = await rr.find_successor(session, v2["id"])
        assert nxt2 is not None and nxt2.id == v3["id"]
        # v3 has no successor yet
        nxt3 = await rr.find_successor(session, v3["id"])
        assert nxt3 is None


async def test_version_bump_preserves_manual_related_edges(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="Friend", summary="s", content="c")
    host = await service.create_knowledge(
        title="Host",
        summary="s",
        content="no wikilinks",
        related_titles=["Friend"],
    )
    assert host["related"] == ["Friend"]

    bumped = await service.update_knowledge(host["id"], summary="s2")
    # Manual edge survives the version bump.
    assert "Friend" in (bumped["related"] or [])


async def test_version_bump_refreshes_wikilinks(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="A", summary="s", content="c")
    await service.create_knowledge(title="B", summary="s", content="c")
    host = await service.create_knowledge(
        title="H", summary="s", content="see [[A]]"
    )
    assert host["related"] == ["A"]

    updated = await service.update_knowledge(
        host["id"], content="see [[B]] only"
    )
    assert updated["related"] == ["B"]


# ---------------------------------------------------------------------------
# low-level repository guarantees
# ---------------------------------------------------------------------------

async def test_repository_supersede_deletes_old_fts_row(
    engine_and_factory,
) -> None:
    _, factory = engine_and_factory
    async with factory() as session:
        row = await kr.create(
            session,
            title="FTSRow",
            summary="s",
            content="findMeByToken",
        )
        hits_before = await sr.fts_search(session, "findMeByToken")
        assert [h.id for h in hits_before] == [row.id]

        _, new = await kr.supersede(
            session, row.id, content="newbodyToken"
        )

        # Old token gone, new token hits only the new row.
        assert await sr.fts_search(session, "findMeByToken") == []
        new_hits = await sr.fts_search(session, "newbodyToken")
        assert [h.id for h in new_hits] == [new.id]


async def test_repository_find_duplicate_by_hash(engine_and_factory) -> None:
    _, factory = engine_and_factory
    async with factory() as session:
        a = await kr.create(session, title="A", summary="s", content="samebody")
        b = await kr.create(session, title="B", summary="s", content="samebody")

        # Look-up that excludes the new row should point back to A.
        dup = await kr.find_duplicate_by_hash(
            session, kr.compute_content_hash("samebody"), exclude_id=b.id
        )
        assert dup is not None and dup.id == a.id

        # Distinct content yields no duplicate.
        miss = await kr.find_duplicate_by_hash(
            session, kr.compute_content_hash("other")
        )
        assert miss is None


async def test_repository_supersede_rejects_missing_id(
    engine_and_factory,
) -> None:
    _, factory = engine_and_factory
    async with factory() as session:
        with pytest.raises(ValueError):
            await kr.supersede(session, 9999, content="x")


async def test_repository_supersede_rejects_unknown_field(
    engine_and_factory,
) -> None:
    _, factory = engine_and_factory
    async with factory() as session:
        row = await kr.create(
            session, title="t", summary="s", content="c"
        )
        with pytest.raises(ValueError):
            await kr.supersede(session, row.id, bogus="x")
