"""Auto-edge by vector similarity — create_knowledge attaches ``related``
edges to the top-K nearest neighbors above ``auto_link_threshold``.

Uses StubEmbedding (same pattern as test_vector_search.py) so no Ollama is
needed and vectors are deterministic. Real SQLite with sqlite-vec loaded —
per project red-line, no mocks.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
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
    """Deterministic ``text -> vector`` map — bypasses Ollama."""

    def __init__(self, mapping: dict[str, list[float]]):
        super().__init__(config=MnemoConfig())
        self._mapping = mapping

    def prepare_text(
        self, title: str, summary: str | None = None, content: str | None = None
    ) -> str:
        return title

    async def embed(self, text: str) -> list[float]:  # type: ignore[override]
        if text not in self._mapping:
            # Any unmapped text gets a distant vector so we never accidentally
            # match two rows that weren't intended to be similar.
            return _pad([0.0, 0.0, 0.0, 1.0])
        return self._mapping[text]

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


async def _count_related_edges(
    factory, source_id: int, relation_type: str = "related"
) -> list[tuple[int, float, str]]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Relation.target_id, Relation.weight, Relation.relation_type
                ).where(
                    Relation.source_id == source_id,
                    Relation.relation_type == relation_type,
                )
            )
        ).all()
    return [(r[0], r[1], r[2]) for r in rows]


@pytest_asyncio.fixture
async def service_and_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[KnowledgeService, StubEmbedding, Any, MnemoConfig]]:
    db_path = tmp_path / "mnemo.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Two pairs of vectors: (A, A2) are nearly identical (cos ≈ 0.99); C is
    # orthogonal to both (cos = 0.0). A third pair (A, B) shares one
    # dimension, cos ≈ 0.707 — above default 0.7 threshold but just barely.
    mapping: dict[str, list[float]] = {
        "A":  _pad([1.0]),
        "A2": _pad([0.99, 0.141]),   # cos(A, A2) ≈ 0.99
        "B":  _pad([0.707, 0.707]),  # cos(A, B)  ≈ 0.707
        "C":  _pad([0.0, 0.0, 1.0]), # cos(A, C)  = 0.0
    }
    stub = StubEmbedding(mapping)

    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    # Phase 5b's _auto_link_v2 is the default path. This fixture targets the
    # Phase 4 legacy vector-only auto-linker (_auto_link_by_vector); the
    # fine-grained keyword path has its own coverage in test_fine_edge.py.
    config.fine_edge_enabled = False
    service = KnowledgeService(
        session_factory=factory, embedding_service=stub, config=config
    )
    try:
        yield service, stub, factory, config
    finally:
        await engine.dispose()


async def test_auto_link_creates_edges_for_similar_knowledge(
    service_and_factory,
) -> None:
    """Creating two near-identical entries attaches an auto-linked
    ``related`` edge from the newer row to the older one, weighted by cosine
    similarity."""
    service, _stub, factory, _cfg = service_and_factory

    first = await service.create_knowledge(
        title="A",
        summary="first",
        content="first content body long enough for write gate >50",
    )
    second = await service.create_knowledge(
        title="A2",
        summary="second",
        content="second content body long enough for write gate >50",
    )

    edges = await _count_related_edges(factory, second["id"])
    assert len(edges) >= 1, f"expected ≥1 auto-linked edge, got {edges}"
    target_ids = {e[0] for e in edges}
    assert first["id"] in target_ids, "auto-link should point at the similar row"

    # Weight should match cosine similarity (~0.99), well above threshold.
    weight_for_first = next(w for t, w, _ in edges if t == first["id"])
    assert 0.9 <= weight_for_first <= 1.0, (
        f"edge weight should be ~cosine=0.99, got {weight_for_first}"
    )




async def test_auto_link_skips_without_embedding(tmp_path: Path) -> None:
    """No EmbeddingService → auto-link is a silent no-op, not an error."""
    db_path = tmp_path / "mnemo.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Explicitly no embedding_service → FTS-only deployment.
    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    service = KnowledgeService(
        session_factory=factory, embedding_service=None, config=config
    )

    try:
        first = await service.create_knowledge(
            title="A",
            summary="first",
            content="first content body long enough for write gate >50",
        )
        second = await service.create_knowledge(
            title="A2",
            summary="second",
            content="second content body long enough for write gate >50",
        )
    finally:
        pass

    try:
        edges = await _count_related_edges(factory, second["id"])
        assert edges == [], (
            f"no embedding → no auto-link; got {edges}"
        )
        # Row still exists — create_knowledge must succeed without embedding.
        assert "id" in first and "id" in second
    finally:
        await engine.dispose()


async def test_auto_link_respects_threshold(service_and_factory) -> None:
    """Neighbors below ``auto_link_threshold`` get no edge."""
    service, _stub, factory, cfg = service_and_factory
    # Push threshold above cos(A, A2) ≈ 0.99 so even that pair drops.
    cfg.auto_link_threshold = 0.995

    await service.create_knowledge(
        title="A",
        summary="first",
        content="first content body long enough for write gate >50",
    )
    second = await service.create_knowledge(
        title="A2",
        summary="second",
        content="second content body long enough for write gate >50",
    )

    edges = await _count_related_edges(factory, second["id"])
    assert edges == [], (
        f"cos=0.99 is below threshold=0.995, no edge expected; got {edges}"
    )
