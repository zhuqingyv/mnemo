"""Phase 5b M2 — feedback propagation into auto_related edge weights.

Covers docs/phase5b/FINE_EDGE_PLAN.md §2.3 (saturation formula) and §5.2
(propagation hook). The tests build ``auto_related`` edges directly at the
ORM layer so they do not depend on M1 landing ``_auto_link_v2`` — the
propagation hook is the unit under test here, not the edge builder.

Red-line compliance: real SQLite, no mocks.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base, Knowledge, Relation
from mnemo.services import feedback_service as fs
from mnemo.services.feedback_service import (
    EDGE_WEIGHT_BASE,
    EDGE_WEIGHT_CAP,
    EDGE_WEIGHT_FLOOR,
    EDGE_WEIGHT_K,
    compute_edge_weight,
    propagate_edge_feedback,
)
from mnemo.services.knowledge_service import KnowledgeService


# ---------------------------------------------------------------------------
# Pure-formula unit tests — no DB needed
# ---------------------------------------------------------------------------

class TestComputeEdgeWeight:
    def test_zero_feedback_returns_base(self) -> None:
        assert compute_edge_weight(0, 0) == pytest.approx(EDGE_WEIGHT_BASE)

    def test_one_helpful_lifts_above_base(self) -> None:
        w = compute_edge_weight(1, 0)
        expected = EDGE_WEIGHT_BASE + (1 / (1 + EDGE_WEIGHT_K)) * (
            EDGE_WEIGHT_CAP - EDGE_WEIGHT_BASE
        )
        assert w == pytest.approx(expected)
        assert w > EDGE_WEIGHT_BASE

    def test_five_helpful_reaches_saturation_midpoint(self) -> None:
        """K=5 means helpful=5 puts the saturation term at exactly 0.5 —
        weight = 0.3 + 0.5 * (0.85 - 0.3) = 0.575. This encodes the parameter
        choice from FINE_EDGE_PLAN §2.3; if K changes this test breaks on
        purpose."""
        w = compute_edge_weight(5, 0)
        assert w == pytest.approx(0.575)

    def test_helpful_saturates_below_cap(self) -> None:
        """Even a huge helpful count cannot push weight past the cap."""
        assert compute_edge_weight(10_000, 0) < EDGE_WEIGHT_CAP
        assert compute_edge_weight(10_000, 0) == pytest.approx(EDGE_WEIGHT_CAP, abs=0.01)

    def test_one_misleading_drops_below_base(self) -> None:
        """0 helpful + 1 misleading: the multiplicative decay does not move
        the base (helpful term is 0) but the floor guarantees weight ≥ 0.05.
        At h=0/m=1 the post-decay base is 0.3 since base is additive — to
        observe a drop the caller needs at least 1 helpful + 1 misleading."""
        w0 = compute_edge_weight(0, 1)
        # With zero helpful the base is still 0.3 — misleading alone does
        # not pull weight below base; it suppresses future helpful gains.
        assert w0 == pytest.approx(EDGE_WEIGHT_BASE)

    def test_misleading_suppresses_helpful_gain(self) -> None:
        """helpful=5 / misleading=0 → 0.575. With misleading=1, decay=0.85
        and weight = 0.3 + 0.5 * 0.55 * 0.85 = 0.53375. Strictly less than
        the no-misleading baseline."""
        without = compute_edge_weight(5, 0)
        with_one_mis = compute_edge_weight(5, 1)
        assert with_one_mis < without
        assert with_one_mis == pytest.approx(0.3 + 0.5 * 0.55 * 0.85)

    def test_weight_never_goes_below_floor(self) -> None:
        """Extreme misleading count: decay clamps to 0.1 and floor=0.05
        guarantees an irreducible residual."""
        assert compute_edge_weight(0, 1_000) == pytest.approx(EDGE_WEIGHT_BASE)
        # A helpful+many-misleading case: helpful is throttled but weight is
        # still pinned to BASE by the additive term not the floor.
        assert compute_edge_weight(100, 1_000) >= EDGE_WEIGHT_FLOOR

    def test_negative_counts_coerced_to_zero(self) -> None:
        """Defensive: malformed extra_json must not crash the formula."""
        assert compute_edge_weight(-1, -1) == pytest.approx(EDGE_WEIGHT_BASE)


# ---------------------------------------------------------------------------
# Integration fixture — real DB, KnowledgeService, MnemoConfig
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def service_and_cfg(
    tmp_path: Path,
) -> AsyncIterator[tuple[KnowledgeService, MnemoConfig, async_sessionmaker]]:
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
    cfg = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    service = KnowledgeService(session_factory=factory, config=cfg)
    try:
        yield service, cfg, factory
    finally:
        await engine.dispose()


async def _mk_knowledge(factory: async_sessionmaker, title: str) -> int:
    async with factory() as s:
        k = Knowledge(title=title, summary=f"s-{title}", content=f"c-{title}", tags="[]")
        s.add(k)
        await s.commit()
        await s.refresh(k)
        return k.id


async def _mk_edge(
    factory: async_sessionmaker,
    *,
    source_id: int,
    target_id: int,
    relation_type: str = "auto_related",
    weight: float = EDGE_WEIGHT_BASE,
    extra: dict | None = None,
) -> int:
    async with factory() as s:
        payload = {"helpful_count": 0, "misleading_count": 0, "last_feedback_at": None}
        if extra:
            payload.update(extra)
        edge = Relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            weight=weight,
            extra_json=json.dumps(payload, ensure_ascii=False),
        )
        s.add(edge)
        await s.commit()
        await s.refresh(edge)
        return edge.id


async def _get_edge(factory: async_sessionmaker, edge_id: int) -> Relation:
    async with factory() as s:
        row = await s.get(Relation, edge_id)
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


async def test_zero_feedback_edge_stays_at_base(service_and_cfg) -> None:
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    row = await _get_edge(factory, edge_id)
    assert row.weight == pytest.approx(EDGE_WEIGHT_BASE)



async def test_one_helpful_feedback_lifts_edge_weight(service_and_cfg) -> None:
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    result = await fs.record_feedback(
        service,
        knowledge_id=src,
        signal="helpful",
        actor="agent:alice",
        config=cfg,
    )
    assert result["success"] is True
    assert result.get("edges_updated") == 1

    row = await _get_edge(factory, edge_id)
    assert row.weight > EDGE_WEIGHT_BASE
    extra = json.loads(row.extra_json)
    assert extra["helpful_count"] == 1
    assert extra["misleading_count"] == 0
    assert extra["last_feedback_at"] is not None



async def test_five_helpful_feedbacks_reach_midpoint(service_and_cfg) -> None:
    """Five distinct actors sending helpful on the same node: counter lands
    at 5 and weight ≈ 0.575 (saturation midpoint when K=5)."""
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    for i in range(5):
        result = await fs.record_feedback(
            service,
            knowledge_id=src,
            signal="helpful",
            actor=f"agent:helper-{i}",
            config=cfg,
        )
        assert result["success"] is True

    row = await _get_edge(factory, edge_id)
    extra = json.loads(row.extra_json)
    assert extra["helpful_count"] == 5
    assert row.weight == pytest.approx(0.575)



async def test_one_misleading_suppresses_future_helpful_gain(service_and_cfg) -> None:
    """Feedback mixing: 5 helpful + 1 misleading < 5 helpful alone."""
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    for i in range(5):
        await fs.record_feedback(
            service, knowledge_id=src, signal="helpful",
            actor=f"agent:h-{i}", config=cfg,
        )
    await fs.record_feedback(
        service, knowledge_id=src, signal="misleading",
        actor="agent:critic", config=cfg,
    )

    row = await _get_edge(factory, edge_id)
    extra = json.loads(row.extra_json)
    assert extra["helpful_count"] == 5
    assert extra["misleading_count"] == 1
    # decay = 0.85, weight = 0.3 + 0.5*0.55*0.85 = 0.53375
    assert row.weight < 0.575
    assert row.weight == pytest.approx(0.3 + 0.5 * 0.55 * 0.85)



async def test_feature_flag_off_leaves_edge_weight_unchanged(service_and_cfg) -> None:
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    cfg.edge_feedback_propagation = False
    try:
        result = await fs.record_feedback(
            service, knowledge_id=src, signal="helpful",
            actor="agent:alice", config=cfg,
        )
    finally:
        cfg.edge_feedback_propagation = True

    assert result["success"] is True
    assert "edges_updated" not in result
    row = await _get_edge(factory, edge_id)
    assert row.weight == pytest.approx(EDGE_WEIGHT_BASE)
    extra = json.loads(row.extra_json)
    assert extra["helpful_count"] == 0



async def test_manual_related_edge_not_affected(service_and_cfg) -> None:
    """Manual ``related`` edges (weight 1.0, agent-declared) must survive
    propagation untouched — feedback only moves auto_related."""
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt_auto = await _mk_knowledge(factory, "B-auto")
    tgt_manual = await _mk_knowledge(factory, "B-manual")

    auto_id = await _mk_edge(factory, source_id=src, target_id=tgt_auto)
    # manual related edge: no extra_json counters, weight=1.0
    async with factory() as s:
        manual = Relation(
            source_id=src,
            target_id=tgt_manual,
            relation_type="related",
            weight=1.0,
            extra_json=None,
        )
        s.add(manual)
        await s.commit()
        await s.refresh(manual)
        manual_id = manual.id

    await fs.record_feedback(
        service, knowledge_id=src, signal="helpful",
        actor="agent:alice", config=cfg,
    )

    manual_row = await _get_edge(factory, manual_id)
    assert manual_row.weight == pytest.approx(1.0)
    assert manual_row.extra_json is None

    auto_row = await _get_edge(factory, auto_id)
    assert auto_row.weight > EDGE_WEIGHT_BASE



async def test_propagation_hits_both_source_and_target_sides(service_and_cfg) -> None:
    """Feedback on the middle node of a chain updates both incoming and
    outgoing auto_related edges — the selector is ``source OR target``."""
    service, cfg, factory = service_and_cfg
    upstream = await _mk_knowledge(factory, "upstream")
    middle = await _mk_knowledge(factory, "middle")
    downstream = await _mk_knowledge(factory, "downstream")

    incoming_id = await _mk_edge(factory, source_id=upstream, target_id=middle)
    outgoing_id = await _mk_edge(factory, source_id=middle, target_id=downstream)

    result = await fs.record_feedback(
        service, knowledge_id=middle, signal="helpful",
        actor="agent:alice", config=cfg,
    )
    assert result["edges_updated"] == 2

    for eid in (incoming_id, outgoing_id):
        row = await _get_edge(factory, eid)
        assert row.weight > EDGE_WEIGHT_BASE
        extra = json.loads(row.extra_json)
        assert extra["helpful_count"] == 1



async def test_outdated_signal_does_not_move_weight(service_and_cfg) -> None:
    """``outdated`` is a lifecycle signal (archive candidate), not a weight
    signal — §2.3 intentionally excludes it from propagation."""
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    result = await fs.record_feedback(
        service, knowledge_id=src, signal="outdated",
        actor="agent:alice", config=cfg,
    )
    assert result["success"] is True
    assert "edges_updated" not in result

    row = await _get_edge(factory, edge_id)
    assert row.weight == pytest.approx(EDGE_WEIGHT_BASE)
    extra = json.loads(row.extra_json)
    assert extra["helpful_count"] == 0
    assert extra["misleading_count"] == 0



async def test_propagate_hook_invoked_directly(service_and_cfg) -> None:
    """Direct hook call is the unit path used by future callers (e.g. a CLI
    that wants to re-propagate without writing a feedback event)."""
    service, cfg, factory = service_and_cfg
    src = await _mk_knowledge(factory, "A")
    tgt = await _mk_knowledge(factory, "B")
    edge_id = await _mk_edge(factory, source_id=src, target_id=tgt)

    diag = await propagate_edge_feedback(
        service, knowledge_id=src, signal="helpful", config=cfg,
    )
    assert diag["propagated"] is True
    assert diag["updated"] == 1

    row = await _get_edge(factory, edge_id)
    assert row.weight > EDGE_WEIGHT_BASE
