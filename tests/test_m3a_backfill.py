"""End-to-end test for scripts/m3a_backfill_relation_types.py against a
real temp-dir SQLite DB.

We avoid mocking per project rule (no DB mocks). A tiny fixture set exercises:
  - supersedes preservation
  - keyword rule hit
  - tag rule hit
  - claim_type fallback
  - dry-run leaves DB untouched
  - idempotent second run produces no further changes
  - all relations land in the 7-value whitelist (0 dangling)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from mnemo.config import MnemoConfig
from mnemo.db import get_engine, get_session_factory, init_db, reset_engine
from mnemo.models.knowledge import Knowledge, Relation
from mnemo.relation_types import (
    ALTERNATIVE_TO,
    CONTRADICTS,
    DEPENDS_ON,
    DERIVED_FROM,
    EXAMPLE_OF,
    REFINES,
    SUPERSEDES,
    VALID_RELATION_TYPES,
)
from scripts.m3a_backfill_relation_types import backfill


@pytest_asyncio.fixture
async def seeded_db(tmp_path: Path):
    """Build a fresh mnemo DB under tmp_path and seed test rows."""
    await reset_engine()
    config = MnemoConfig(data_dir=str(tmp_path))
    get_engine(config)
    factory = get_session_factory(config)
    await init_db(config)

    async with factory() as session:
        rows = [
            # 1: fact (plain) — fallback REFINES
            Knowledge(
                title="事实 A",
                summary="一些陈述",
                content="这是一条普通事实",
                tags=json.dumps([]),
                scope="global",
                claim_type="fact",
            ),
            # 2: fact — target of various edges
            Knowledge(
                title="事实 B",
                summary="目标事实",
                content="目标",
                tags=json.dumps([]),
                scope="global",
                claim_type="fact",
            ),
            # 3: decision with keyword 依赖 — tgt "事实 B" named in the span
            Knowledge(
                title="决策 C",
                summary="选 X 因为依赖 事实 B",
                content="该决策必须先依赖 事实 B 才能生效",
                tags=json.dumps(["architecture"]),
                scope="global",
                claim_type="decision",
            ),
            # 4: decision tagged rejected-option — tgt must appear in the
            # rejection window for alternative_to to fire.
            Knowledge(
                title="决策 D 被弃",
                summary="方案 D",
                content="放弃方案：决策 C 成本过高，最终未采纳",
                tags=json.dumps(["rejected-option"]),
                scope="global",
                claim_type="decision",
            ),
            # 5: fact tagged fact-correction — tgt must appear in the
            # correction window for contradicts to fire.
            Knowledge(
                title="更正 E",
                summary="事实更正",
                content="事实更正：事实 A 之前的说法是错的，现在更正",
                tags=json.dumps(["fact-correction"]),
                scope="global",
                claim_type="fact",
            ),
            # 6: procedure — tgt "事实 B" named in the example span
            Knowledge(
                title="流程 F",
                summary="流程步骤",
                content="例如 事实 B 说明的情况",
                tags=json.dumps([]),
                scope="global",
                claim_type="procedure",
            ),
            # 7: superseded old version
            Knowledge(
                title="旧版 G",
                summary="旧版",
                content="旧版内容",
                tags=json.dumps([]),
                scope="global",
                claim_type="decision",
                status="superseded",
            ),
            # 8: new version
            Knowledge(
                title="新版 G",
                summary="新版",
                content="新版内容",
                tags=json.dumps([]),
                scope="global",
                claim_type="decision",
            ),
        ]
        session.add_all(rows)
        await session.commit()
        for r in rows:
            await session.refresh(r)
        ids = {r.title: r.id for r in rows}

        relations = [
            # Rule 3 fallback fact->fact -> REFINES
            Relation(source_id=ids["事实 A"], target_id=ids["事实 B"], relation_type="wikilink"),
            # Rule 2 keyword depends_on (src content 含 "依赖"/"必须先")
            Relation(source_id=ids["决策 C"], target_id=ids["事实 B"], relation_type="related"),
            # Rule 1 tag rejected-option -> ALTERNATIVE_TO
            Relation(source_id=ids["决策 D 被弃"], target_id=ids["决策 C"], relation_type="wikilink"),
            # Rule 1 tag fact-correction -> CONTRADICTS
            Relation(source_id=ids["更正 E"], target_id=ids["事实 A"], relation_type="related"),
            # Rule 2 keyword example_of (src content 含 "例如")
            Relation(source_id=ids["流程 F"], target_id=ids["事实 B"], relation_type="wikilink"),
            # Rule 0 supersedes preserved
            Relation(source_id=ids["新版 G"], target_id=ids["旧版 G"], relation_type="supersedes"),
        ]
        session.add_all(relations)
        await session.commit()

    yield tmp_path, ids

    await reset_engine()


@pytest.mark.asyncio
async def test_backfill_writes_valid_types(seeded_db):
    tmp_path, ids = seeded_db

    code, report = await backfill(data_dir=tmp_path, dry_run=False, sample_n=5)
    assert code == 0, report

    # Reopen engine after backfill reset — rebuild factory
    await reset_engine()
    config = MnemoConfig(data_dir=str(tmp_path))
    get_engine(config)
    factory = get_session_factory(config)

    async with factory() as s:
        rows = (await s.execute(select(Relation))).scalars().all()
        assert rows, "expected relations seeded"
        types_seen = {r.relation_type for r in rows}
        assert types_seen.issubset(VALID_RELATION_TYPES), (
            f"dangling types: {types_seen - VALID_RELATION_TYPES}"
        )

        # Check specific edges
        by_pair = {(r.source_id, r.target_id): r.relation_type for r in rows}

        assert by_pair[(ids["事实 A"], ids["事实 B"])] == REFINES
        assert by_pair[(ids["决策 C"], ids["事实 B"])] == DEPENDS_ON
        assert by_pair[(ids["决策 D 被弃"], ids["决策 C"])] == ALTERNATIVE_TO
        assert by_pair[(ids["更正 E"], ids["事实 A"])] == CONTRADICTS
        assert by_pair[(ids["流程 F"], ids["事实 B"])] == EXAMPLE_OF
        assert by_pair[(ids["新版 G"], ids["旧版 G"])] == SUPERSEDES


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(seeded_db):
    tmp_path, _ = seeded_db

    async def snapshot():
        await reset_engine()
        config = MnemoConfig(data_dir=str(tmp_path))
        get_engine(config)
        factory = get_session_factory(config)
        async with factory() as s:
            rows = (await s.execute(select(Relation))).scalars().all()
            return {(r.source_id, r.target_id): r.relation_type for r in rows}

    before = await snapshot()

    code, _ = await backfill(data_dir=tmp_path, dry_run=True, sample_n=5)
    assert code == 0

    after = await snapshot()
    assert before == after, "dry-run must not alter the DB"


@pytest.mark.asyncio
async def test_idempotent(seeded_db):
    tmp_path, _ = seeded_db

    code, _ = await backfill(data_dir=tmp_path, dry_run=False, sample_n=5)
    assert code == 0

    # Second run: nothing should change.
    await reset_engine()
    config = MnemoConfig(data_dir=str(tmp_path))
    get_engine(config)
    factory = get_session_factory(config)
    async with factory() as s:
        before = {
            (r.source_id, r.target_id): r.relation_type
            for r in (await s.execute(select(Relation))).scalars().all()
        }

    code, _ = await backfill(data_dir=tmp_path, dry_run=False, sample_n=5)
    assert code == 0

    await reset_engine()
    config = MnemoConfig(data_dir=str(tmp_path))
    get_engine(config)
    factory = get_session_factory(config)
    async with factory() as s:
        after = {
            (r.source_id, r.target_id): r.relation_type
            for r in (await s.execute(select(Relation))).scalars().all()
        }

    assert before == after


@pytest.mark.asyncio
async def test_event_recorded(seeded_db):
    tmp_path, _ = seeded_db

    code, _ = await backfill(data_dir=tmp_path, dry_run=False, sample_n=5)
    assert code == 0

    await reset_engine()
    config = MnemoConfig(data_dir=str(tmp_path))
    get_engine(config)
    factory = get_session_factory(config)
    async with factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT event_type, payload_json FROM knowledge_event "
                    "WHERE event_type = 'm3a_backfill'"
                )
            )
        ).first()
        assert row is not None
        payload = json.loads(row[1])
        assert "before" in payload and "after" in payload
        assert payload["dry_run"] is False
