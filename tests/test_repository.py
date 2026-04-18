"""Integration tests for the repository layer against a real in-memory SQLite DB."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base, Knowledge
from mnemo.repository import (
    knowledge_repository as kr,
    relation_repository as rr,
    search_repository as sr,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    # Use a shared-cache in-memory DB so FTS5 virtual table and main tables
    # share the same connection pool state across sessions if needed.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
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
    async with factory() as s:
        await s.execute(text("PRAGMA foreign_keys = ON"))
        yield s

    await engine.dispose()


async def test_create_and_get(session: AsyncSession) -> None:
    created = await kr.create(
        session,
        title="SQLite FTS5 基础",
        summary="全文检索入门",
        content="SQLite 的 FTS5 是一个全文检索虚拟表。",
        tags=["sqlite", "中文"],
    )
    assert created.id is not None
    assert created.title == "SQLite FTS5 基础"
    assert json.loads(created.tags) == ["sqlite", "中文"]
    assert created.scope == "global"

    fetched = await kr.get_by_id(session, created.id)
    assert fetched is not None
    assert fetched.title == created.title

    by_title = await kr.get_by_title(session, "SQLite FTS5 基础")
    assert by_title is not None
    assert by_title.id == created.id


async def test_create_normalizes_tags(session: AsyncSession) -> None:
    row = await kr.create(
        session,
        title="tag-normalize",
        summary="x",
        content="y",
        tags="a, b, c",
    )
    assert json.loads(row.tags) == ["a", "b", "c"]

    row2 = await kr.create(
        session,
        title="tag-empty",
        summary="x",
        content="y",
        tags=None,
    )
    assert row2.tags == "[]"


async def test_update_syncs_fts(session: AsyncSession) -> None:
    row = await kr.create(
        session,
        title="Original",
        summary="old summary",
        content="apple banana",
        tags=["fruit"],
    )

    # Search hits original content
    hits = await sr.fts_search(session, "apple")
    assert [h.id for h in hits] == [row.id]

    await kr.update(session, row.id, content="cherry durian", title="Renamed")
    refreshed = await kr.get_by_id(session, row.id)
    assert refreshed is not None
    assert refreshed.title == "Renamed"

    # Old term gone, new term hits
    assert await sr.fts_search(session, "apple") == []
    hits2 = await sr.fts_search(session, "cherry")
    assert [h.id for h in hits2] == [row.id]


async def test_update_rejects_unknown_field(session: AsyncSession) -> None:
    row = await kr.create(session, title="t", summary="s", content="c")
    with pytest.raises(ValueError):
        await kr.update(session, row.id, bogus="x")


async def test_update_missing_id_raises(session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await kr.update(session, 9999, title="nope")


async def test_delete_removes_everything(session: AsyncSession) -> None:
    a = await kr.create(session, title="A", summary="s", content="hello world")
    b = await kr.create(session, title="B", summary="s", content="linked node")
    await rr.create(session, source_id=a.id, target_id=b.id)

    ok = await kr.delete(session, a.id)
    assert ok is True

    assert await kr.get_by_id(session, a.id) is None
    # FTS row gone
    assert await sr.fts_search(session, "hello") == []
    # Relation gone
    backlinks = await rr.get_backlinks(session, b.id)
    assert backlinks == []

    # Deleting a missing id returns False
    assert await kr.delete(session, 12345) is False


async def test_list_all_scope_filter(session: AsyncSession) -> None:
    await kr.create(session, title="g1", summary="s", content="c", scope="global")
    await kr.create(
        session,
        title="p1",
        summary="s",
        content="c",
        scope="project",
        project_name="mnemo",
    )
    await kr.create(
        session,
        title="p2",
        summary="s",
        content="c",
        scope="project",
        project_name="other",
    )

    all_rows = await kr.list_all(session)
    assert len(all_rows) == 3

    project_rows = await kr.list_all(session, scope="project")
    assert {r.title for r in project_rows} == {"p1", "p2"}

    mnemo_rows = await kr.list_all(session, scope="project", project_name="mnemo")
    assert [r.title for r in mnemo_rows] == ["p1"]


async def test_fts_search_scope_filter(session: AsyncSession) -> None:
    await kr.create(session, title="G", summary="global note", content="pomelo", scope="global")
    await kr.create(
        session,
        title="P",
        summary="project note",
        content="pomelo",
        scope="project",
        project_name="mnemo",
    )

    all_hits = await sr.fts_search(session, "pomelo")
    assert {h.title for h in all_hits} == {"G", "P"}

    proj_hits = await sr.fts_search(session, "pomelo", scope="project")
    assert [h.title for h in proj_hits] == ["P"]

    mnemo_hits = await sr.fts_search(session, "pomelo", scope="project", project_name="mnemo")
    assert [h.title for h in mnemo_hits] == ["P"]


async def test_fts_search_empty_and_sanitize(session: AsyncSession) -> None:
    await kr.create(session, title="t", summary="s", content="hello world")

    assert await sr.fts_search(session, "") == []
    assert await sr.fts_search(session, "   ") == []
    # Punctuation should not blow up FTS parsing
    hits = await sr.fts_search(session, 'hello "world"!')
    assert len(hits) == 1


async def test_relation_get_related_depth(session: AsyncSession) -> None:
    # Graph: A -> B -> C ; A -> D
    a = await kr.create(session, title="A", summary="s", content="c")
    b = await kr.create(session, title="B", summary="s", content="c")
    c = await kr.create(session, title="C", summary="s", content="c")
    d = await kr.create(session, title="D", summary="s", content="c")

    await rr.create(session, source_id=a.id, target_id=b.id)
    await rr.create(session, source_id=b.id, target_id=c.id)
    await rr.create(session, source_id=a.id, target_id=d.id)

    depth1 = await rr.get_related(session, a.id, depth=1)
    assert {k.title for k in depth1} == {"B", "D"}

    depth2 = await rr.get_related(session, a.id, depth=2)
    assert {k.title for k in depth2} == {"B", "C", "D"}

    depth0 = await rr.get_related(session, a.id, depth=0)
    assert depth0 == []


async def test_relation_backlinks(session: AsyncSession) -> None:
    a = await kr.create(session, title="A", summary="s", content="c")
    b = await kr.create(session, title="B", summary="s", content="c")
    c = await kr.create(session, title="C", summary="s", content="c")

    await rr.create(session, source_id=a.id, target_id=c.id)
    await rr.create(session, source_id=b.id, target_id=c.id)

    backlinks = await rr.get_backlinks(session, c.id)
    assert {k.title for k in backlinks} == {"A", "B"}

    assert await rr.get_backlinks(session, a.id) == []


async def test_relation_self_link_rejected(session: AsyncSession) -> None:
    a = await kr.create(session, title="self", summary="s", content="c")
    with pytest.raises(ValueError):
        await rr.create(session, source_id=a.id, target_id=a.id)


async def test_relation_delete_by_knowledge(session: AsyncSession) -> None:
    a = await kr.create(session, title="A", summary="s", content="c")
    b = await kr.create(session, title="B", summary="s", content="c")
    c = await kr.create(session, title="C", summary="s", content="c")
    await rr.create(session, source_id=a.id, target_id=b.id)
    await rr.create(session, source_id=c.id, target_id=a.id)

    removed = await rr.delete_by_knowledge(session, a.id)
    assert removed == 2
    assert await rr.get_related(session, a.id) == []


async def test_list_tags_and_search_by_tag(session: AsyncSession) -> None:
    await kr.create(
        session, title="k1", summary="s", content="c", tags=["python", "db"]
    )
    await kr.create(
        session, title="k2", summary="s", content="c", tags=["python", "web"]
    )
    await kr.create(
        session,
        title="k3",
        summary="s",
        content="c",
        tags=["rust"],
        scope="project",
        project_name="mnemo",
    )

    all_tags = await sr.list_tags(session)
    assert all_tags == ["db", "python", "rust", "web"]

    global_tags = await sr.list_tags(session, scope="global")
    assert global_tags == ["db", "python", "web"]

    py_hits = await sr.search_by_tag(session, ["python"])
    assert {k.title for k in py_hits} == {"k1", "k2"}

    py_db = await sr.search_by_tag(session, ["python", "db"])
    assert [k.title for k in py_db] == ["k1"]

    # Requiring a tag not present returns []
    assert await sr.search_by_tag(session, ["nonexistent"]) == []
    assert await sr.search_by_tag(session, []) == []


async def test_get_by_title_scope_filter(session: AsyncSession) -> None:
    await kr.create(session, title="dup", summary="s", content="c", scope="global")
    # Same title not allowed due to unique constraint — verify filter still narrows
    found_global = await kr.get_by_title(session, "dup", scope="global")
    assert found_global is not None
    found_project = await kr.get_by_title(session, "dup", scope="project")
    assert found_project is None


async def test_fts_ranking_order(session: AsyncSession) -> None:
    # Default FTS5 tokenizer (unicode61) splits on whitespace/punctuation; use
    # latin tokens so matches are deterministic. Shorter/focused documents
    # rank higher under BM25.
    await kr.create(
        session,
        title="unrelated heading",
        summary="irrelevant",
        content=(
            "alpha beta gamma delta epsilon zeta eta theta iota kappa "
            "lambda mu nu xi omicron pi rho sigma tau upsilon phi chi "
            "psi omega target and more filler text around target."
        ),
    )
    top = await kr.create(
        session,
        title="target keyword",
        summary="target summary",
        content="target.",
    )

    hits = await sr.fts_search(session, "target")
    assert len(hits) == 2
    assert hits[0].id == top.id


async def test_knowledge_not_found(session: AsyncSession) -> None:
    assert await kr.get_by_id(session, 999) is None
    assert await kr.get_by_title(session, "missing") is None


async def test_session_scope_fields(session: AsyncSession) -> None:
    row = await kr.create(
        session,
        title="session-note",
        summary="s",
        content="c",
        scope="session",
        session_id="sess-123",
        source="repl",
    )
    assert row.scope == "session"
    assert row.session_id == "sess-123"
    assert row.source == "repl"


async def test_knowledge_model_repr(session: AsyncSession) -> None:
    row = await kr.create(session, title="repr", summary="s", content="c")
    assert isinstance(row, Knowledge)
    assert "repr" in repr(row)


async def test_list_titles_by_scope_basic(session: AsyncSession) -> None:
    g1 = await kr.create(session, title="global-a", summary="s", content="c", scope="global")
    g2 = await kr.create(session, title="global-b", summary="s", content="c", scope="global")
    await kr.create(session, title="session-a", summary="s", content="c", scope="session")

    rows = await kr.list_titles_by_scope(session, "global")
    ids = {r["id"] for r in rows}
    titles = {r["title"] for r in rows}
    assert ids == {g1.id, g2.id}
    assert titles == {"global-a", "global-b"}
    assert all(set(r.keys()) == {"id", "title"} for r in rows)


async def test_list_titles_by_scope_project_filter(session: AsyncSession) -> None:
    a = await kr.create(
        session, title="p1-a", summary="s", content="c",
        scope="project", project_name="proj1",
    )
    await kr.create(
        session, title="p2-a", summary="s", content="c",
        scope="project", project_name="proj2",
    )

    rows = await kr.list_titles_by_scope(session, "project", project_name="proj1")
    assert [r["id"] for r in rows] == [a.id]


async def test_list_titles_by_scope_excludes_superseded(session: AsyncSession) -> None:
    old = await kr.create(session, title="v1", summary="s", content="c", scope="global")
    _, new = await kr.supersede(session, old.id, title="v2", content="c2")

    rows = await kr.list_titles_by_scope(session, "global")
    ids = [r["id"] for r in rows]
    assert new.id in ids
    assert old.id not in ids


async def test_list_titles_by_scope_respects_limit(session: AsyncSession) -> None:
    for i in range(5):
        await kr.create(session, title=f"t-{i}", summary="s", content="c", scope="global")

    rows = await kr.list_titles_by_scope(session, "global", limit=3)
    assert len(rows) == 3
