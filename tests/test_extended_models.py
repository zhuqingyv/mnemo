"""Tests for the frozen extended data model.

Covers:
- Auto-computed content_hash on create / update
- claim_type, status, version defaults + explicit set
- extra_json round-trip on Knowledge and Relation
- KnowledgeMeta CRUD
- KnowledgeEvent write + query (with nullable knowledge_id)
- KnowledgeVec BLOB round-trip
- Relation.weight default + explicit set
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mnemo.models.knowledge import (
    Base,
    Knowledge,
    KnowledgeEvent,
    KnowledgeMeta,
    KnowledgeVec,
    Relation,
)
from mnemo.repository import (
    knowledge_repository as kr,
    relation_repository as rr,
)
from mnemo.services.knowledge_service import KnowledgeService


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
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


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
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
# content_hash
# ---------------------------------------------------------------------------

async def test_create_auto_computes_content_hash(session: AsyncSession) -> None:
    content = "the quick brown fox"
    row = await kr.create(session, title="hash-create", summary="s", content=content)

    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert row.content_hash == expected
    assert len(row.content_hash) == 64


async def test_update_recomputes_content_hash_when_content_changes(
    session: AsyncSession,
) -> None:
    row = await kr.create(session, title="hash-update", summary="s", content="first")
    before = row.content_hash

    updated = await kr.update(session, row.id, content="second body")
    after = updated.content_hash

    assert before != after
    assert after == hashlib.sha256(b"second body").hexdigest()


async def test_update_without_content_preserves_hash(session: AsyncSession) -> None:
    row = await kr.create(session, title="hash-stable", summary="s", content="body")
    before = row.content_hash

    updated = await kr.update(session, row.id, summary="new summary")
    assert updated.content_hash == before


async def test_duplicate_content_produces_same_hash(session: AsyncSession) -> None:
    a = await kr.create(session, title="dup-a", summary="s", content="same body")
    b = await kr.create(session, title="dup-b", summary="s", content="same body")
    assert a.content_hash == b.content_hash


# ---------------------------------------------------------------------------
# claim_type / status / version / extra_json
# ---------------------------------------------------------------------------

async def test_status_defaults_active_and_version_defaults_one(
    session: AsyncSession,
) -> None:
    row = await kr.create(session, title="defaults", summary="s", content="c")
    assert row.status == "active"
    assert row.version == 1
    assert row.claim_type is None
    assert row.extra_json is None


async def test_claim_type_round_trip(session: AsyncSession) -> None:
    row = await kr.create(
        session,
        title="typed",
        summary="s",
        content="c",
        claim_type="decision",
    )
    assert row.claim_type == "decision"

    fetched = await kr.get_by_id(session, row.id)
    assert fetched is not None
    assert fetched.claim_type == "decision"


async def test_update_new_fields(session: AsyncSession) -> None:
    row = await kr.create(session, title="mutate", summary="s", content="c")

    updated = await kr.update(
        session,
        row.id,
        claim_type="fact",
        status="superseded",
        extra_json='{"confidence":0.9}',
    )
    assert updated.claim_type == "fact"
    assert updated.status == "superseded"
    assert updated.extra_json == '{"confidence":0.9}'


async def test_extra_json_accepts_null_by_default(session: AsyncSession) -> None:
    row = await kr.create(
        session,
        title="with-extra",
        summary="s",
        content="c",
        extra_json='{"tag_source":"imported"}',
    )
    assert row.extra_json == '{"tag_source":"imported"}'


# ---------------------------------------------------------------------------
# service layer exposes new fields
# ---------------------------------------------------------------------------

async def test_service_create_exposes_new_fields(service: KnowledgeService) -> None:
    item = await service.create_knowledge(
        title="svc-new",
        summary="s",
        content="body",
        claim_type="hypothesis",
    )
    assert item["claim_type"] == "hypothesis"
    assert item["status"] == "active"
    assert item["version"] == 1
    assert item["content_hash"] == hashlib.sha256(b"body").hexdigest()
    assert item["extra_json"] is None


async def test_service_summary_dict_exposes_claim_type_and_status(
    service: KnowledgeService,
) -> None:
    await service.create_knowledge(
        title="listed", summary="s", content="unique-term-xyz", claim_type="fact"
    )
    hits = await service.search("unique-term-xyz")
    assert len(hits) == 1
    assert hits[0]["claim_type"] == "fact"
    assert hits[0]["status"] == "active"
    assert hits[0]["version"] == 1


# ---------------------------------------------------------------------------
# Relation.weight and extra_json
# ---------------------------------------------------------------------------

async def test_relation_weight_default_and_explicit(session: AsyncSession) -> None:
    a = await kr.create(session, title="rel-a", summary="s", content="c")
    b = await kr.create(session, title="rel-b", summary="s", content="c")
    rel = await rr.create(session, source_id=a.id, target_id=b.id)

    assert rel.weight == 1.0
    assert rel.extra_json is None

    rel.weight = 0.25
    rel.extra_json = '{"source":"manual"}'
    await session.commit()
    await session.refresh(rel)

    assert rel.weight == 0.25
    assert rel.extra_json == '{"source":"manual"}'


# ---------------------------------------------------------------------------
# KnowledgeMeta CRUD
# ---------------------------------------------------------------------------

async def test_knowledge_meta_crud(session: AsyncSession) -> None:
    parent = await kr.create(session, title="meta-parent", summary="s", content="c")

    m1 = KnowledgeMeta(knowledge_id=parent.id, key="confidence", value="0.85")
    m2 = KnowledgeMeta(knowledge_id=parent.id, key="author", value="zq")
    session.add_all([m1, m2])
    await session.commit()

    stmt = (
        select(KnowledgeMeta)
        .where(KnowledgeMeta.knowledge_id == parent.id)
        .order_by(KnowledgeMeta.key)
    )
    rows = (await session.execute(stmt)).scalars().all()
    assert [(r.key, r.value) for r in rows] == [
        ("author", "zq"),
        ("confidence", "0.85"),
    ]

    # Delete the "confidence" entry, leaving "author" behind.
    await session.delete(m1)
    await session.commit()
    remaining = (await session.execute(stmt)).scalars().all()
    assert [r.key for r in remaining] == ["author"]


async def test_knowledge_meta_cascades_on_knowledge_delete(
    session: AsyncSession,
) -> None:
    parent = await kr.create(session, title="meta-cascade", summary="s", content="c")
    session.add(KnowledgeMeta(knowledge_id=parent.id, key="k", value="v"))
    await session.commit()

    await kr.delete(session, parent.id)

    rows = (
        await session.execute(
            select(KnowledgeMeta).where(KnowledgeMeta.knowledge_id == parent.id)
        )
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# KnowledgeEvent
# ---------------------------------------------------------------------------

async def test_knowledge_event_write_and_query(session: AsyncSession) -> None:
    parent = await kr.create(session, title="evt-parent", summary="s", content="c")

    session.add_all(
        [
            KnowledgeEvent(
                knowledge_id=parent.id,
                event_type="created",
                actor="agent-42",
                payload_json='{"via":"cli"}',
            ),
            KnowledgeEvent(
                knowledge_id=parent.id,
                event_type="read",
                actor="agent-7",
            ),
            KnowledgeEvent(
                knowledge_id=parent.id,
                event_type="marked_helpful",
                actor="user-1",
            ),
        ]
    )
    await session.commit()

    rows = (
        await session.execute(
            select(KnowledgeEvent)
            .where(KnowledgeEvent.knowledge_id == parent.id)
            .order_by(KnowledgeEvent.id)
        )
    ).scalars().all()
    assert [r.event_type for r in rows] == ["created", "read", "marked_helpful"]
    assert rows[0].actor == "agent-42"
    assert rows[0].payload_json == '{"via":"cli"}'
    assert rows[1].payload_json is None


async def test_knowledge_event_allows_null_knowledge_id(
    session: AsyncSession,
) -> None:
    """Events not bound to a specific knowledge row are legal."""
    evt = KnowledgeEvent(
        knowledge_id=None,
        event_type="session_started",
        actor="agent-x",
    )
    session.add(evt)
    await session.commit()
    await session.refresh(evt)

    assert evt.id is not None
    assert evt.knowledge_id is None


async def test_knowledge_event_cascades_on_knowledge_delete(
    session: AsyncSession,
) -> None:
    parent = await kr.create(session, title="evt-cascade", summary="s", content="c")
    session.add(
        KnowledgeEvent(knowledge_id=parent.id, event_type="created", actor="x")
    )
    await session.commit()

    await kr.delete(session, parent.id)

    rows = (
        await session.execute(
            select(KnowledgeEvent).where(KnowledgeEvent.knowledge_id == parent.id)
        )
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# KnowledgeVec
# ---------------------------------------------------------------------------

async def test_knowledge_vec_blob_round_trip(session: AsyncSession) -> None:
    parent = await kr.create(session, title="vec-parent", summary="s", content="c")

    blob = bytes(range(256))  # every byte value
    vec = KnowledgeVec(
        knowledge_id=parent.id,
        model_name="test-embedding-v1",
        vector=blob,
    )
    session.add(vec)
    await session.commit()
    await session.refresh(vec)

    fetched = (
        await session.execute(
            select(KnowledgeVec).where(KnowledgeVec.knowledge_id == parent.id)
        )
    ).scalar_one()
    assert fetched.model_name == "test-embedding-v1"
    assert fetched.vector == blob
    assert len(fetched.vector) == 256


async def test_knowledge_vec_multiple_models_per_knowledge(
    session: AsyncSession,
) -> None:
    parent = await kr.create(session, title="vec-multi", summary="s", content="c")
    session.add_all(
        [
            KnowledgeVec(knowledge_id=parent.id, model_name="m1", vector=b"\x01\x02"),
            KnowledgeVec(knowledge_id=parent.id, model_name="m2", vector=b"\x03\x04"),
        ]
    )
    await session.commit()

    rows = (
        await session.execute(
            select(KnowledgeVec)
            .where(KnowledgeVec.knowledge_id == parent.id)
            .order_by(KnowledgeVec.model_name)
        )
    ).scalars().all()
    assert [r.model_name for r in rows] == ["m1", "m2"]


# ---------------------------------------------------------------------------
# Sanity: compute_content_hash helper is stable
# ---------------------------------------------------------------------------

def test_compute_content_hash_matches_sha256() -> None:
    assert kr.compute_content_hash("") == hashlib.sha256(b"").hexdigest()
    assert (
        kr.compute_content_hash("中文 content")
        == hashlib.sha256("中文 content".encode("utf-8")).hexdigest()
    )


# ---------------------------------------------------------------------------
# Sanity: frozen schema model signatures
# ---------------------------------------------------------------------------

def test_knowledge_has_all_new_columns() -> None:
    cols = {c.name for c in Knowledge.__table__.columns}
    for required in ("claim_type", "status", "content_hash", "version", "extra_json"):
        assert required in cols, f"missing column {required!r}"


def test_relation_has_weight_and_extra_json() -> None:
    cols = {c.name for c in Relation.__table__.columns}
    assert "weight" in cols
    assert "extra_json" in cols
