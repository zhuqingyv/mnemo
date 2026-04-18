"""Phase 5b fine-grained keyword auto-edge tests (M1 scope).

Covers:
  1. Keyword extraction basics (jieba + filters in tokenizer).
  2. Two keyword-sharing entries auto-create an ``auto_related`` edge.
  3. Newly created auto edges start at weight ``0.3``.
  4. ``fine_edge_enabled=False`` falls back to the Phase 4 vector-only path.
  5. An existing manual ``related`` edge is not overwritten by ``auto_related``.

Real SQLite + sqlite-vec + a deterministic StubEmbedding (no Ollama). The
write path runs through ``KnowledgeService.create_knowledge`` end-to-end so
the test catches regressions in both the service-layer hookup and the
repository contract.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
import sqlite_vec
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Base, Relation
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services.knowledge_service import KnowledgeService


EMBEDDING_DIM = VECTOR_DIM


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _pad(prefix: list[float]) -> list[float]:
    return _unit(list(prefix) + [0.0] * (EMBEDDING_DIM - len(prefix)))


class StubEmbedding(EmbeddingService):
    """Deterministic title→vector mapping — bypasses Ollama."""

    def __init__(self, mapping: dict[str, list[float]]):
        super().__init__(config=MnemoConfig(_env_file=None))  # type: ignore[call-arg]
        self._mapping = mapping

    def prepare_text(
        self, title: str, summary: str | None = None, content: str | None = None
    ) -> str:
        # Keying on the title alone keeps the test vectors legible; the rest
        # of the row (summary/content) drives the FTS channel only.
        return title

    async def embed(self, text: str) -> list[float]:  # type: ignore[override]
        if text in self._mapping:
            return self._mapping[text]
        # Any unmapped input gets a distant orthogonal vector so rows that
        # weren't designed to match never accidentally do.
        return _pad([0.0, 0.0, 0.0, 1.0])

    async def embed_batch(
        self, texts: list[str], batch_size: int = 64
    ):  # type: ignore[override]
        return [await self.embed(t) for t in texts]

    async def warmup(self) -> bool:  # type: ignore[override]
        self.ready = True
        return True


def _load_sqlite_vec_sync(dbapi_conn, _record) -> None:
    aiosqlite_conn = getattr(dbapi_conn, "_connection", None)
    if aiosqlite_conn is None:
        dbapi_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_conn)
        finally:
            dbapi_conn.enable_load_extension(False)
        return

    def _do_load(sync_conn):
        sync_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(sync_conn)
        finally:
            sync_conn.enable_load_extension(False)

    dbapi_conn.await_(aiosqlite_conn._execute(_do_load, aiosqlite_conn._conn))


async def _build_engine(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    event.listen(engine.sync_engine, "connect", _load_sqlite_vec_sync)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, "
                "knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(
            text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec_idx "
                f"USING vec0(knowledge_id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{EMBEDDING_DIM}])"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    return engine


async def _edges_from(
    factory, source_id: int, relation_type: str | None = None
) -> list[Relation]:
    async with factory() as session:
        stmt = select(Relation).where(Relation.source_id == source_id)
        if relation_type is not None:
            stmt = stmt.where(Relation.relation_type == relation_type)
        return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# 1. Keyword extraction
# ---------------------------------------------------------------------------


def test_extract_keywords_basic_filters():
    """Short words, pure digits, and stopwords drop out; jieba keeps the rest."""
    from mnemo.utils.tokenizer import extract_keywords_for_edge

    text = (
        "响应式编程 使用 React useEffect 实现状态管理。 "
        "what how 2026 这个 SQLite FTS5 基础知识图谱"
    )
    kws = extract_keywords_for_edge(text, top_n=20)

    # We should see multi-char Chinese/English content words.
    assert len(kws) >= 3, kws
    # Stopwords filtered out (tokenizer dropped these).
    for bad in ("how", "what", "这个"):
        assert bad.lower() not in {k.lower() for k in kws}, (bad, kws)
    # Pure digit tokens dropped.
    assert "2026" not in kws, kws
    # Single-char tokens dropped.
    for k in kws:
        assert len(k) >= 2, k


def test_extract_keywords_top_n_cap():
    """Requesting top_n caps the returned list even when jieba finds many."""
    from mnemo.utils.tokenizer import extract_keywords_for_edge

    # Repeated high-signal terms so jieba definitely returns >5 candidates.
    text = (
        "SQLite 向量 检索 嵌入 语义 搜索 知识图谱 自动 建边 关键词 "
        "jieba 中文分词 TF IDF 测试 数据模型 边 权重 反馈 机制"
    )
    out = extract_keywords_for_edge(text, top_n=5)
    assert len(out) == 5, out


# ---------------------------------------------------------------------------
# 2. Fixtures for service-level tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def service_and_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[KnowledgeService, StubEmbedding, Any, MnemoConfig]]:
    db_path = tmp_path / "mnemo.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Near-identical vectors for the two keyword-sharing rows (cos ≈ 0.99)
    # so whole_floor=0.3 is comfortably satisfied. C is orthogonal so it
    # never matches even though its title is distinct.
    mapping: dict[str, list[float]] = {
        "响应式编程入门":       _pad([1.0]),
        "响应式编程实战":       _pad([0.99, 0.141]),
        "无关主题":             _pad([0.0, 0.0, 1.0]),
    }
    stub = StubEmbedding(mapping)

    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    service = KnowledgeService(
        session_factory=factory, embedding_service=stub, config=config
    )
    try:
        yield service, stub, factory, config
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 3. Two keyword-sharing entries auto-build an ``auto_related`` edge
# ---------------------------------------------------------------------------


async def test_auto_link_v2_creates_auto_related_with_initial_weight(
    service_and_factory,
) -> None:
    service, _stub, factory, _cfg = service_and_factory

    first = await service.create_knowledge(
        title="响应式编程入门",
        summary="介绍响应式编程范式",
        content=(
            "响应式编程是一种声明式编程范式，核心概念包括数据流、"
            "变更传播、订阅者模式。本文使用 RxJS 为例讲解基本原理，"
            "是入门响应式编程的基础知识。"
        ),
    )
    second = await service.create_knowledge(
        title="响应式编程实战",
        summary="响应式编程在 Web 项目中的实战",
        content=(
            "本节讲响应式编程如何用在前端。响应式编程让组件订阅"
            "状态流，React useEffect 可以和 RxJS 观察者整合，"
            "实现细粒度的响应式数据更新。"
        ),
    )

    auto_edges = await _edges_from(factory, second["id"], "auto_related")
    assert len(auto_edges) >= 1, (
        f"expected ≥1 auto_related edge from second row, got {auto_edges}"
    )
    target_ids = {e.target_id for e in auto_edges}
    assert first["id"] in target_ids, (
        "auto_related should point at the keyword-sharing row"
    )

    # Weight is the Phase 5b initial constant, not the cosine similarity.
    edge_to_first = next(e for e in auto_edges if e.target_id == first["id"])
    assert edge_to_first.weight == 0.3, (
        f"initial auto_related weight must be 0.3, got {edge_to_first.weight}"
    )

    # extra_json records the keyword trigger + counters for the M2 feedback loop.
    assert edge_to_first.extra_json, "auto_related edge must carry extra_json"
    payload = json.loads(edge_to_first.extra_json)
    assert payload["kw_match_type"] == "exact"
    assert payload["helpful_count"] == 0
    assert payload["misleading_count"] == 0
    assert payload["created_by"] == "auto_link_v2"
    assert isinstance(payload.get("kw_source"), str) and payload["kw_source"]

    # No Phase-4 ``related`` auto-edge should have been created under the
    # fine-edge path — the new row reached every target via auto_related.
    legacy_related = [
        e
        for e in await _edges_from(factory, second["id"], "related")
        if e.target_id != first["id"]  # defensive; shouldn't exist at all
    ]
    assert legacy_related == [], (
        f"fine_edge path must not emit legacy 'related' auto-edges, got {legacy_related}"
    )


# ---------------------------------------------------------------------------
# 4. fine_edge_enabled=False → Phase 4 fallback
# ---------------------------------------------------------------------------


async def test_fine_edge_disabled_falls_back_to_vector_path(
    service_and_factory,
) -> None:
    service, _stub, factory, cfg = service_and_factory
    cfg.fine_edge_enabled = False

    await service.create_knowledge(
        title="响应式编程入门",
        summary="介绍响应式编程范式",
        content="响应式编程范式长文本超过写门槛五十字符长度的内容。" * 2,
    )
    second = await service.create_knowledge(
        title="响应式编程实战",
        summary="响应式编程实战",
        content="响应式编程在前端的实战长文本超过五十字符长度的内容。" * 2,
    )

    auto_edges = await _edges_from(factory, second["id"], "auto_related")
    assert auto_edges == [], (
        f"fine_edge_enabled=False must NOT create auto_related edges, got {auto_edges}"
    )

    # Legacy path builds ``related`` edges weighted by cosine (~0.99 here).
    legacy = await _edges_from(factory, second["id"], "related")
    assert len(legacy) >= 1, (
        "legacy _auto_link_by_vector should still link near-identical vectors"
    )
    weight = next(e.weight for e in legacy)
    assert 0.9 <= weight <= 1.0, (
        f"legacy edge weight should be cosine (~0.99), got {weight}"
    )


# ---------------------------------------------------------------------------
# 5. A pre-existing manual ``related`` edge is preserved, not overwritten
# ---------------------------------------------------------------------------


async def test_existing_manual_related_is_not_overwritten(
    service_and_factory,
) -> None:
    service, _stub, factory, _cfg = service_and_factory

    first = await service.create_knowledge(
        title="响应式编程入门",
        summary="介绍响应式编程范式",
        content=(
            "响应式编程是一种声明式编程范式，核心概念包括数据流、"
            "变更传播、订阅者模式。"
        ),
    )
    # Agent declares the second row as manually ``related`` to the first.
    second = await service.create_knowledge(
        title="响应式编程实战",
        summary="响应式编程实战",
        content=(
            "本节讲响应式编程如何用在前端。组件订阅状态流，React "
            "useEffect 和 RxJS 观察者整合。"
        ),
        related_titles=[first["title"]],
    )

    all_edges = await _edges_from(factory, second["id"])
    to_first = [e for e in all_edges if e.target_id == first["id"]]
    assert len(to_first) == 1, (
        f"exactly one edge should exist between the pair, got {to_first}"
    )
    assert to_first[0].relation_type == "related", (
        f"manual related edge must win over auto_related, got "
        f"{to_first[0].relation_type!r}"
    )
    assert to_first[0].weight == 1.0, (
        f"manual related weight is the fixed 1.0, got {to_first[0].weight}"
    )
