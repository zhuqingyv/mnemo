"""Integration tests for KnowledgeService, wikilink parser, and session
lifecycle — all against a real SQLite database in a per-test tmp directory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.markdown.parser import extract_wikilinks
from mnemo.models.knowledge import Base
from mnemo.services.knowledge_service import KnowledgeService


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    """Build a KnowledgeService backed by a fresh file-based SQLite DB.

    We avoid the module-level engine cache in mnemo.db by constructing our own
    engine + session factory and injecting it. This keeps each test fully
    isolated without having to monkey-patch global state.
    """
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
        yield KnowledgeService(session_factory=factory)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# markdown parser
# ---------------------------------------------------------------------------

def test_extract_wikilinks_basic() -> None:
    text_ = "see [[foo]] and [[bar]] for details"
    assert extract_wikilinks(text_) == ["foo", "bar"]


def test_extract_wikilinks_dedupes_and_preserves_order() -> None:
    text_ = "[[alpha]] [[beta]] [[alpha]] [[gamma]] [[beta]]"
    assert extract_wikilinks(text_) == ["alpha", "beta", "gamma"]


def test_extract_wikilinks_alias_is_ignored() -> None:
    text_ = "read [[real-target|display text]] now"
    assert extract_wikilinks(text_) == ["real-target"]


def test_extract_wikilinks_trims_whitespace() -> None:
    assert extract_wikilinks("[[  spaced  ]]") == ["spaced"]


def test_extract_wikilinks_skips_empty() -> None:
    assert extract_wikilinks("[[]] [[   ]] [[ok]]") == ["ok"]


def test_extract_wikilinks_empty_input() -> None:
    assert extract_wikilinks("") == []
    assert extract_wikilinks(None) == []


def test_extract_wikilinks_rejects_multiline_target() -> None:
    # A newline inside the [[...]] must break the match so multi-line text
    # doesn't accidentally turn into a wikilink target.
    assert extract_wikilinks("[[foo\nbar]]") == []


# ---------------------------------------------------------------------------
# service: create + wikilink auto-relation
# ---------------------------------------------------------------------------

async def test_create_knowledge_returns_dict(service: KnowledgeService) -> None:
    result = await service.create_knowledge(
        title="SQLite FTS",
        summary="note on fts",
        content="content body",
        tags=["sqlite", "search"],
    )
    assert result["id"] is not None
    assert result["title"] == "SQLite FTS"
    assert result["tags"] == ["sqlite", "search"]
    assert result["scope"] == "global"
    assert result["related"] == []
    assert "created_at" in result and result["created_at"]


async def test_create_knowledge_auto_links_existing_wikilink_targets(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="Python", summary="s", content="c")
    await service.create_knowledge(title="Rust", summary="s", content="c")

    created = await service.create_knowledge(
        title="Comparison",
        summary="compare two languages",
        content="see [[Python]] and [[Rust]] and also [[Missing]]",
    )
    # Missing target is skipped; existing ones become relations
    assert sorted(created["related"]) == ["Python", "Rust"]


async def test_create_knowledge_wikilink_to_self_skipped(
    service: KnowledgeService,
) -> None:
    created = await service.create_knowledge(
        title="Selfie",
        summary="s",
        content="I mention [[Selfie]] in myself",
    )
    assert created["related"] == []


async def test_create_knowledge_accepts_related_titles(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="A", summary="s", content="c")
    await service.create_knowledge(title="B", summary="s", content="c")

    created = await service.create_knowledge(
        title="C",
        summary="s",
        content="no wikilinks here",
        related_titles=["A", "B", "Missing"],
    )
    assert sorted(created["related"]) == ["A", "B"]


async def test_create_merges_wikilink_and_related_titles_without_dup(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="X", summary="s", content="c")

    created = await service.create_knowledge(
        title="Y",
        summary="s",
        content="mentions [[X]]",
        related_titles=["X"],  # same target as wikilink
    )
    # Both wikilink and manual relation created but returned list is de-duped
    assert created["related"] == ["X"]


# ---------------------------------------------------------------------------
# service: get / search / related
# ---------------------------------------------------------------------------

async def test_get_knowledge_by_id_and_title(service: KnowledgeService) -> None:
    created = await service.create_knowledge(
        title="K1", summary="s", content="c"
    )
    by_id = await service.get_knowledge(created["id"])
    by_title = await service.get_knowledge("K1")
    assert by_id is not None and by_title is not None
    assert by_id["id"] == by_title["id"] == created["id"]
    assert by_id["related"] == []


async def test_get_knowledge_missing_returns_none(
    service: KnowledgeService,
) -> None:
    assert await service.get_knowledge(9999) is None
    assert await service.get_knowledge("no-such-title") is None


async def test_get_knowledge_includes_related_titles(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="A", summary="s", content="c")
    created = await service.create_knowledge(
        title="B", summary="s", content="links [[A]]"
    )
    fetched = await service.get_knowledge(created["id"])
    assert fetched is not None
    assert fetched["related"] == ["A"]


async def test_search_returns_summary_dicts(service: KnowledgeService) -> None:
    await service.create_knowledge(
        title="pineapple", summary="a fruit", content="yellow"
    )
    await service.create_knowledge(
        title="banana", summary="a fruit", content="yellow fruit body"
    )
    hits = await service.search("yellow")
    assert len(hits) == 2
    for h in hits:
        assert "content" not in h  # summary dict is trimmed
        assert {"id", "title", "summary", "tags", "scope"} <= set(h.keys())


async def test_search_hybrid_degrades_to_fts_without_embedding(
    service: KnowledgeService,
) -> None:
    """Default mode is ``hybrid``; with no EmbeddingService it must fall back
    to FTS results rather than erroring, and log a hybrid_degraded event."""
    from sqlalchemy import select
    from mnemo.models.knowledge import KnowledgeEvent

    await service.create_knowledge(
        title="alpha", summary="s", content="carrot apple banana"
    )
    hits = await service.search("carrot")
    assert [h["title"] for h in hits] == ["alpha"]

    async with service._session_factory() as session:
        events = (
            await session.execute(
                select(KnowledgeEvent).where(
                    KnowledgeEvent.event_type == "hybrid_degraded"
                )
            )
        ).scalars().all()
    assert len(events) >= 1


async def test_search_rejects_unknown_mode(service: KnowledgeService) -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError):
        await service.search("anything", mode="bogus")


async def test_search_fts_mode_bypasses_rrf(service: KnowledgeService) -> None:
    """Explicit mode='fts' must return raw summary dicts (no rrf_score)."""
    await service.create_knowledge(
        title="alpha", summary="s", content="carrot apple"
    )
    hits = await service.search("carrot", mode="fts")
    assert len(hits) == 1
    assert "rrf_score" not in hits[0]


async def test_search_hybrid_fuses_fts_and_vector(tmp_path) -> None:
    """With a stub EmbeddingService + real vec0 table, hybrid must call
    rrf_fuse and return rrf_score-annotated entries."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from tests.test_vector_search import _build_engine, StubEmbedding

    db_path = tmp_path / "hybrid.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = KnowledgeService(session_factory=factory, embedding_service=StubEmbedding())

    await svc.create_knowledge(title="dog", summary="s", content="bark bark")
    await svc.create_knowledge(title="cat", summary="s", content="meow bark")

    hits = await svc.search("bark")
    assert len(hits) >= 1
    assert all("rrf_score" in h for h in hits)
    assert all("source" in h for h in hits)
    assert all(h["source"] in {"both", "fts_only", "vec_only"} for h in hits)
    scores = [h["rrf_score"] for h in hits]
    assert scores == sorted(scores, reverse=True)

    await engine.dispose()


async def test_search_hybrid_fts_miss_strict_vector_threshold(tmp_path) -> None:
    """方案 E + M3b + M4：FTS 零命中时走向量通道但用严阈值 0.60（M4 任务 #3 从 0.55 调）。
    - FTS miss + 向量命中（distance ≤ 0.60）→ 返回向量结果（口语/跨语义 query 救回）
    - FTS miss + 向量不命中（distance > 0.60）→ 返回空（OOD query 拦住）
    - M3b 附加：纯向量路径 Top-1 ``final`` 低于 ``vec_only_min_final`` 也会返空（
      本测试为 apple 补一条 refines 入边把 authority 抬起来，保持原用例意图）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from tests.test_vector_search import _build_engine, EMBEDDING_DIM, StubEmbedding
    from mnemo.repository import authority_repository as ar
    from mnemo.repository import relation_repository as rr

    db_path = tmp_path / "fts_miss.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # apple 存向量 = one_hot(0)；query "zebra" stub 输出 one_hot(5)
    # cosine_distance(one_hot(0), one_hot(5)) = 1.0 > 0.60 → 向量通道也空 → 返回空
    zebra_vec = [0.0] * EMBEDDING_DIM
    zebra_vec[5] = 1.0
    # ref_source: 辅助节点，向量走不同正交维度，保证不被 "close" 召回
    ref_source_vec = [0.0] * EMBEDDING_DIM
    ref_source_vec[9] = 1.0
    stub_miss = StubEmbedding(
        mapping={"zebra": zebra_vec, "ref_source": ref_source_vec}
    )
    svc_miss = KnowledgeService(session_factory=factory, embedding_service=stub_miss)
    apple = await svc_miss.create_knowledge(title="apple", summary="s", content="fruit red")
    ref = await svc_miss.create_knowledge(title="ref_source", summary="s", content="x")

    # M3b 前置：给 apple 一条 refines 入边并写入 authority_score，模拟 M3a 回填状态。
    async with factory() as s:
        await rr.create(s, source_id=ref["id"], target_id=apple["id"], relation_type="refines")
        await ar.recompute_and_store_authority(s, apple["id"])
        await s.commit()

    hits = await svc_miss.search("zebra")
    assert hits == []

    # 同一 DB 上换 query：stub_hit 让 "close" 的向量 = apple 的向量 → cosine_distance=0
    # FTS 不命中（"close" 在 apple 里没出现）但向量严阈值命中 → 返回 apple
    stub_hit = StubEmbedding(mapping={"close": [1.0] + [0.0] * (EMBEDDING_DIM - 1)})
    svc_hit = KnowledgeService(session_factory=factory, embedding_service=stub_hit)
    hits2 = await svc_hit.search("close")
    assert len(hits2) == 1
    assert hits2[0]["title"] == "apple"

    await engine.dispose()


async def test_search_scope_filter(service: KnowledgeService) -> None:
    await service.create_knowledge(
        title="G", summary="s", content="unique-word", scope="global"
    )
    await service.create_knowledge(
        title="P",
        summary="s",
        content="unique-word",
        scope="project",
        project_name="mnemo",
    )
    all_hits = await service.search("unique-word")
    assert {h["title"] for h in all_hits} == {"G", "P"}

    proj_hits = await service.search("unique-word", scope="project")
    assert [h["title"] for h in proj_hits] == ["P"]

    mnemo_hits = await service.search(
        "unique-word", scope="project", project_name="mnemo"
    )
    assert [h["title"] for h in mnemo_hits] == ["P"]


async def test_get_related_depth_traversal(service: KnowledgeService) -> None:
    # Graph: A -> B -> C ; A -> D
    await service.create_knowledge(title="A", summary="s", content="c")
    await service.create_knowledge(title="B", summary="s", content="links [[A]]")
    await service.create_knowledge(title="C", summary="s", content="links [[B]]")
    await service.create_knowledge(title="D", summary="s", content="links [[A]]")

    depth1 = await service.get_related("A", depth=1)
    assert {k["title"] for k in depth1} == {"B", "D"}

    depth2 = await service.get_related("A", depth=2)
    assert {k["title"] for k in depth2} == {"B", "C", "D"}


async def test_get_related_missing(service: KnowledgeService) -> None:
    assert await service.get_related("nope") == []


# ---------------------------------------------------------------------------
# service: update (wikilink refresh)
# ---------------------------------------------------------------------------

async def test_update_refreshes_wikilinks(service: KnowledgeService) -> None:
    await service.create_knowledge(title="A", summary="s", content="c")
    await service.create_knowledge(title="B", summary="s", content="c")
    await service.create_knowledge(title="C", summary="s", content="c")

    host = await service.create_knowledge(
        title="Host", summary="s", content="links [[A]] and [[B]]"
    )
    assert sorted(host["related"]) == ["A", "B"]

    updated = await service.update_knowledge(
        host["id"], content="now only [[C]]"
    )
    # update now bumps the version, so the returned id differs from the old one.
    assert updated["id"] != host["id"]
    assert updated["related"] == ["C"]

    fetched = await service.get_knowledge(updated["id"])
    assert fetched is not None
    assert fetched["related"] == ["C"]


async def test_update_without_content_preserves_wikilinks(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="A", summary="s", content="c")
    host = await service.create_knowledge(
        title="Host", summary="old summary", content="see [[A]]"
    )
    updated = await service.update_knowledge(
        host["id"], summary="new summary"
    )
    assert updated["summary"] == "new summary"
    assert updated["related"] == ["A"]


async def test_update_preserves_manual_related_when_content_changes(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(title="A", summary="s", content="c")
    await service.create_knowledge(title="B", summary="s", content="c")

    host = await service.create_knowledge(
        title="Host",
        summary="s",
        content="mentions [[A]]",
        related_titles=["B"],  # manual edge of type "related"
    )
    assert sorted(host["related"]) == ["A", "B"]

    # Content change drops wikilink to A, but manual edge to B must survive
    updated = await service.update_knowledge(
        host["id"], content="no more links"
    )
    assert updated["related"] == ["B"]


async def test_update_title_and_tags(service: KnowledgeService) -> None:
    created = await service.create_knowledge(
        title="Old", summary="s", content="c", tags=["x"]
    )
    updated = await service.update_knowledge(
        created["id"], title="New", tags=["y", "z"]
    )
    assert updated["title"] == "New"
    assert updated["tags"] == ["y", "z"]


# ---------------------------------------------------------------------------
# service: delete
# ---------------------------------------------------------------------------

async def test_delete_knowledge(service: KnowledgeService) -> None:
    created = await service.create_knowledge(
        title="Doomed", summary="s", content="c"
    )
    assert await service.delete_knowledge(created["id"]) is True
    assert await service.get_knowledge(created["id"]) is None
    assert await service.delete_knowledge(created["id"]) is False


async def test_delete_removes_relations(service: KnowledgeService) -> None:
    await service.create_knowledge(title="Target", summary="s", content="c")
    host = await service.create_knowledge(
        title="Host", summary="s", content="mentions [[Target]]"
    )
    assert host["related"] == ["Target"]

    assert await service.delete_knowledge(host["id"]) is True
    # Target survives and its graph is clean
    target = await service.get_knowledge("Target")
    assert target is not None
    assert target["related"] == []


# ---------------------------------------------------------------------------
# service: tags
# ---------------------------------------------------------------------------

async def test_list_tags_global_and_scope(service: KnowledgeService) -> None:
    await service.create_knowledge(
        title="k1", summary="s", content="c", tags=["python", "db"]
    )
    await service.create_knowledge(
        title="k2",
        summary="s",
        content="c",
        tags=["rust"],
        scope="project",
        project_name="mnemo",
    )

    assert await service.list_tags() == ["db", "python", "rust"]
    assert await service.list_tags(scope="global") == ["db", "python"]
    assert await service.list_tags(scope="project") == ["rust"]


async def test_search_by_tag_returns_summaries(service: KnowledgeService) -> None:
    await service.create_knowledge(
        title="a", summary="s", content="c", tags=["t1", "t2"]
    )
    await service.create_knowledge(
        title="b", summary="s", content="c", tags=["t1"]
    )

    hits = await service.search_by_tag(["t1"])
    assert {h["title"] for h in hits} == {"a", "b"}
    for h in hits:
        assert "content" not in h

    both = await service.search_by_tag(["t1", "t2"])
    assert [h["title"] for h in both] == ["a"]

    assert await service.search_by_tag([]) == []


# ---------------------------------------------------------------------------
# service: list_knowledge
# ---------------------------------------------------------------------------

async def test_list_knowledge_pagination(service: KnowledgeService) -> None:
    for i in range(5):
        await service.create_knowledge(
            title=f"k{i}", summary="s", content="c"
        )

    page = await service.list_knowledge(limit=2, offset=0)
    assert len(page) == 2
    page2 = await service.list_knowledge(limit=2, offset=2)
    assert len(page2) == 2
    assert {p["title"] for p in page}.isdisjoint({p["title"] for p in page2})


# ---------------------------------------------------------------------------
# service: error paths
# ---------------------------------------------------------------------------

async def test_update_missing_id_raises(service: KnowledgeService) -> None:
    with pytest.raises(ValueError):
        await service.update_knowledge(9999, title="x")
