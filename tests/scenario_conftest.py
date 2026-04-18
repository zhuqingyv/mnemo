"""Shared fixtures for scenario tests (accuracy / relevance / intelligence).

This file is intentionally NOT a pytest ``conftest.py`` — it is imported
explicitly by ``test_accuracy.py`` / ``test_relevance.py`` /
``test_intelligence.py`` so the scenario suite does not disturb the 138
existing unit tests.

Responsibilities:
- Build an isolated async engine + session_factory bound to a tmp dir,
  without touching the ``mnemo.db`` module-level singletons.
- Create ORM tables and the ``knowledge_fts`` virtual table.
- Load every fixture under ``tests/fixtures/knowledge/*.json`` via
  ``KnowledgeService`` in two passes so forward wikilink/related targets
  resolve (same approach as ``tests/fixtures/load_fixtures.py``).
- Load every ``tests/fixtures/scenarios/*.json`` into a dict keyed by
  category name (filename with ``_scenarios.json`` stripped).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlite_vec
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Base, Knowledge, Relation
from mnemo.relation_types import VALID_RELATION_TYPES, ClassifyInput, classify
from mnemo.repository import authority_repository as ar
from mnemo.repository import knowledge_repository as kr
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services.knowledge_service import (
    MANUAL_RELATION_TYPE,
    WIKILINK_RELATION_TYPE,
    KnowledgeService,
)
from sqlalchemy import select, update


# When the env var ``MNEMO_HYBRID=1`` is set, the scenario fixtures build an
# engine with sqlite-vec loaded and inject a real EmbeddingService — the
# KnowledgeService.search call then goes through the hybrid RRF path. Without
# the var we preserve the original FTS-only behavior to keep the 138 existing
# unit tests fast and Ollama-free.
_HYBRID_MODE = os.environ.get("MNEMO_HYBRID") == "1"


def _load_sqlite_vec(dbapi_conn, _cr) -> None:
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


REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "tests" / "fixtures" / "knowledge"
SCENARIOS_DIR = REPO_ROOT / "tests" / "fixtures" / "scenarios"


def _load_knowledge_entries() -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict):
                entries.append((path.name, item))
    return entries


async def _init_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)
                """
            )
        )
        if _HYBRID_MODE:
            await conn.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec_idx "
                    f"USING vec0(knowledge_id INTEGER PRIMARY KEY, "
                    f"embedding FLOAT[{VECTOR_DIM}])"
                )
            )


async def _insert_all(
    service: KnowledgeService,
    entries: list[tuple[str, dict[str, Any]]],
) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for _filename, item in entries:
        title = item.get("title")
        if not title:
            skipped += 1
            continue
        try:
            await service.create_knowledge(
                title=title,
                summary=item.get("summary") or "",
                content=item.get("content") or "",
                tags=item.get("tags") or None,
                scope=item.get("scope") or "global",
                project_name=item.get("project_name"),
                source=item.get("source"),
                claim_type=item.get("claim_type"),
                related_titles=item.get("related") or None,
            )
            inserted += 1
        except Exception:  # noqa: BLE001 — one bad row shouldn't abort the load
            skipped += 1
    return inserted, skipped


async def _reapply_relations(
    service: KnowledgeService,
    entries: list[tuple[str, dict[str, Any]]],
) -> int:
    factory = service._session_factory  # noqa: SLF001
    refreshed = 0
    async with factory() as session:
        for _filename, item in entries:
            title = item.get("title")
            if not title:
                continue
            row = await kr.get_by_title(session, title)
            if row is None:
                continue
            await session.execute(
                text(
                    "DELETE FROM relation "
                    "WHERE source_id = :sid AND relation_type IN (:t1, :t2)"
                ),
                {
                    "sid": row.id,
                    "t1": WIKILINK_RELATION_TYPE,
                    "t2": MANUAL_RELATION_TYPE,
                },
            )
            await session.commit()
            await service._apply_wikilinks(session, row.id, row.content)  # noqa: SLF001
            await service._apply_manual_relations(  # noqa: SLF001
                session, row.id, item.get("related") or None
            )
            refreshed += 1
    return refreshed


def _classify_input(k: Knowledge) -> ClassifyInput:
    raw = k.tags
    tags: tuple[str, ...] = ()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                tags = tuple(str(t) for t in parsed)
        except json.JSONDecodeError:
            pass
    return ClassifyInput(
        title=k.title,
        summary=k.summary or "",
        content=k.content or "",
        claim_type=k.claim_type,
        tags=tags,
    )


async def _apply_m3_backfill(service: KnowledgeService) -> tuple[int, int]:
    """Run M3a classify() reclassification + M3b authority rebuild.

    Mirrors ``scripts/m3b_backfill_on_gate_db.py`` but operates on the
    scenario-built session factory. Without this pass, scenario DBs have only
    the legacy ``wikilink`` / ``related`` edges and authority is zero
    everywhere, which collapses every pure-vector query below the M3b gate.
    """
    factory = service._session_factory  # noqa: SLF001
    async with factory() as session:
        k_rows = (await session.execute(select(Knowledge))).scalars().all()
        k_by_id = {k.id: k for k in k_rows}
        relations = (await session.execute(select(Relation))).scalars().all()

        reclassified = 0
        for rel in relations:
            src = k_by_id.get(rel.source_id)
            tgt = k_by_id.get(rel.target_id)
            if src is None or tgt is None:
                continue
            new_type = classify(
                src=_classify_input(src),
                tgt=_classify_input(tgt),
                current_type=rel.relation_type,
            )
            if new_type not in VALID_RELATION_TYPES:
                continue
            if new_type != rel.relation_type:
                await session.execute(
                    update(Relation)
                    .where(Relation.id == rel.id)
                    .values(relation_type=new_type)
                )
                reclassified += 1
        await session.commit()

        nonzero = 0
        for kid in [k.id for k in k_rows]:
            score = await ar.recompute_and_store_authority(session, kid)
            if score > 0:
                nonzero += 1
        await session.commit()

        return reclassified, nonzero


async def _build_service(db_path: Path) -> tuple[KnowledgeService, Any, dict[str, int]]:
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)
    if _HYBRID_MODE:
        event.listen(engine.sync_engine, "connect", _load_sqlite_vec)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _init_schema(engine)

    embedding: EmbeddingService | None = None
    config: MnemoConfig | None = None
    if _HYBRID_MODE:
        config = MnemoConfig()
        embedding = EmbeddingService(config=config)
        ok = await embedding.warmup()
        if not ok:
            raise RuntimeError(
                "MNEMO_HYBRID=1 but Ollama warmup failed — cannot run hybrid gate"
            )

    service = KnowledgeService(
        session_factory=session_factory,
        config=config,
        embedding_service=embedding,
    )

    entries = _load_knowledge_entries()
    inserted, skipped = await _insert_all(service, entries)
    refreshed = await _reapply_relations(service, entries)
    reclassified, authority_nonzero = await _apply_m3_backfill(service)

    async with session_factory() as session:
        active = int(
            (
                await session.execute(
                    text("SELECT COUNT(*) FROM knowledge WHERE status = 'active'")
                )
            ).scalar_one()
        )
        total = int(
            (
                await session.execute(text("SELECT COUNT(*) FROM knowledge"))
            ).scalar_one()
        )
        relations = int(
            (
                await session.execute(text("SELECT COUNT(*) FROM relation"))
            ).scalar_one()
        )

    stats = {
        "entries": len(entries),
        "inserted": inserted,
        "skipped": skipped,
        "refreshed": refreshed,
        "reclassified": reclassified,
        "authority_nonzero": authority_nonzero,
        "active": active,
        "total_rows": total,
        "relations": relations,
    }
    return service, engine, stats


@pytest.fixture(scope="session")
def scenario_stats() -> dict[str, int]:
    """Populated by ``scenario_service`` — surfaces load counters to tests."""
    return {}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def scenario_service(tmp_path_factory, scenario_stats):
    """Session-level KnowledgeService with all 12 fixture categories loaded."""
    tmp_dir = tmp_path_factory.mktemp("mnemo-scenarios")
    db_path = tmp_dir / "mnemo.db"
    service, engine, stats = await _build_service(db_path)
    scenario_stats.update(stats)
    try:
        yield service
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def all_scenarios() -> dict[str, list[dict[str, Any]]]:
    """Map ``category -> scenarios`` for every ``*_scenarios.json`` file."""
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(SCENARIOS_DIR.glob("*_scenarios.json")):
        category = (
            path.stem[: -len("_scenarios")]
            if path.stem.endswith("_scenarios")
            else path.stem
        )
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            result[category] = [s for s in data if isinstance(s, dict)]
        else:
            result[category] = []
    return result
